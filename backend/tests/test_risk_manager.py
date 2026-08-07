from __future__ import annotations

import pytest

from backend.config import Settings
from backend.execution.risk_manager import (
    KillSwitchError,
    OrderRequest,
    RiskManager,
    RiskValidationError,
)
from backend.models.enums import Side


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def risk_manager(settings: Settings) -> RiskManager:
    return RiskManager(settings)


@pytest.fixture
def valid_request() -> OrderRequest:
    return OrderRequest(
        symbol="EURUSD",
        side=Side.BUY,
        volume=0.1,
        price=1.1000,
        stop_loss=1.0900,
        take_profit=1.1200,
    )


def test_risk_manager_accepts_valid_order(
    risk_manager: RiskManager, valid_request: OrderRequest
) -> None:
    risk_manager.validate_order(
        request=valid_request,
        equity=10000.0,
        daily_loss=0.0,
    )
    # Não deve levantar exceção


def test_risk_manager_rejects_without_sl(
    risk_manager: RiskManager, valid_request: OrderRequest
) -> None:
    req = OrderRequest(
        symbol=valid_request.symbol,
        side=valid_request.side,
        volume=valid_request.volume,
        price=valid_request.price,
        stop_loss=None,
        take_profit=valid_request.take_profit,
    )
    with pytest.raises(RiskValidationError, match="sem stop loss"):
        risk_manager.validate_order(
            request=req,
            equity=10000.0,
            daily_loss=0.0,
        )


def test_risk_manager_kill_switch_daily_loss(
    risk_manager: RiskManager, valid_request: OrderRequest
) -> None:
    # Equity = 10000, max daily loss = 5% = 500
    with pytest.raises(KillSwitchError, match=r"Perda diária de 500\.0 excedeu limite"):
        risk_manager.validate_order(
            request=valid_request,
            equity=10000.0,
            daily_loss=500.0,
        )


def test_risk_manager_ftmo_max_drawdown(
    risk_manager: RiskManager, valid_request: OrderRequest
) -> None:
    # Equity = 10000, max drawdown = 10% = 1000
    with pytest.raises(KillSwitchError, match=r"Drawdown de 1000\.0 excedeu limite FTMO"):
        risk_manager.validate_order(
            request=valid_request,
            equity=10000.0,
            daily_loss=0.0,
            account_drawdown=1000.0,
        )


def test_risk_manager_ftmo_daily_loss(
    risk_manager: RiskManager, valid_request: OrderRequest
) -> None:
    # Equity = 10000, max daily loss FTMO = 5% = 500
    # To test specifically the FTMO daily loss, we can change max_daily_loss_pct to something higher
    # to avoid the general KillSwitchError triggering first.
    risk_manager.settings.max_daily_loss_pct = 10.0
    with pytest.raises(KillSwitchError, match="Limite de perda diária FTMO excedido"):
        risk_manager.validate_order(
            request=valid_request,
            equity=10000.0,
            daily_loss=500.0,
        )


def test_risk_manager_rejects_volume_below_broker_minimum(
    risk_manager: RiskManager, valid_request: OrderRequest
) -> None:
    req = OrderRequest(
        symbol=valid_request.symbol,
        side=valid_request.side,
        volume=0.01,
        price=valid_request.price,
        stop_loss=valid_request.stop_loss,
        take_profit=valid_request.take_profit,
        min_volume=0.1,
    )
    with pytest.raises(RiskValidationError, match="abaixo do mínimo do broker"):
        risk_manager.validate_order(
            request=req,
            equity=10000.0,
            daily_loss=0.0,
        )


def test_risk_manager_accepts_volume_equal_to_broker_minimum(
    risk_manager: RiskManager, valid_request: OrderRequest
) -> None:
    req = OrderRequest(
        symbol=valid_request.symbol,
        side=valid_request.side,
        volume=0.1,
        price=valid_request.price,
        stop_loss=valid_request.stop_loss,
        take_profit=valid_request.take_profit,
        min_volume=0.1,
    )
    risk_manager.validate_order(
        request=req,
        equity=10000.0,
        daily_loss=0.0,
    )
    # Não deve levantar exceção


def test_risk_manager_kill_switch_active(
    risk_manager: RiskManager, valid_request: OrderRequest
) -> None:
    with pytest.raises(KillSwitchError, match="Kill switch persistente está ativo"):
        risk_manager.validate_order(
            request=valid_request,
            equity=10000.0,
            daily_loss=0.0,
            kill_switch_active=True,
        )


