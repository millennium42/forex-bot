"""História 6 — sentimento em [-1,1] + confiança, cache Redis, fallback TextBlob."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest

from backend.analysis import sentiment_analyzer as sa
from backend.analysis.sentiment_analyzer import (
    FINBERT_ENGINE,
    TEXTBLOB_ENGINE,
    FinBertBackend,
    SentimentAnalyzer,
    SentimentScore,
    SentimentUnavailableError,
    TextBlobBackend,
    build_cache,
    cache_key,
    load_backend,
    neutral,
    text_fingerprint,
)
from backend.config import Settings

TEXTO = "ECB signals rate cut, euro weakens against the dollar"


# --------------------------------------------------------------------------- #
# Dublês
# --------------------------------------------------------------------------- #
class FakeCache:
    """Redis em memória. Conta as gravações para provar que o TTL foi passado."""

    def __init__(self) -> None:
        self.dados: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.gets = 0

    def get(self, name: str) -> Any:
        self.gets += 1
        return self.dados.get(name)

    def set(self, name: str, value: str, ex: int | None = None) -> Any:
        self.dados[name] = value
        self.ttls[name] = ex
        return True


class QuebrandoCache:
    """Redis fora do ar: toda operação levanta."""

    def get(self, name: str) -> Any:
        raise ConnectionError("redis fora do ar")

    def set(self, name: str, value: str, ex: int | None = None) -> Any:
        raise ConnectionError("redis fora do ar")


class ContandoBackend:
    """Backend que registra quantas vezes o modelo foi de fato acionado."""

    name = "contador"

    def __init__(self, score: float = 0.5, confidence: float = 0.9) -> None:
        self.chamadas = 0
        self._score = score
        self._confidence = confidence

    def score(self, text: str) -> SentimentScore:
        self.chamadas += 1
        return SentimentScore(score=self._score, confidence=self._confidence, engine=self.name)


def _pipeline(label: str, prob: float) -> Any:
    return lambda _text: [{"label": label, "score": prob}]


def _textblob(polarity: float) -> Any:
    class _Sentiment:
        def __init__(self, p: float) -> None:
            self.polarity = p
            self.subjectivity = 0.5

    class _Blob:
        def __init__(self, _text: str) -> None:
            self.sentiment = _Sentiment(polarity)

    return _Blob


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


# --------------------------------------------------------------------------- #
# SentimentScore — faixas contratadas
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [(2.5, 1.0), (-7.0, -1.0), (0.3, 0.3), (-1.0, -1.0), (1.0, 1.0)],
)
def test_score_sempre_em_menos_um_a_um(bruto: float, esperado: float) -> None:
    assert SentimentScore(score=bruto, confidence=0.5, engine="x").score == esperado


@pytest.mark.parametrize(("bruto", "esperado"), [(1.7, 1.0), (-0.4, 0.0), (0.6, 0.6)])
def test_confianca_sempre_em_zero_a_um(bruto: float, esperado: float) -> None:
    assert SentimentScore(score=0.0, confidence=bruto, engine="x").confidence == esperado


def test_neutral_tem_confianca_zero() -> None:
    resultado = neutral("x")
    assert (resultado.score, resultado.confidence) == (0.0, 0.0)


def test_roundtrip_json() -> None:
    original = SentimentScore(score=-0.42, confidence=0.81, engine=FINBERT_ENGINE)
    assert SentimentScore.from_json(original.to_json()) == original


def test_roundtrip_json_aceita_bytes() -> None:
    """O Redis devolve bytes, não str."""
    original = SentimentScore(score=0.1, confidence=0.2, engine="x")
    assert SentimentScore.from_json(original.to_json().encode()) == original


def test_from_json_normaliza_valor_fora_da_faixa() -> None:
    """Entrada de cache adulterada não escapa do clamp."""
    bruto = json.dumps({"score": 9.0, "confidence": 9.0, "engine": "x"})
    resultado = SentimentScore.from_json(bruto)
    assert resultado is not None
    assert (resultado.score, resultado.confidence) == (1.0, 1.0)


@pytest.mark.parametrize(
    "bruto",
    [
        "não é json",
        json.dumps({"score": 0.1}),
        json.dumps(["lista"]),
        json.dumps({"score": "x"}),
        42,
    ],
)
def test_from_json_devolve_none_para_payload_inservivel(bruto: object) -> None:
    assert SentimentScore.from_json(bruto) is None


# --------------------------------------------------------------------------- #
# FinBERT
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("label", "prob", "score_esperado"),
    [("positive", 0.93, 0.93), ("negative", 0.88, -0.88), ("neutral", 0.7, 0.0)],
)
def test_finbert_converte_rotulo_em_score_assinado(
    label: str, prob: float, score_esperado: float
) -> None:
    backend = FinBertBackend("modelo", pipeline=_pipeline(label, prob))
    resultado = backend.score(TEXTO)
    assert resultado.score == pytest.approx(score_esperado)
    assert resultado.confidence == pytest.approx(prob)
    assert resultado.engine == FINBERT_ENGINE


def test_finbert_aceita_saida_aninhada() -> None:
    """`return_all_scores` devolve lista de listas; o achatamento cobre os dois formatos."""
    backend = FinBertBackend("modelo", pipeline=lambda _t: [[{"label": "positive", "score": 0.6}]])
    assert backend.score(TEXTO).score == pytest.approx(0.6)


def test_finbert_com_rotulo_desconhecido_vira_neutro_sem_confianca() -> None:
    backend = FinBertBackend("modelo", pipeline=_pipeline("LABEL_7", 0.99))
    resultado = backend.score(TEXTO)
    assert (resultado.score, resultado.confidence) == (0.0, 0.0)


def test_finbert_com_saida_vazia_vira_neutro() -> None:
    backend = FinBertBackend("modelo", pipeline=lambda _t: [])
    assert backend.score(TEXTO).confidence == 0.0


# --------------------------------------------------------------------------- #
# TextBlob
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("polarity", [-1.0, -0.3, 0.0, 0.45, 1.0])
def test_textblob_usa_polaridade_como_score(polarity: float) -> None:
    backend = TextBlobBackend(factory=_textblob(polarity))
    resultado = backend.score(TEXTO)
    assert resultado.score == pytest.approx(polarity)
    assert resultado.confidence == pytest.approx(abs(polarity))
    assert resultado.engine == TEXTBLOB_ENGINE


# --------------------------------------------------------------------------- #
# Seleção de backend
# --------------------------------------------------------------------------- #
def test_load_backend_prefere_finbert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sa, "_load_finbert_pipeline", lambda _m: _pipeline("positive", 0.5))
    assert load_backend(_settings()).name == FINBERT_ENGINE


def test_load_backend_cai_para_textblob_quando_finbert_indisponivel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _sem_transformers(_model: str) -> Any:
        raise ImportError("No module named 'transformers'")

    monkeypatch.setattr(sa, "_load_finbert_pipeline", _sem_transformers)
    monkeypatch.setattr(sa, "_load_textblob", lambda: _textblob(0.2))
    assert load_backend(_settings()).name == TEXTBLOB_ENGINE


def test_load_backend_cai_para_textblob_quando_modelo_nao_baixa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Peso ausente/offline levanta OSError no transformers, não ImportError."""

    def _sem_peso(_model: str) -> Any:
        raise OSError("não foi possível baixar o modelo")

    monkeypatch.setattr(sa, "_load_finbert_pipeline", _sem_peso)
    monkeypatch.setattr(sa, "_load_textblob", lambda: _textblob(0.2))
    assert load_backend(_settings()).name == TEXTBLOB_ENGINE


