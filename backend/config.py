"""Configuração central do bot.

Regra inegociável (§1 do PRD): o modo real **nunca** é alcançado por acidente.
`effective_trading_mode` só retorna REAL quando duas condições independentes
são verdadeiras ao mesmo tempo. Qualquer ambiguidade resolve para DEMO.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(StrEnum):
    DEMO = "demo"
    REAL = "real"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Modo de operação ---------------------------------------------------
    # String crua de propósito: um valor inválido não pode explodir em runtime
    # nem — pior — ser interpretado como "real". Ver `effective_trading_mode`.
    trading_mode: str = "demo"
    real_trading_unlocked: bool = False

    # --- Banco / fila -------------------------------------------------------
    database_url: str = "postgresql+psycopg://forex:forex@127.0.0.1:5432/forex_bot"
    redis_url: str = "redis://127.0.0.1:6379/0"
    celery_broker_url: str = "redis://127.0.0.1:6379/1"
    celery_result_backend: str = "redis://127.0.0.1:6379/2"

    # --- MetaTrader 5 -------------------------------------------------------
    mt5_login: int | None = None
    mt5_password: str | None = None
    mt5_server: str | None = None
    mt5_terminal_path: str | None = None
    mt5_timeout_ms: int = 10_000

    # --- Regras de risco (§4) ----------------------------------------------
    max_risk_per_trade_pct: float = Field(default=1.0, gt=0, le=100)
    max_total_exposure_pct: float = Field(default=3.0, gt=0, le=100)
    max_daily_loss_pct: float = Field(default=5.0, gt=0, le=100)
    max_trades_per_day: int = Field(default=10, gt=0)
    atr_sl_multiplier: float = Field(default=2.0, gt=0)
    macro_blackout_minutes: int = Field(default=15, ge=0)

    # --- Regras FTMO --------------------------------------------------------
    ftmo_max_daily_loss_pct: float = Field(default=5.0, gt=0, le=100)
    ftmo_max_drawdown_pct: float = Field(default=10.0, gt=0, le=100)

    # --- Gates de promoção (§5) --------------------------------------------
    promotion_min_trades: int = Field(default=200, gt=0)
    promotion_min_win_rate: float = Field(default=0.55, gt=0, le=1)
    promotion_min_sharpe: float = Field(default=1.0)
    promotion_max_drawdown_pct: float = Field(default=10.0, gt=0, le=100)
    promotion_min_profit_factor: float = Field(default=1.3, gt=0)
    promotion_max_backtest_deviation_pct: float = Field(default=15.0, gt=0, le=100)

    # --- Coletores ----------------------------------------------------------
    news_rss_feeds: str = ""
    twitter_bearer_token: str | None = None
    twitter_cashtags: str = ""

    # --- NLP ----------------------------------------------------------------
    sentiment_model: str = "ProsusAI/finbert"
    sentiment_cache_ttl_seconds: int = 86_400

    # --- Observabilidade ----------------------------------------------------
    sentry_dsn: str | None = None
    log_level: str = "INFO"

    # ------------------------------------------------------------------ #
    @property
    def effective_trading_mode(self) -> TradingMode:
        """Modo realmente em vigor. Falha fechado.

        REAL exige, simultaneamente:
          1. TRADING_MODE exatamente "real" (case-insensitive, sem espaços);
          2. REAL_TRADING_UNLOCKED=true — destravado manualmente após os gates.

        Qualquer outra combinação, incluindo valores desconhecidos, é DEMO.
        """
        declared = self.trading_mode.strip().lower()
        if declared == TradingMode.REAL.value and self.real_trading_unlocked:
            return TradingMode.REAL
        return TradingMode.DEMO

    @property
    def is_real_trading(self) -> bool:
        return self.effective_trading_mode is TradingMode.REAL

    @property
    def rss_feed_list(self) -> list[str]:
        return [f.strip() for f in self.news_rss_feeds.split(",") if f.strip()]

    @property
    def cashtag_list(self) -> list[str]:
        return [t.strip() for t in self.twitter_cashtags.split(",") if t.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings memoizado. Use `get_settings.cache_clear()` em testes."""
    return Settings()
