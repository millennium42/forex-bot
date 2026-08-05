"""História 5 — coleta de tweets com dedupe por id, na mesma fila e schema do news."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.collection.documents import store_items
from backend.collection.news_collector import url_fingerprint
from backend.collection.twitter_collector import (
    DEFAULT_ORIGIN,
    MAX_RULE_LENGTH,
    RULE_TAG,
    STREAM_URL,
    TweetItem,
    TwitterStreamError,
    build_client,
    build_rules,
    collect_tweets,
    fetch_rules,
    iter_tweets,
    parse_payload,
    stream_tweets,
    sync_rules,
    tweet_fingerprint,
)
from backend.config import Settings
from backend.models import Document, DocumentSource

CASHTAGS = ["$EURUSD", "$GBPUSD"]


def _objeto(tweet_id: str, texto: str = "EURUSD subindo forte", tag: str = RULE_TAG) -> str:
    return json.dumps(
        {
            "data": {
                "id": tweet_id,
                "text": texto,
                "author_id": "42",
                "created_at": "2026-08-05T12:00:00.000Z",
            },
            "matching_rules": [{"id": "1", "tag": tag}],
        }
    )


def _stream_body(*ids: str) -> bytes:
    # Linha em branco no meio: keep-alive do servidor, exatamente como a API manda.
    linhas = [_objeto(i) for i in ids]
    return ("\n\n".join(linhas) + "\n").encode()


def _handler(
    stream: bytes,
    regras: list[dict[str, str]] | None = None,
    chamadas: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if chamadas is not None:
            chamadas.append(request)
        if request.url.path.endswith("/rules"):
            if request.method == "GET":
                return httpx.Response(200, json={"data": regras or []})
            return httpx.Response(200, json={"meta": {"sent": "ok"}})
        return httpx.Response(200, content=stream)

    return httpx.MockTransport(handler)


@pytest.fixture
def stream_client() -> Iterator[httpx.Client]:
    with httpx.Client(transport=_handler(_stream_body("1", "2"))) as client:
        yield client


def _conta_documentos(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Document)) or 0


# --- hash ------------------------------------------------------------------
def test_fingerprint_tem_64_hex() -> None:
    valor = tweet_fingerprint("123")
    assert len(valor) == 64
    assert set(valor) <= set("0123456789abcdef")


def test_fingerprint_e_estavel_para_o_mesmo_id() -> None:
    assert tweet_fingerprint("123") == tweet_fingerprint(" 123 ")


def test_fingerprint_distingue_ids() -> None:
    assert tweet_fingerprint("123") != tweet_fingerprint("124")


def test_fingerprint_nao_colide_com_hash_de_url() -> None:
    """Namespace separado: o hash de tweet e o de notícia dividem a mesma coluna única."""
    assert tweet_fingerprint("https://exemplo.test/a") != url_fingerprint("https://exemplo.test/a")


# --- regras do stream ------------------------------------------------------
def test_build_rules_agrupa_cashtags_com_or() -> None:
    assert build_rules(CASHTAGS) == ["($EURUSD OR $GBPUSD) -is:retweet"]


def test_build_rules_exclui_retweet() -> None:
    assert build_rules(["$EURUSD"])[0].endswith("-is:retweet")


def test_build_rules_sem_cashtag_devolve_vazio() -> None:
    assert build_rules([" ", ""]) == []


def test_build_rules_respeita_limite_de_caracteres() -> None:
    termos = [f"$SYM{i:03d}" for i in range(100)]
    regras = build_rules(termos)
    assert len(regras) > 1
    assert all(len(r) <= MAX_RULE_LENGTH for r in regras)
    # Nenhum termo se perde no agrupamento.
    assert sum(r.count(" OR ") + 1 for r in regras) == len(termos)


def test_fetch_rules_mapeia_id_para_valor() -> None:
    regras = [{"id": "7", "value": "($EURUSD) -is:retweet"}]
    with httpx.Client(transport=_handler(b"", regras=regras)) as client:
        assert fetch_rules(client) == {"7": "($EURUSD) -is:retweet"}


def test_sync_rules_cadastra_quando_nao_ha_nada() -> None:
    chamadas: list[httpx.Request] = []
    with httpx.Client(transport=_handler(b"", chamadas=chamadas)) as client:
        assert sync_rules(client, build_rules(CASHTAGS)) == build_rules(CASHTAGS)

    posts = [c for c in chamadas if c.method == "POST"]
    assert len(posts) == 1
    assert json.loads(posts[0].content)["add"][0]["tag"] == RULE_TAG


def test_sync_rules_apaga_regra_obsoleta_antes_de_cadastrar() -> None:
    """Regra é global por conta: filtro antigo continuaria injetando fora do escopo."""
    chamadas: list[httpx.Request] = []
    antigas = [{"id": "9", "value": "($XAUUSD) -is:retweet"}]
    with httpx.Client(transport=_handler(b"", regras=antigas, chamadas=chamadas)) as client:
        sync_rules(client, build_rules(CASHTAGS))

    corpos = [json.loads(c.content) for c in chamadas if c.method == "POST"]
    assert corpos[0] == {"delete": {"ids": ["9"]}}
    assert "add" in corpos[1]


def test_sync_rules_nao_reescreve_regra_igual() -> None:
    chamadas: list[httpx.Request] = []
    atuais = [{"id": "1", "value": build_rules(CASHTAGS)[0]}]
    with httpx.Client(transport=_handler(b"", regras=atuais, chamadas=chamadas)) as client:
        sync_rules(client, build_rules(CASHTAGS))

    assert [c for c in chamadas if c.method == "POST"] == []


def test_erro_http_no_endpoint_de_regras_vira_twitter_stream_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(TwitterStreamError, match="endpoint de regras"),
    ):
        fetch_rules(client)


# --- parse -----------------------------------------------------------------
def test_parse_payload_extrai_campos() -> None:
    item = parse_payload(json.loads(_objeto("77")))
    assert item is not None
    assert item.tweet_id == "77"
    assert item.text == "EURUSD subindo forte"
    assert item.author_id == "42"
    assert item.published_at == datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def test_parse_payload_usa_tag_da_regra_como_origem() -> None:
    item = parse_payload(json.loads(_objeto("77", tag="twitter:majors")))
    assert item is not None
    assert item.origin == "twitter:majors"


def test_parse_payload_sem_tag_cai_na_origem_default() -> None:
    item = parse_payload({"data": {"id": "1", "text": "x"}})
    assert item is not None
    assert item.origin == DEFAULT_ORIGIN


def test_parse_payload_descarta_objeto_sem_id() -> None:
    assert parse_payload({"data": {"text": "sem id"}}) is None


def test_parse_payload_descarta_objeto_sem_texto() -> None:
    assert parse_payload({"data": {"id": "1", "text": "   "}}) is None


def test_parse_payload_descarta_erro_do_stream() -> None:
    """Objeto de erro operacional da API não tem `data`."""
    assert parse_payload({"errors": [{"title": "operational-disconnect"}]}) is None
    assert parse_payload("nao e objeto") is None


def test_parse_payload_data_invalida_deixa_published_at_nulo() -> None:
    item = parse_payload({"data": {"id": "1", "text": "x", "created_at": "ontem"}})
    assert item is not None
    assert item.published_at is None


# --- iteração do NDJSON ----------------------------------------------------
def test_iter_tweets_ignora_keep_alive() -> None:
    linhas = [_objeto("1"), "", "   ", _objeto("2")]
    assert [t.tweet_id for t in iter_tweets(linhas)] == ["1", "2"]


def test_iter_tweets_pula_linha_invalida() -> None:
    """Um objeto malformado não pode encerrar a coleta inteira."""
    assert [t.tweet_id for t in iter_tweets(["{quebrado", _objeto("2")])] == ["2"]


def test_iter_tweets_respeita_limite() -> None:
    linhas = [_objeto(str(i)) for i in range(5)]
    assert len(list(iter_tweets(linhas, limit=2))) == 2


def test_stream_tweets_le_o_corpo(stream_client: httpx.Client) -> None:
    assert [t.tweet_id for t in stream_tweets(stream_client)] == ["1", "2"]


def test_stream_tweets_erro_http_vira_twitter_stream_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"title": "Unauthorized"})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(TwitterStreamError, match="HTTP 401"),
    ):
        stream_tweets(client)


def test_stream_tweets_erro_de_rede_vira_twitter_stream_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sem rota")

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(TwitterStreamError, match="falha no stream"),
    ):
        stream_tweets(client)


# --- persistência e dedupe -------------------------------------------------
def _item(tweet_id: str) -> TweetItem:
    return TweetItem(
        tweet_id=tweet_id,
        text="EURUSD",
        origin=DEFAULT_ORIGIN,
        author_id="42",
        published_at=None,
    )


def test_tweet_vira_documento_de_twitter(session: Session) -> None:
    """Mesmo schema de documento do news collector — só muda a fonte."""
    assert store_items(session, [_item("77")]) == 1
    session.commit()

    doc = session.scalars(select(Document)).one()
    assert doc.source is DocumentSource.TWITTER
    assert doc.dedupe_hash == tweet_fingerprint("77")
    assert doc.url == "https://twitter.com/i/web/status/77"
    assert doc.title is None
    assert doc.origin == DEFAULT_ORIGIN


def test_dedupe_por_tweet_id(session: Session) -> None:
    """Critério de aceite da história 5."""
    assert store_items(session, [_item("1"), _item("2")]) == 2
    session.commit()

    # Mesmo id, texto editado pela fonte: continua sendo o mesmo tweet.
    repetido = TweetItem(
        tweet_id="1", text="outro texto", origin=DEFAULT_ORIGIN, author_id="9", published_at=None
    )
    assert store_items(session, [repetido]) == 0
    session.commit()
    assert _conta_documentos(session) == 2


def test_duplicata_dentro_do_mesmo_lote_entra_uma_vez(session: Session) -> None:
    assert store_items(session, [_item("1"), _item("1")]) == 1
    session.commit()
    assert _conta_documentos(session) == 1


def test_tweet_e_noticia_convivem_na_mesma_tabela(session: Session) -> None:
    from backend.collection.news_collector import NewsItem

    noticia = NewsItem(
        url="https://exemplo.test/n1",
        title="t",
        content="c",
        origin="https://exemplo.test/rss",
        published_at=None,
    )
    assert store_items(session, [noticia]) == 1
    assert store_items(session, [_item("1")]) == 1
    session.commit()

    fontes = set(session.scalars(select(Document.source)).all())
    assert fontes == {DocumentSource.NEWS, DocumentSource.TWITTER}


# --- orquestração ----------------------------------------------------------
def test_collect_tweets_ponta_a_ponta(session: Session, stream_client: httpx.Client) -> None:
    assert collect_tweets(session, client=stream_client, cashtags=CASHTAGS) == 2
    session.commit()
    assert _conta_documentos(session) == 2


def test_collect_tweets_reprocessado_nao_duplica(
    session: Session, stream_client: httpx.Client
) -> None:
    assert collect_tweets(session, client=stream_client, cashtags=CASHTAGS) == 2
    session.commit()
    assert collect_tweets(session, client=stream_client, cashtags=CASHTAGS) == 0
    session.commit()
    assert _conta_documentos(session) == 2


def test_collect_tweets_usa_cashtags_da_config(
    session: Session, stream_client: httpx.Client
) -> None:
    settings = Settings(_env_file=None, twitter_cashtags="$EURUSD, ")
    assert collect_tweets(session, client=stream_client, settings=settings) == 2


def test_collect_tweets_sem_cashtags_nao_abre_stream(session: Session) -> None:
    settings = Settings(_env_file=None, twitter_cashtags="")
    assert collect_tweets(session, cashtags=None, settings=settings) == 0


def test_collect_tweets_sem_token_nao_abre_stream(session: Session) -> None:
    """Sem credencial a rodada é pulada — nunca substituída por dado inventado."""
    settings = Settings(_env_file=None, twitter_cashtags="$EURUSD", twitter_bearer_token=None)
    assert collect_tweets(session, cashtags=CASHTAGS, settings=settings) == 0


def test_collect_tweets_respeita_limite(session: Session) -> None:
    with httpx.Client(transport=_handler(_stream_body("1", "2", "3"))) as client:
        assert collect_tweets(session, client=client, cashtags=CASHTAGS, limit=1) == 1


def test_build_client_manda_o_token_no_header() -> None:
    with build_client("segredo") as client:
        assert client.headers["Authorization"] == "Bearer segredo"


def test_stream_url_e_o_filtered_stream_da_v2() -> None:
    assert STREAM_URL == "https://api.twitter.com/2/tweets/search/stream"
