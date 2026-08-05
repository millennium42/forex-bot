"""História 1 — a config falha fechado para o modo demo."""

from __future__ import annotations

import pytest

from backend.config import Settings, TradingMode, get_settings


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


def test_listas_derivadas_de_env() -> None:
    s = _settings(news_rss_feeds="a, b ,,c", twitter_cashtags="$EURUSD, $GBPUSD")
    assert s.rss_feed_list == ["a", "b", "c"]
    assert s.cashtag_list == ["$EURUSD", "$GBPUSD"]


def test_get_settings_e_memoizado() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()
    get_settings.cache_clear()
