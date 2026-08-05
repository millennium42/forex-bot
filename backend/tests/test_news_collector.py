"""História 4 — coleta de notícias com dedupe por hash de URL."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tenacity import wait_none

from backend.collection.news_collector import (
    NewsFetchError,
    NewsItem,
    collect_feed,
    collect_news,
    fetch_feed,
    normalize_url,
    parse_feed,
    store_items,
    url_fingerprint,
)
from backend.config import Settings
from backend.models import Document, DocumentSource

FEED_URL = "https://exemplo.test/rss"

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Macro Wire</title>
    <item>
      <title>ECB mantem juros</title>
      <link>https://exemplo.test/ecb-juros?utm_source=rss</link>
      <description>O BCE manteve a taxa em 4%.</description>
      <pubDate>Mon, 04 Aug 2026 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Dolar recua contra o euro</title>
      <link>https://exemplo.test/dolar-recua</link>
      <description>Movimento apos dado de emprego.</description>
      <pubDate>Mon, 04 Aug 2026 13:30:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

FEED_XML_SEM_LINK = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Macro Wire</title>
    <item>
      <title>Nota sem link</title>
      <description>Sem URL nao ha chave de dedupe.</description>
    </item>
  </channel>
</rss>
"""


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> Iterator[httpx.Client]:
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        yield client


@pytest.fixture
def feed_client() -> Iterator[httpx.Client]:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=FEED_XML.encode())

    yield from _client(handler)


def _conta_documentos(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Document)) or 0


# --- normalização e hash ---------------------------------------------------
def test_normalize_url_descarta_fragmento_e_tracking() -> None:
    assert (
        normalize_url("https://Exemplo.test/Noticia/?utm_source=rss&id=7#topo")
        == "https://exemplo.test/Noticia?id=7"
    )


def test_normalize_url_ordena_query() -> None:
    a = normalize_url("https://exemplo.test/x?b=2&a=1")
    b = normalize_url("https://exemplo.test/x?a=1&b=2")
    assert a == b


def test_hash_ignora_parametro_de_campanha() -> None:
    """Mesma notícia divulgada por dois canais não pode virar duas linhas."""
    assert url_fingerprint("https://exemplo.test/n1?utm_source=twitter") == url_fingerprint(
        "https://exemplo.test/n1?utm_source=newsletter"
    )


def test_hash_distingue_urls_diferentes() -> None:
    assert url_fingerprint("https://exemplo.test/a") != url_fingerprint("https://exemplo.test/b")


def test_hash_tem_64_hex() -> None:
    valor = url_fingerprint("https://exemplo.test/a")
    assert len(valor) == 64
    assert set(valor) <= set("0123456789abcdef")


# --- parse -----------------------------------------------------------------
def test_parse_feed_extrai_campos() -> None:
    items = parse_feed(FEED_XML, origin=FEED_URL)
    assert len(items) == 2
    primeiro = items[0]
    assert primeiro.title == "ECB mantem juros"
    assert primeiro.url == "https://exemplo.test/ecb-juros?utm_source=rss"
    assert primeiro.content == "O BCE manteve a taxa em 4%."
    assert primeiro.origin == FEED_URL


def test_parse_feed_converte_data_para_utc() -> None:
    items = parse_feed(FEED_XML, origin=FEED_URL)
    assert items[0].published_at == datetime(2026, 8, 4, 12, 0, tzinfo=UTC)


def test_parse_feed_descarta_entrada_sem_link() -> None:
    assert parse_feed(FEED_XML_SEM_LINK, origin=FEED_URL) == []


def test_parse_feed_aceita_bytes() -> None:
    assert len(parse_feed(FEED_XML.encode(), origin=FEED_URL)) == 2


def test_parse_feed_sem_data_deixa_published_at_nulo() -> None:
    xml = FEED_XML.replace("<pubDate>Mon, 04 Aug 2026 12:00:00 GMT</pubDate>", "")
    assert parse_feed(xml, origin=FEED_URL)[0].published_at is None


# --- persistência e dedupe -------------------------------------------------
def test_store_items_persiste_como_documento_de_news(session: Session) -> None:
    assert store_items(session, parse_feed(FEED_XML, origin=FEED_URL)) == 2
    session.commit()

    doc = session.scalars(select(Document).order_by(Document.id)).first()
    assert doc is not None
    assert doc.source is DocumentSource.NEWS
    assert doc.dedupe_hash == url_fingerprint("https://exemplo.test/ecb-juros?utm_source=rss")


