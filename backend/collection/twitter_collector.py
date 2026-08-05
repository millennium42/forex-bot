"""Coletor de tweets pelo filtered stream da API v2 do X/Twitter.

Mesmo destino do news collector: tabela `documents` e fila Celery `collection`.
O que muda é a chave de dedupe — o id do tweet, não o hash da URL — e o
transporte: um stream NDJSON de longa duração em vez de um GET por ciclo.

O filtro roda no servidor: as cashtags configuradas viram regras do stream
(`sync_rules`), então o worker só recebe o que interessa. Igual às notícias,
tweet é entrada best-effort do sentimento: token ausente ou lista de cashtags
vazia derruba a coleta desta rodada, não o processo.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import structlog
from sqlalchemy.orm import Session

from backend.collection.documents import store_items
from backend.config import Settings, get_settings
from backend.models import Document, DocumentSource

logger = structlog.get_logger(__name__)

STREAM_URL = "https://api.twitter.com/2/tweets/search/stream"
RULES_URL = f"{STREAM_URL}/rules"
TWEET_URL_TEMPLATE = "https://twitter.com/i/web/status/{tweet_id}"

# Campos pedidos ao endpoint. `created_at` é a janela temporal do sentimento;
# sem ele o documento entraria com `published_at` nulo.
STREAM_PARAMS = {"tweet.fields": "created_at,author_id,lang"}

# Limite de caracteres de uma regra do filtered stream no tier básico.
MAX_RULE_LENGTH = 512
RULE_TAG = "twitter:cashtags"
DEFAULT_ORIGIN = RULE_TAG

# Retweet repete o texto do original e enviesaria o sentimento por contagem.
RULE_SUFFIX = " -is:retweet"

# Quantos tweets uma execução da task consome antes de fechar o stream. O
# stream é infinito por natureza; sem teto a task nunca comitaria nada.
DEFAULT_STREAM_LIMIT = 200

CONNECT_TIMEOUT_SECONDS = 10.0
# Read alto de propósito: o stream fica em silêncio entre tweets e manda
# keep-alive a cada ~20s. Timeout curto derrubaria conexão saudável.
READ_TIMEOUT_SECONDS = 60.0


class TwitterStreamError(RuntimeError):
    """Stream ou endpoint de regras inacessível, ou resposta HTTP de erro."""


def tweet_fingerprint(tweet_id: str) -> str:
    """sha256 hex do id do tweet, com namespace.

    O prefixo separa o espaço de hashes do usado pelas notícias: um id de tweet
    e uma URL nunca podem colidir na mesma coluna única.
    """
    return hashlib.sha256(f"twitter:{tweet_id.strip()}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class TweetItem:
    """Tweet já normalizado, ainda não persistido."""

    tweet_id: str
    text: str
    origin: str
    author_id: str | None
    published_at: datetime | None

    @property
    def dedupe_hash(self) -> str:
        return tweet_fingerprint(self.tweet_id)

    @property
    def url(self) -> str:
        return TWEET_URL_TEMPLATE.format(tweet_id=self.tweet_id)

    def to_document(self) -> Document:
        return Document(
            source=DocumentSource.TWITTER,
            dedupe_hash=self.dedupe_hash,
            url=self.url,
            title=None,  # tweet não tem título; inventar um seria ruído no NLP
            content=self.text,
            origin=self.origin[:255],
            published_at=self.published_at,
        )


def _render_rule(termos: Sequence[str]) -> str:
    return f"({' OR '.join(termos)}){RULE_SUFFIX}"


def build_rules(cashtags: Sequence[str]) -> list[str]:
    """Converte as cashtags configuradas em regras do filtered stream.

    Os termos são agrupados por OR até o limite de caracteres da regra; o que
    não couber vira a próxima regra. Um termo sozinho maior que o limite é
    mantido — quem valida o tamanho é a API, e recusar aqui esconderia o erro.
    """
    termos = [t.strip() for t in cashtags if t.strip()]
    if not termos:
        return []

    regras: list[str] = []
    atual: list[str] = []
    for termo in termos:
        if atual and len(_render_rule([*atual, termo])) > MAX_RULE_LENGTH:
            regras.append(_render_rule(atual))
            atual = [termo]
        else:
            atual.append(termo)
    regras.append(_render_rule(atual))
    return regras


def _rules_request(client: httpx.Client, method: str, **kwargs: Any) -> dict[str, Any]:
    try:
        response = client.request(method, RULES_URL, **kwargs)
        response.raise_for_status()
        payload: Any = response.json()
    except httpx.HTTPError as exc:
        raise TwitterStreamError(f"falha no endpoint de regras: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise TwitterStreamError(f"resposta de regras não é JSON: {exc}") from exc
    return payload if isinstance(payload, dict) else {}


def fetch_rules(client: httpx.Client) -> dict[str, str]:
    """Regras ativas na conta, como `{id: value}`."""
    payload = _rules_request(client, "GET")
    dados = payload.get("data") or []
    return {
        str(r["id"]): str(r.get("value", ""))
        for r in dados
        if isinstance(r, dict) and r.get("id") is not None
    }


def sync_rules(client: httpx.Client, rules: Sequence[str]) -> list[str]:
    """Deixa o stream com exatamente `rules` cadastradas. Devolve as regras ativas.

    As regras são globais por conta, não por conexão: sem essa sincronização, um
    filtro antigo continuaria injetando tweets fora do escopo atual.
    """
    atuais = fetch_rules(client)
    desejadas = list(rules)
    if set(atuais.values()) == set(desejadas):
        return desejadas

    if atuais:
        _rules_request(client, "POST", json={"delete": {"ids": list(atuais)}})
    if desejadas:
        _rules_request(
            client, "POST", json={"add": [{"value": r, "tag": RULE_TAG} for r in desejadas]}
        )
    logger.info("twitter.regras_sincronizadas", quantidade=len(desejadas))
    return desejadas


def _parse_published(valor: Any) -> datetime | None:
    if not isinstance(valor, str) or not valor.strip():
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_payload(payload: object, default_origin: str = DEFAULT_ORIGIN) -> TweetItem | None:
    """Converte um objeto do stream em `TweetItem`. Devolve `None` se inservível.

    Sem id não há chave de dedupe e sem texto não há o que analisar: nos dois
    casos o objeto é descartado, não persistido pela metade.
    """
    if not isinstance(payload, dict):
        return None
    dados = payload.get("data")
    if not isinstance(dados, dict):
        return None

    tweet_id = str(dados.get("id") or "").strip()
    texto = str(dados.get("text") or "").strip()
    if not tweet_id or not texto:
        return None

    # A tag da regra que casou vira a origem: permite desligar um filtro
    # ruidoso sem apagar o que ele já produziu.
    origem = default_origin
    regras = payload.get("matching_rules")
    if isinstance(regras, list) and regras and isinstance(regras[0], dict):
        origem = str(regras[0].get("tag") or default_origin)

    autor = dados.get("author_id")
    return TweetItem(
        tweet_id=tweet_id,
        text=texto,
        origin=origem,
        author_id=str(autor) if autor is not None else None,
        published_at=_parse_published(dados.get("created_at")),
    )


def iter_tweets(
    lines: Iterable[str],
    default_origin: str = DEFAULT_ORIGIN,
    limit: int | None = None,
) -> Iterator[TweetItem]:
    """Itera o NDJSON do stream.

    Linha em branco é keep-alive do servidor, não erro. Linha que não parseia é
    logada e pulada: um objeto malformado não pode encerrar a coleta inteira.
    """
    emitidos = 0
    for line in lines:
        if limit is not None and emitidos >= limit:
            return
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("twitter.linha_invalida")
            continue
        item = parse_payload(payload, default_origin=default_origin)
        if item is None:
            logger.debug("twitter.objeto_descartado")
            continue
        emitidos += 1
        yield item


def stream_tweets(
    client: httpx.Client,
    limit: int | None = DEFAULT_STREAM_LIMIT,
    default_origin: str = DEFAULT_ORIGIN,
) -> list[TweetItem]:
    """Abre o stream e devolve até `limit` tweets, fechando a conexão em seguida."""
    try:
        with client.stream("GET", STREAM_URL, params=STREAM_PARAMS) as response:
            if response.status_code >= 400:
                response.read()
                raise TwitterStreamError(f"stream respondeu HTTP {response.status_code}")
            return list(iter_tweets(response.iter_lines(), default_origin, limit))
    except httpx.HTTPError as exc:
        raise TwitterStreamError(f"falha no stream: {exc}") from exc


def build_client(bearer_token: str) -> httpx.Client:
    """Client autenticado. O token só existe no header — nunca em log."""
    return httpx.Client(
        headers={"Authorization": f"Bearer {bearer_token}"},
        timeout=httpx.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
    )


def collect_tweets(
    session: Session,
    client: httpx.Client | None = None,
    cashtags: Sequence[str] | None = None,
    limit: int | None = DEFAULT_STREAM_LIMIT,
    settings: Settings | None = None,
) -> int:
    """Sincroniza as regras, consome o stream e persiste. Devolve quantos são novos.

    Reexecutar é seguro: o dedupe por id do tweet vive no índice único de
    `documents.dedupe_hash`.
    """
    termos = list(cashtags) if cashtags is not None else None
    proprio: httpx.Client | None = None

    if termos is None or client is None:
        settings = settings or get_settings()
        termos = termos if termos is not None else settings.cashtag_list

    regras = build_rules(termos)
    if not regras:
        logger.warning("twitter.sem_cashtags")
        return 0

    if client is None:
        token = (settings.twitter_bearer_token if settings else None) or ""
        if not token:
            logger.warning("twitter.sem_token")
            return 0
        proprio = client = build_client(token)

    try:
        sync_rules(client, regras)
        items = stream_tweets(client, limit=limit)
    finally:
        if proprio is not None:
            proprio.close()

    inseridos = store_items(session, items)
    logger.info("twitter.stream_coletado", lidos=len(items), inseridos=inseridos)
    return inseridos
