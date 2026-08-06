"""Configuração central do bot.

Regra inegociável (§1 do PRD): o modo real **nunca** é alcançado por acidente.
`effective_trading_mode` só retorna REAL quando duas condições independentes
são verdadeiras ao mesmo tempo. Qualquer ambiguidade resolve para DEMO.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(StrEnum):
    DEMO = "demo"
    REAL = "real"


# Constantes reais do pacote `MetaTrader5` (win32-only). Hard-coded aqui pelo
# mesmo motivo do `TIMEFRAME_M1` que existia em runner.py: ler um inteiro não
# justifica importar um pacote que não instala em Linux (CI).
TIMEFRAME_MAP: dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 16_385,
}


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

    # Timeframe dos candles analisados pelo runner. Nome, não inteiro mágico
    # do MT5 — ver `TIMEFRAME_MAP` e `mt5_timeframe`.
    timeframe: str = "M5"

    # --- Regras de risco (§4) ----------------------------------------------
    # Volume da ordem é derivado deste percentual (história 30) para
    # dimensionar o lote pelo risco — não é mais usado por `risk_manager` como
    # teto que bloqueia ordem (história 32: só a margem livre real do broker e
    # `volume_max_per_order_lots` limitam tamanho).
    max_risk_per_trade_pct: float = Field(default=0.5, gt=0, le=100)
    max_daily_loss_pct: float = Field(default=5.0, gt=0, le=100)
    atr_sl_multiplier: float = Field(default=2.0, gt=0)
    macro_blackout_minutes: int = Field(default=15, ge=0)

    # --- Perfil agressivo (história 32) -------------------------------------
    # A pedido do operador, os tetos artificiais de TAMANHO saem de cena:
    # risco por trade e exposição agregada deixam de bloquear ordem em
    # `risk_manager`. O único teto de tamanho que resta é este valor fixo de
    # lotes — nunca calculado a partir de risco — mais a margem livre real da
    # conta. Kill switch de perda diária e drawdown do pico continuam intactos
    # porque protegem a conta, não limitam o tamanho da ordem.
    volume_max_per_order_lots: float = Field(default=2.0, gt=0)
    # Folga sobre a margem livre real antes de calcular o volume máximo
    # permitido: nunca 100%, para sobrar espaço para o preço variar entre o
    # cálculo do volume e o envio da ordem ao broker.
    margin_free_buffer_pct: float = Field(default=95.0, gt=0, le=100)

    # --- Regras FTMO --------------------------------------------------------
    ftmo_max_daily_loss_pct: float = Field(default=5.0, gt=0, le=100)
    ftmo_max_drawdown_pct: float = Field(default=10.0, gt=0, le=100)

    # Drawdown acumulado medido a partir do maior equity já observado (não do
    # saldo inicial): lucro seguido de queda conta a partir do pico, que é o
    # cenário que de fato erode a conta. Independente do limite diário — os
    # dois são avaliados e qualquer um bloqueia (história 29).
    max_drawdown_from_peak_pct: float = Field(default=10.0, gt=0, le=100)

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
    sentiment_lookback_minutes: int = Field(default=60, gt=0)

    # Confiança mínima da fusão para o runner executar a ordem (história 27).
    # Default provisório: com 0 sinais gravados no momento desta calibração
    # (ver `scripts/calibrar_confianca.py` e progress.txt), não há distribuição
    # real para derivar um número — usa-se o mesmo limiar já calibrado do
    # `fuse_signals` (threshold=0.1 para a decisão categórica), como piso
    # mínimo defensável até existirem >=100 sinais reais para recalibrar.
    min_signal_confidence: float = Field(default=0.1, ge=0, le=1)

    # --- Observabilidade ----------------------------------------------------
    sentry_dsn: str | None = None
    log_level: str = "INFO"
    position_tracker_interval_seconds: int = Field(default=60, gt=0)

    @field_validator("timeframe")
    @classmethod
    def _valida_timeframe(cls, v: str) -> str:
        """Falha no boot com mensagem clara — ao contrário do trading mode,
        aqui não há valor seguro para degradar: um timeframe errado troca a
        série de candles inteira sem que ninguém perceba."""
        if v not in TIMEFRAME_MAP:
            opcoes = ", ".join(TIMEFRAME_MAP)
            raise ValueError(f"timeframe invalido: {v!r}. Opcoes validas: {opcoes}")
        return v

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
    def mt5_timeframe(self) -> int:
        """Constante MT5 correspondente ao `timeframe` configurado."""
        return TIMEFRAME_MAP[self.timeframe]

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