def test_reprocessar_mesma_feed_nao_duplica(session: Session) -> None:
    """Critério de aceite da história 4."""
    items = parse_feed(FEED_XML, origin=FEED_URL)
    assert store_items(session, items) == 2
    session.commit()

    assert store_items(session, parse_feed(FEED_XML, origin=FEED_URL)) == 0
    session.commit()

    assert _conta_documentos(session) == 2


def test_duplicata_dentro_do_mesmo_lote_entra_uma_vez(session: Session) -> None:
    item = NewsItem(
        url="https://exemplo.test/n1",
        title="t",
        content="c",
        origin=FEED_URL,
        published_at=None,
    )
    assert store_items(session, [item, item]) == 1
    session.commit()
    assert _conta_documentos(session) == 1


def test_urls_equivalentes_colidem_no_dedupe(session: Session) -> None:
    def item(url: str) -> NewsItem:
        return NewsItem(url=url, title="t", content="c", origin=FEED_URL, published_at=None)

    assert store_items(session, [item("https://exemplo.test/n1?utm_source=a")]) == 1
    session.commit()
    assert store_items(session, [item("https://exemplo.test/n1#fim")]) == 0
    session.commit()
    assert _conta_documentos(session) == 1


def test_lote_vazio_nao_toca_o_banco(session: Session) -> None:
    assert store_items(session, []) == 0


def test_indice_unico_impede_duplicata_no_banco(session: Session) -> None:
    """O dedupe é do schema, não da aplicação: insert direto também colide."""
    for _ in range(2):
        session.add(
            Document(
                source=DocumentSource.NEWS,
                dedupe_hash="a" * 64,
                url="https://exemplo.test/n1",
                title="t",
                content="c",
                origin=FEED_URL,
            )
        )
    with pytest.raises(IntegrityError):
        session.commit()


# --- fetch -----------------------------------------------------------------
def test_fetch_feed_devolve_corpo(feed_client: httpx.Client) -> None:
    assert b"ECB mantem juros" in fetch_feed(FEED_URL, client=feed_client)


def test_fetch_feed_erro_http_vira_news_fetch_error() -> None:
    chamadas = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal chamadas
        chamadas += 1
        return httpx.Response(503)

    # `retry_with` é do wrapper do tenacity; o mypy só enxerga o Callable original.
    sem_espera = fetch_feed.retry_with(wait=wait_none())  # type: ignore[attr-defined]
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(NewsFetchError, match="falha ao buscar feed"),
    ):
        sem_espera(FEED_URL, client=client)

    assert chamadas == 3  # backoff com 3 tentativas


def test_fetch_feed_erro_de_rede_vira_news_fetch_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sem rota")

    # `retry_with` é do wrapper do tenacity; o mypy só enxerga o Callable original.
    sem_espera = fetch_feed.retry_with(wait=wait_none())  # type: ignore[attr-defined]
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(NewsFetchError),
    ):
        sem_espera(FEED_URL, client=client)


# --- orquestração ----------------------------------------------------------
def test_collect_feed_ponta_a_ponta(session: Session, feed_client: httpx.Client) -> None:
    assert collect_feed(session, FEED_URL, client=feed_client) == 2
    session.commit()
    assert _conta_documentos(session) == 2


def test_collect_news_usa_feeds_da_config(session: Session, feed_client: httpx.Client) -> None:
    settings = Settings(_env_file=None, news_rss_feeds=f"{FEED_URL}, ")
    assert collect_news(session, client=feed_client, settings=settings) == {FEED_URL: 2}


def test_collect_news_pula_feed_que_falha(session: Session) -> None:
    """Notícia é entrada best-effort: uma fonte fora do ar não cega as outras."""
    bom = "https://ok.test/rss"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ok.test":
            return httpx.Response(200, content=FEED_XML.encode())
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        resultado = collect_news(
            session, feeds=["https://ruim.test/rss", bom], client=client, settings=None
        )

    session.commit()
    assert resultado == {bom: 2}
    assert _conta_documentos(session) == 2
