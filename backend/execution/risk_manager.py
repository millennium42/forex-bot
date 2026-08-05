from __future__ import annotations

from dataclasses import dataclass

from backend.config import Settings
from backend.models.enums import Side


class RiskValidationError(Exception):
    """Erro de validação de risco (ordem rejeitada)."""


class KillSwitchError(RiskValidationError):
    """Kill switch ativado."""


@dataclass(frozen=True, slots=True)
class OrderRequest:
    symbol: str
    side: Side
    volume: float
    price: float
    stop_loss: float | None
    take_profit: float | None


class RiskManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def validate_order(
        self,
        request: OrderRequest,
        equity: float,
        daily_loss: float,
        current_exposure: float,
        trade_monetary_risk: float,
        account_drawdown: float = 0.0,
        kill_switch_active: bool = False,
    ) -> None:
        if kill_switch_active:
            raise KillSwitchError("Kill switch persistente está ativo")

        # 1. Kill switch / Daily loss (5%)
        # Se perda diária + risco da nova ordem > max daily loss?
        # A regra de 5% de perda diária: se a perda diária atual já bate 5%, bloqueia.
        max_daily_loss = equity * (self.settings.max_daily_loss_pct / 100.0)
        if daily_loss >= max_daily_loss:
            raise KillSwitchError(
                f"Perda diária de {daily_loss} excedeu limite de {max_daily_loss}"
            )

        # FTMO daily loss
        ftmo_max_daily = equity * (self.settings.ftmo_max_daily_loss_pct / 100.0)
        if daily_loss >= ftmo_max_daily:
            raise KillSwitchError("Limite de perda diária FTMO excedido")

        # FTMO max drawdown
        ftmo_max_drawdown = equity * (self.settings.ftmo_max_drawdown_pct / 100.0)
        # Note: In a real scenario, this would be computed over the initial balance,
        # but to keep it simple and consistent with FTMO max drawdown representation,
        # we check against the allowed drawdown amount.
        if account_drawdown >= ftmo_max_drawdown:
            raise KillSwitchError(
                f"Drawdown de {account_drawdown} excedeu limite FTMO de {ftmo_max_drawdown}"
            )

        # 2. SL obrigatório
        if request.stop_loss is None or request.stop_loss <= 0:
            raise RiskValidationError("Ordem sem stop loss (SL) é rejeitada")

        # 3. 1% por trade
        max_risk_per_trade = equity * (self.settings.max_risk_per_trade_pct / 100.0)
        if trade_monetary_risk > max_risk_per_trade:
            raise RiskValidationError(
                f"Risco de {trade_monetary_risk} excede limite por trade de {max_risk_per_trade}"
            )

        # 4. 3% de exposição
        # Exposição total considerando a nova ordem
        new_exposure = current_exposure + trade_monetary_risk
        max_exposure = equity * (self.settings.max_total_exposure_pct / 100.0)
        if new_exposure > max_exposure:
            raise RiskValidationError(
                f"Exposição de {new_exposure} excede limite de {max_exposure}"
            )
