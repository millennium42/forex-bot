"""História 1 — a config falha fechado para o modo demo."""

from __future__ import annotations

import pytest

from backend.config import TIMEFRAME_MAP, Settings, TradingMode, get_settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"trading_mode": "demo", "real_trading_unlocked": False}
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def test_default_e_demo() -> None:
    assert _settings().effective_trading_mode is TradingMode.DEMO


def test_real_exige_as_duas_condicoes() -> None:
    assert _settings(trading_mode="real").effective_trading_mode is TradingMode.DEMO
    assert _settings(real_trading_unlocked=True).effective_trading_mode is TradingMode.DEMO

    liberado = _settings(trading_mode="real", real_trading_unlocked=True)
    assert liberado.effective_trading_mode is TradingMode.REAL
    assert liberado.is_real_trading is True


@pytest.mark.parametrize(
    "valor",
    ["REAL", " real ", "Real"],
)
def test_variacoes_de_caixa_e_espaco_ainda_valem(valor: str) -> None:
    assert _settings(trading_mode=valor, real_trading_unlocked=True).is_real_trading


@pytest.mark.parametrize(
    "valor",
    ["", "prod", "production", "live", "demo", "rea", "real2", "1", "true"],
)
def test_valor_desconhecido_resolve_para_demo(valor: str) -> None:
    """Fail-closed: nada além de 'real' pode virar real, nem com o unlock ligado."""
    s = _settings(trading_mode=valor, real_trading_unlocked=True)
    assert s.effective_trading_mode is TradingMode.DEMO


def test_limites_de_risco_sao_validados() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _settings(max_risk_per_trade_pct=0)
    with pytest.raises(ValidationError):
        _settings(max_daily_loss_pct=101)


def test_default_do_teto_de_volume_por_ordem_e_dois_lotes() -> None:
    assert _settings().volume_max_per_order_lots == 2.0


def test_teto_de_volume_por_ordem_e_validado() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _settings(volume_max_per_order_lots=0)


def test_folga_de_margem_e_validada() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _settings(margin_free_buffer_pct=0)
    with pytest.raises(ValidationError):
        _settings(margin_free_buffer_pct=101)


def test_listas_derivadas_de_env() -> None:
    s = _settings(news_rss_feeds="a, b ,,c", twitter_cashtags="$EURUSD, $GBPUSD")
    assert s.rss_feed_list == ["a", "b", "c"]
    assert s.cashtag_list == ["$EURUSD", "$GBPUSD"]


def test_default_de_estrategias_e_so_technical() -> None:
    assert _settings().strategies_enabled_list == ["technical"]


def test_estrategias_habilitadas_derivada_de_env() -> None:
    s = _settings(strategies_enabled="technical, bbrsi ,,3macd")
    assert s.strategies_enabled_list == ["technical", "bbrsi", "3macd"]


def test_get_settings_e_memoizado() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()
    get_settings.cache_clear()


def test_default_de_timeframe_e_m5() -> None:
    assert _settings().timeframe == "M5"
    assert _settings().mt5_timeframe == TIMEFRAME_MAP["M5"]


@pytest.mark.parametrize(
    ("nome", "constante"),
    [("M1", 1), ("M5", 5), ("M15", 15), ("M30", 30), ("H1", 16_385)],
)
def test_mapeamento_nome_para_constante_do_mt5(nome: str, constante: int) -> None:
    assert TIMEFRAME_MAP[nome] == constante
    assert _settings(timeframe=nome).mt5_timeframe == constante


@pytest.mark.parametrize("valor", ["m5", "M2", "D1", "", "5"])
def test_timeframe_invalido_falha_no_boot(valor: str) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _settings(timeframe=valor)
