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
    # 1% de 10000 = 100
    # Exposicao total limite = 300
    risk_manager.validate_order(
        request=valid_request,
        equity=10000.0,
        daily_loss=0.0,
        current_exposure=100.0,
        trade_monetary_risk=50.0,
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
            current_exposure=0.0,
            trade_monetary_risk=50.0,
        )


def test_risk_manager_rejects_exceeding_max_risk_per_trade(
    risk_manager: RiskManager, valid_request: OrderRequest
) -> None:
    # Equity = 10000, max risk = 1% = 100
    with pytest.raises(RiskValidationError, match="excede limite por trade"):
        risk_manager.validate_order(
            request=valid_request,
            equity=10000.0,
            daily_loss=0.0,
            current_exposure=0.0,
            trade_monetary_risk=101.0,
        )


def test_risk_manager_rejects_exceeding_total_exposure(
    risk_manager: RiskManager, valid_request: OrderRequest
) -> None:
    # Equity = 10000, max exposure = 3% = 300
    with pytest.raises(RiskValidationError, match="excede limite"):
        risk_manager.validate_order(
            request=valid_request,
            equity=10000.0,
            daily_loss=0.0,
            current_exposure=260.0,
            trade_monetary_risk=50.0,
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
            current_exposure=0.0,
            trade_monetary_risk=50.0,
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
            current_exposure=0.0,
            trade_monetary_risk=50.0,
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
            current_exposure=0.0,
            trade_monetary_risk=50.0,
        )
