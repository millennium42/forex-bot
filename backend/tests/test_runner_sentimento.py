"""História 25 — sentimento entra na decisão do runner.

O runner passava sempre `sentiment=None` para `fuse_signals`: a decisão era
puramente técnica, com teto matemático de confiança em 0.7 (o peso técnico).
Estes testes cobrem a busca de documentos recentes do par, a agregação em um
`SentimentScore` único e a gravação em `Signal.sentiment_score`/
`sentiment_confidence` — sem documento vira `None`, nunca score forjado.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from backend.analysis.sentiment_analyzer import SentimentAnalyzer, SentimentScore
from backend.analysis.signal_fusion import fuse_signals
from backend.analysis.technical_analyzer import IndicatorSnapshot, TechnicalScore
from backend.config import Settings
from backend.execution.runner import BotRunner
from backend.models.document import Document
from backend.models.enums import DocumentSource
from backend.models.instrument import Instrument

SYMBOL = "EURUSD"


class FakeBackend:
    """Backend determinístico: cada texto tem um score fixo, cadastrado no teste."""

    name = "fake"

    def __init__(self, por_texto: dict[str, SentimentScore]) -> None:
        self._por_texto = por_texto

    def score(self, text: str) -> SentimentScore:
        return self._por_texto[text]


def _settings(**overrides: Any) -> Settings:
    # `sentiment_enabled=True` por default aqui: o default de produção é
    # desligado (história 38), mas este arquivo existe justamente para exercitar
    # o caminho com sentimento. Os testes do caminho desligado ficam separados,
    # abaixo, e ligam/desligam de forma explícita.
    base: dict[str, Any] = {
        "trading_mode": "demo",
        "real_trading_unlocked": False,
        "sentiment_enabled": True,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _runner(analyzer: SentimentAnalyzer | None = None, **settings_overrides: Any) -> BotRunner:
    return BotRunner(
        symbols=[SYMBOL], settings=_settings(**settings_overrides), sentiment_analyzer=analyzer
    )


def _documento(session: Session, content: str, *, minutos_atras: int) -> Document:
    dedupe = hashlib.sha256(f"{content}-{minutos_atras}".encode()).hexdigest()
    doc = Document(
        source=DocumentSource.NEWS,
        dedupe_hash=dedupe,
        url=f"https://exemplo.test/{dedupe[:8]}",
        title=None,
        content=content,
        origin="teste",
        created_at=datetime.now(UTC) - timedelta(minutes=minutos_atras),
    )
    session.add(doc)
    session.commit()
    return doc


# -- ausência de documento não vira score forjado (AC3) -----------------------


def test_obter_sentimento_sem_documento_retorna_none(session: Session) -> None:
    assert _runner()._obter_sentimento(session, SYMBOL) is None


def test_obter_sentimento_ignora_documento_de_outro_par(session: Session) -> None:
    _documento(session, "GBPUSD dispara com decisão do BoE", minutos_atras=5)
    assert _runner()._obter_sentimento(session, SYMBOL) is None


# -- janela de recência configurável (AC2) -------------------------------------


def test_obter_sentimento_ignora_documento_fora_da_janela(session: Session) -> None:
    _documento(session, f"{SYMBOL} recua com dado de inflação", minutos_atras=120)
    runner = _runner(sentiment_lookback_minutes=30)
    assert runner._obter_sentimento(session, SYMBOL) is None


def test_obter_sentimento_considera_documento_dentro_da_janela(session: Session) -> None:
    texto = f"{SYMBOL} sobe com dado de emprego"
    _documento(session, texto, minutos_atras=10)
    backend = FakeBackend({texto: SentimentScore(score=0.5, confidence=0.8, engine="fake")})
    runner = _runner(
        SentimentAnalyzer(backend=backend, settings=_settings()), sentiment_lookback_minutes=30
    )

    resultado = runner._obter_sentimento(session, SYMBOL)

    assert resultado is not None
    assert resultado.score == pytest.approx(0.5)


# -- agrega documentos concordantes e discordantes (AC1, AC4, AC6) ------------


def test_obter_sentimento_documentos_concordantes(session: Session) -> None:
    texto1 = f"{SYMBOL} sobe com decisão do Fed"
    texto2 = f"{SYMBOL} avança em dia de otimismo"
    _documento(session, texto1, minutos_atras=5)
    _documento(session, texto2, minutos_atras=15)
    backend = FakeBackend(
        {
            texto1: SentimentScore(score=0.8, confidence=0.9, engine="fake"),
            texto2: SentimentScore(score=0.6, confidence=0.9, engine="fake"),
        }
    )
    runner = _runner(SentimentAnalyzer(backend=backend, settings=_settings()))

    resultado = runner._obter_sentimento(session, SYMBOL)

    assert resultado is not None
    assert resultado.score == pytest.approx((0.8 * 0.9 + 0.6 * 0.9) / (0.9 + 0.9))
    assert resultado.confidence == pytest.approx(0.9)


def test_obter_sentimento_documentos_discordantes(session: Session) -> None:
    texto_pos = f"{SYMBOL} sobe com dado forte de PIB"
    texto_neg = f"{SYMBOL} recua com temor de recessão"
    _documento(session, texto_pos, minutos_atras=5)
    _documento(session, texto_neg, minutos_atras=5)
    backend = FakeBackend(
        {
            texto_pos: SentimentScore(score=0.9, confidence=0.9, engine="fake"),
            texto_neg: SentimentScore(score=-0.9, confidence=0.9, engine="fake"),
        }
    )
    runner = _runner(SentimentAnalyzer(backend=backend, settings=_settings()))

    resultado = runner._obter_sentimento(session, SYMBOL)

    assert resultado is not None
    assert resultado.score == pytest.approx(0.0, abs=1e-9)


# -- score de sentimento vai para o Signal (AC5) -------------------------------


def test_registrar_signal_grava_sentimento_quando_presente(session: Session) -> None:
    instrument = Instrument(symbol=SYMBOL)
    session.add(instrument)
    session.commit()

    technical = TechnicalScore(score=0.8, confidence=1.0, components={"rsi": 0.8}, indicators=None)
    sentiment = SentimentScore(score=0.8, confidence=1.0, engine="fake")
    fused = fuse_signals(technical=technical, sentiment=sentiment)
    indicadores = IndicatorSnapshot(
        close=1.1,
        rsi=70,
        macd=0.001,
        macd_signal=0.0005,
        macd_diff=0.0005,
        bb_high=1.2,
        bb_low=1.0,
        bb_pct=0.8,
        atr=0.001,
    )

    signal = _runner()._registrar_signal(
        session, instrument, fused, technical, indicadores, sentiment
    )

    assert signal.sentiment_score == pytest.approx(0.8)
    assert signal.sentiment_confidence == pytest.approx(1.0)
    # Técnica e sentimento concordando ultrapassam o teto de 0.7 que existia
    # quando o runner só usava a ponta técnica (peso técnico = 0.7).
    assert signal.confidence > 0.7


def test_registrar_signal_sem_sentimento_grava_nulo(session: Session) -> None:
    instrument = Instrument(symbol=SYMBOL)
    session.add(instrument)
    session.commit()

    technical = TechnicalScore(score=0.8, confidence=1.0, components={}, indicators=None)
    fused = fuse_signals(technical=technical, sentiment=None)
    indicadores = IndicatorSnapshot(
        close=1.1,
        rsi=70,
        macd=0.001,
        macd_signal=0.0005,
        macd_diff=0.0005,
        bb_high=1.2,
        bb_low=1.0,
        bb_pct=0.8,
        atr=0.001,
    )

    signal = _runner()._registrar_signal(session, instrument, fused, technical, indicadores, None)

    assert signal.sentiment_score is None
    assert signal.sentiment_confidence is None


# -- backend de NLP indisponível degrada para None, não derruba o ciclo -------


def test_obter_sentimento_com_backend_indisponivel_retorna_none(session: Session) -> None:
    """Sem o extra `nlp` instalado, `SentimentAnalyzer()` real levanta
    `SentimentUnavailableError`. O runner precisa degradar para `None`, não
    derrubar o ciclo — mesma postura best-effort dos coletores."""
    texto = f"{SYMBOL} sobe forte"
    _documento(session, texto, minutos_atras=5)

    runner = _runner()  # sem analyzer injetado: carrega de verdade sob demanda

    assert runner._obter_sentimento(session, SYMBOL) is None


# --- história 38: sentimento desligado -------------------------------------
def test_sentimento_desligado_nao_consulta_documento(session: Session) -> None:
    """Desligado, nem chega a olhar o banco — a decisão fica puramente técnica."""
    _documento(session, "euro rallies strongly on EURUSD", minutos_atras=1)

    runner = _runner(sentiment_enabled=False)

    assert runner._obter_sentimento(session, SYMBOL) is None


def test_sentimento_desligado_nao_carrega_o_analisador(session: Session) -> None:
    """Sem analisador injetado e desligado: não pode tentar carregar o modelo."""
    _documento(session, "EURUSD outlook positive", minutos_atras=1)
    runner = BotRunner(symbols=[SYMBOL], settings=_settings(sentiment_enabled=False))

    assert runner._obter_sentimento(session, SYMBOL) is None
    # `_sentiment_analyzer` continua None: o loader nunca foi acionado.
    assert runner._sentiment_analyzer is None