def test_risk_manager_rejects_non_positive_equity(
    risk_manager: RiskManager, valid_request: OrderRequest
) -> None:
    with pytest.raises(RiskValidationError, match="Equity não positivo"):
        risk_manager.validate_order(
            request=valid_request,
            equity=0.0,
            daily_loss=0.0,
        )


def test_risk_manager_rejects_negative_equity(
    risk_manager: RiskManager, valid_request: OrderRequest
) -> None:
    with pytest.raises(RiskValidationError, match="Equity não positivo"):
        risk_manager.validate_order(
            request=valid_request,
            equity=-500.0,
            daily_loss=0.0,
        )


# -- história 32: perfil agressivo — teto de tamanho vira só VOLUME_MAX_PER_ORDER_LOTS ---
#
# Risco por trade (1%) e exposição agregada (3%) bloqueavam ordem antes desta
# história (ver git history de test_risk_manager.py). A pedido do operador,
# esses tetos artificiais de TAMANHO saíram do risk_manager: o único limite de
# tamanho que resta aqui é `volume_max_per_order_lots` (a margem livre real é
# aplicada no runner, que é quem conhece leverage/margin_free da conta).


def test_risk_manager_accepts_order_even_with_equity_too_small_for_old_risk_cap(
    risk_manager: RiskManager, valid_request: OrderRequest
) -> None:
    """`validate_order` não recebe mais `trade_monetary_risk`/`current_exposure_monetary`.

    Antes desta história, uma equity de US$100 (1% de risco = US$1) rejeitava
    qualquer trade cujo risco monetário não coubesse nesse valor. Sem esses
    dois parâmetros, o único fator de equity que ainda pode bloquear é kill
    switch/drawdown — uma ordem válida (SL presente, dentro do teto de
    volume) passa independente do tamanho da equity.
    """
    risk_manager.validate_order(
        request=valid_request,
        equity=100.0,
        daily_loss=0.0,
    )
    # Não deve levantar exceção.


def test_risk_manager_rejects_volume_above_max_per_order(
    risk_manager: RiskManager, valid_request: OrderRequest
) -> None:
    req = OrderRequest(
        symbol=valid_request.symbol,
        side=valid_request.side,
        volume=2.01,
        price=valid_request.price,
        stop_loss=valid_request.stop_loss,
        take_profit=valid_request.take_profit,
        max_volume=2.0,
    )
    with pytest.raises(RiskValidationError, match=r"excede o teto de 2\.0 lotes por ordem"):
        risk_manager.validate_order(
            request=req,
            equity=1_000_000.0,
            daily_loss=0.0,
        )


def test_risk_manager_accepts_volume_exactly_at_max_per_order(
    risk_manager: RiskManager, valid_request: OrderRequest
) -> None:
    req = OrderRequest(
        symbol=valid_request.symbol,
        side=valid_request.side,
        volume=2.0,
        price=valid_request.price,
        stop_loss=valid_request.stop_loss,
        take_profit=valid_request.take_profit,
        max_volume=2.0,
    )
    risk_manager.validate_order(
        request=req,
        equity=1_000_000.0,
        daily_loss=0.0,
    )
    # Não deve levantar exceção — na fronteira exata, aceita.


def test_risk_manager_respects_configured_volume_max_per_order(
    risk_manager: RiskManager, valid_request: OrderRequest
) -> None:
    """O teto vem no `OrderRequest`, não mais da config (história 46).

    Deixou de ser constante porque passou a variar com a confiança do sinal;
    quem calcula é o runner, mas a checagem continua no gate.
    """
    req = OrderRequest(
        symbol=valid_request.symbol,
        side=valid_request.side,
        volume=0.6,
        price=valid_request.price,
        stop_loss=valid_request.stop_loss,
        take_profit=valid_request.take_profit,
        max_volume=0.5,
    )
    with pytest.raises(RiskValidationError, match=r"excede o teto de 0\.5 lotes por ordem"):
        risk_manager.validate_order(
            request=req,
            equity=1_000_000.0,
            daily_loss=0.0,
        )


def test_risk_manager_sem_max_volume_nao_bloqueia(
    risk_manager: RiskManager, valid_request: OrderRequest
) -> None:
    """`max_volume=None` pula a checagem — mesma regra de `min_volume`.

    Origem de ordem que não conhece o teto não pode ser rejeitada por um
    valor que ninguém informou.
    """
    req = OrderRequest(
        symbol=valid_request.symbol,
        side=valid_request.side,
        volume=50.0,
        price=valid_request.price,
        stop_loss=valid_request.stop_loss,
        take_profit=valid_request.take_profit,
        max_volume=None,
    )
    risk_manager.validate_order(request=req, equity=1_000_000.0, daily_loss=0.0)
