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
    # Lote mínimo do broker para o símbolo (`symbol_info().volume_min`). `None`
    # quando a origem da ordem não o conhece — nesse caso a checagem é pulada,
    # nunca vira rejeição por um valor que ninguém informou.
    min_volume: float | None = None


class RiskManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def validate_order(
        self,
        request: OrderRequest,
        equity: float,
        daily_loss: float,
        account_drawdown: float = 0.0,
        kill_switch_active: bool = False,
    ) -> None:
        if kill_switch_active:
            raise KillSwitchError("Kill switch persistente está ativo")

        # Equity não positivo é situação degenerada: nenhum percentual calculado
        # a partir dela é confiável (viraria 0 ou negativo), então bloqueia aqui
        # de forma explícita em vez de confiar que os limites abaixo, avaliados
        # sobre uma equity inválida, acabem barrando por acidente.
        if equity <= 0:
            raise RiskValidationError(f"Equity não positivo ({equity}) bloqueia qualquer ordem")

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

        # Volume mínimo do broker: enviar menos que isso o broker recusa, mas o
        # objetivo é nunca deixar a ordem sair do risk manager nesse estado.
        if request.min_volume is not None and request.volume < request.min_volume:
            raise RiskValidationError(
                f"Volume {request.volume} abaixo do mínimo do broker de {request.min_volume}"
            )

        # 3. Teto duro de tamanho por ordem (história 32 — perfil agressivo)
        #
        # A pedido do operador, o risco por trade (1%) e a exposição agregada
        # (3%) deixaram de bloquear ordem: o único limite de TAMANHO que resta
        # é este valor fixo de lotes, nunca calculado a partir de risco. Kill
        # switch de perda diária e drawdown do pico (checados acima) continuam
        # de pé — protegem a conta, não limitam o tamanho da ordem.
        if request.volume > self.settings.volume_max_per_order_lots:
            raise RiskValidationError(
                f"Volume {request.volume} excede o teto de "
                f"{self.settings.volume_max_per_order_lots} lotes por ordem"
            )