def test_load_backend_levanta_sem_nenhum_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    def _falha(*_args: Any) -> Any:
        raise ImportError("faltando")

    monkeypatch.setattr(sa, "_load_finbert_pipeline", _falha)
    monkeypatch.setattr(sa, "_load_textblob", _falha)
    with pytest.raises(SentimentUnavailableError):
        load_backend(_settings())


# --------------------------------------------------------------------------- #
# Chave de cache
# --------------------------------------------------------------------------- #
def test_fingerprint_normaliza_espacos() -> None:
    assert text_fingerprint("euro  cai\n forte") == text_fingerprint("euro cai forte")


def test_fingerprint_distingue_textos() -> None:
    assert text_fingerprint("euro cai") != text_fingerprint("euro sobe")


def test_chave_separa_backends() -> None:
    """Score de motor diferente não pode ser reaproveitado."""
    assert cache_key(FINBERT_ENGINE, TEXTO) != cache_key(TEXTBLOB_ENGINE, TEXTO)


# --------------------------------------------------------------------------- #
# Analisador + cache
# --------------------------------------------------------------------------- #
def test_cache_hit_nao_reprocessa_modelo() -> None:
    backend = ContandoBackend()
    cache = FakeCache()
    analyzer = SentimentAnalyzer(backend=backend, cache=cache, settings=_settings())

    primeiro = analyzer.analyze(TEXTO)
    segundo = analyzer.analyze(TEXTO)

    assert backend.chamadas == 1
    assert primeiro == segundo


