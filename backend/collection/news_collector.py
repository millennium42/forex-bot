"""Coletor de notícias por RSS.

Dedupe por hash da URL normalizada. O invariante ("reprocessar a mesma feed não
duplica linha") é sustentado pelo índice único em `documents.dedupe_hash`; o
filtro em Python só evita o custo de tentar o insert.

Diferente do conector MT5, aqui a falha de uma fonte **não** derruba as outras:
notícia é entrada best-effort do sentimento, não a visão de mercado sobre a qual
uma ordem é dimensionada. O feed que falhou é logado e pulado.
"""

from __future__ import annotations

import hashlib
from calendar import timegm
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from time import struct_time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx
import structlog
from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.collection.documents import store_items
from backend.config import Settings, get_settings
from backend.models import Document, DocumentSource

__all__ = [
    "NewsFetchError",
    "NewsItem",
    "collect_feed",
    "collect_news",
    "fetch_feed",
    "normalize_url",
    "parse_feed",
    "store_items",
    "url_fingerprint",
]

logger = structlog.get_logger(__name__)

# Parâmetros de rastreamento não identificam o artigo: a mesma notícia chega com
# utm diferente por canal e viraria linha nova a cada canal.
TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "igshid")

FETCH_TIMEOUT_SECONDS = 15.0


class NewsFetchError(RuntimeError):
    """Feed inacessível ou resposta HTTP de erro."""


def normalize_url(url: str) -> str:
    """Forma canônica da URL para dedupe.

    Minúsculas em scheme/host, sem fragmento, sem parâmetro de rastreamento,
    query ordenada e barra final removida. Só isso: normalização agressiva
    (remover todos os parâmetros, por exemplo) colapsaria artigos distintos que
    se diferenciam justamente pela query.
    """
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(TRACKING_PARAMS)
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            urlencode(sorted(query)),
            "",  # fragmento descartado: não distingue documento
        )
    )


def url_fingerprint(url: str) -> str:
    """sha256 hex da URL normalizada."""
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class NewsItem:
    """Entrada de feed já normalizada, ainda não persistida."""

    url: str
    title: str
    content: str
    origin: str
    published_at: datetime | None

    @property
    def dedupe_hash(self) -> str:
        return url_fingerprint(self.url)

    def to_document(self) -> Document:
        return Document(
            source=DocumentSource.NEWS,
            dedupe_hash=self.dedupe_hash,
            url=self.url,
            title=self.title[:512] or None,
            content=self.content,
            origin=self.origin[:255],
            published_at=self.published_at,
        )


def _parse_published(entry: Any) -> datetime | None:
    """Converte o `struct_time` do feedparser em datetime UTC.

    O feedparser já entrega o horário em UTC; `timegm` (não `mktime`) é o que
    interpreta struct_time como UTC em vez de horário local.
    """
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not isinstance(parsed, struct_time):
        return None
    return datetime.fromtimestamp(timegm(parsed), tz=UTC)


def _entry_content(entry: Any) -> str:
    for attr in ("summary", "description"):
        value = getattr(entry, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def parse_feed(raw: bytes | str, origin: str) -> list[NewsItem]:
    """Transforma o XML do feed em itens. Entrada sem link é descartada.

    Sem link não há chave de dedupe: persistir tal entrada garantiria duplicata
    no próximo ciclo.
    """
    parsed = feedparser.parse(raw)
    items: list[NewsItem] = []
    for entry in parsed.entries:
        link = getattr(entry, "link", "")
        if not isinstance(link, str) or not link.strip():
            logger.warning("news.entrada_sem_link", origin=origin)
            continue
        items.append(
            NewsItem(
                url=link.strip(),
                title=str(getattr(entry, "title", "")).strip(),
                content=_entry_content(entry),
                origin=origin,
                published_at=_parse_published(entry),
            )
        )
    return items


@retry(
    retry=retry_if_exception_type(NewsFetchError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    reraise=True,
)
def fetch_feed(url: str, client: httpx.Client | None = None) -> bytes:
    """Baixa o feed. Erro de rede ou HTTP >= 400 vira `NewsFetchError`."""
    owned = client is None
    client = client or httpx.Client(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True)
    try:
        response = client.get(url)
        response.raise_for_status()
        return response.content
    except httpx.HTTPError as exc:
        raise NewsFetchError(f"falha ao buscar feed {url}: {exc}") from exc
    finally:
        if owned:
            client.close()


def collect_feed(session: Session, feed_url: str, client: httpx.Client | None = None) -> int:
    """Baixa, parseia e persiste um feed. Devolve quantos documentos são novos."""
    items = parse_feed(fetch_feed(feed_url, client=client), origin=feed_url)
    inseridos = store_items(session, items)
    logger.info("news.feed_coletado", feed=feed_url, lidos=len(items), inseridos=inseridos)
    return inseridos


def collect_news(
    session: Session,
    feeds: Sequence[str] | None = None,
    client: httpx.Client | None = None,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Coleta todos os feeds configurados. Feed que falha é logado e pulado.

    Retorna `{feed: novos}` apenas para os feeds que responderam.
    """
    # `get_settings()` só é tocado quando a lista não veio pronta: chamador que
    # já sabe os feeds não deve depender de config carregada.
    alvos = list(feeds) if feeds is not None else (settings or get_settings()).rss_feed_list

    resultado: dict[str, int] = {}
    for feed_url in alvos:
        try:
            resultado[feed_url] = collect_feed(session, feed_url, client=client)
        except NewsFetchError as exc:
            logger.warning("news.feed_indisponivel", feed=feed_url, erro=str(exc))
    return resultado