def test_cache_hit_vale_para_texto_equivalente() -> None:
    backend = ContandoBackend()
    analyzer = SentimentAnalyzer(backend=backend, cache=FakeCache(), settings=_settings())

    analyzer.analyze("euro  cai\nforte")
    analyzer.analyze("euro cai forte")

    assert backend.chamadas == 1


def test_grava_no_cache_com_ttl_da_config() -> None:
    cache = FakeCache()
    analyzer = SentimentAnalyzer(
        backend=ContandoBackend(),
        cache=cache,
        settings=_settings(sentiment_cache_ttl_seconds=1234),
    )
    analyzer.analyze(TEXTO)

    chave = cache_key("contador", TEXTO)
    assert cache.ttls[chave] == 1234
    assert json.loads(cache.dados[chave])["engine"] == "contador"


def test_ttl_explicito_vence_a_config() -> None:
    cache = FakeCache()
    analyzer = SentimentAnalyzer(
        backend=ContandoBackend(),
        cache=cache,
        ttl_seconds=60,
        settings=_settings(sentiment_cache_ttl_seconds=1234),
    )
    analyzer.analyze(TEXTO)
    assert cache.ttls[cache_key("contador", TEXTO)] == 60


def test_cache_fora_do_ar_nao_derruba_a_analise() -> None:
    backend = ContandoBackend()
    analyzer = SentimentAnalyzer(backend=backend, cache=QuebrandoCache(), settings=_settings())

    resultado = analyzer.analyze(TEXTO)

    assert resultado.confidence == pytest.approx(0.9)
    assert backend.chamadas == 1


def test_entrada_corrompida_no_cache_recalcula() -> None:
    backend = ContandoBackend()
    cache = FakeCache()
    cache.dados[cache_key("contador", TEXTO)] = "{lixo"
    analyzer = SentimentAnalyzer(backend=backend, cache=cache, settings=_settings())

    resultado = analyzer.analyze(TEXTO)

    assert backend.chamadas == 1
    assert resultado.score == pytest.approx(0.5)


def test_sem_cache_configurado_sempre_reprocessa() -> None:
    backend = ContandoBackend()
    analyzer = SentimentAnalyzer(backend=backend, cache=None, settings=_settings())

    analyzer.analyze(TEXTO)
    analyzer.analyze(TEXTO)

    assert backend.chamadas == 2


@pytest.mark.parametrize("texto", ["", "   ", "\n\t"])
def test_texto_vazio_nao_aciona_o_modelo(texto: str) -> None:
    backend = ContandoBackend()
    analyzer = SentimentAnalyzer(backend=backend, cache=FakeCache(), settings=_settings())

    resultado = analyzer.analyze(texto)

    assert backend.chamadas == 0
    assert (resultado.score, resultado.confidence) == (0.0, 0.0)


def test_analyzer_usa_load_backend_quando_nao_injetado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sa, "_load_finbert_pipeline", lambda _m: _pipeline("negative", 0.75))
    analyzer = SentimentAnalyzer(cache=FakeCache(), settings=_settings())

    assert analyzer.engine == FINBERT_ENGINE
    assert analyzer.analyze(TEXTO).score == pytest.approx(-0.75)


# --------------------------------------------------------------------------- #
# Redis de verdade
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_cache_hit_com_redis_real() -> None:
    settings = _settings()
    try:
        cache = build_cache(settings)
        cache.set("sentiment:ping", "1", ex=5)
    except Exception as exc:  # pragma: no cover - depende do ambiente
        pytest.skip(f"Redis indisponível em {settings.redis_url}: {exc}")

    backend = ContandoBackend(score=-0.25, confidence=0.6)
    analyzer = SentimentAnalyzer(backend=backend, cache=cache, ttl_seconds=30, settings=settings)
    # Texto único por execução: rodar a suíte duas vezes seguidas acharia a
    # entrada da rodada anterior ainda viva e o contador não provaria nada.
    texto = f"{TEXTO} :: {uuid4()}"

    primeiro = analyzer.analyze(texto)
    segundo = analyzer.analyze(texto)

    assert backend.chamadas == 1
    assert primeiro == segundo == SentimentScore(-0.25, 0.6, "contador")
