"""Todo enum persiste o valor, não o nome do membro — verificado no Postgres real.

Este arquivo existe por causa de um bug que passou por toda a suíte: o kill
switch tentava gravar `KILL_SWITCH_TRIGGERED` num tipo que só aceita
`kill_switch_triggered`, e o bot morria no primeiro ciclo com
`InvalidTextRepresentation`.

Os testes em SQLite não pegam: lá o tipo vira um CHECK derivado do mesmo
mapeamento, então fica internamente consistente. Só o Postgres, com o tipo
vindo da migration, expõe a divergência — por isso estes testes são
`integration` e usam o schema real.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from backend.models import (
    AuditEventType,
    AuditLog,
    Direction,
    DocumentSource,
    Instrument,
    Outcome,
    Side,
    Signal,
    Trade,
    TradeStatus,
)
from backend.models.document import Document

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def pg_session(pg_engine: Engine, pg_url: str) -> Any:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "backend" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", pg_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    factory = sessionmaker(bind=pg_engine, expire_on_commit=False, future=True)
    with factory() as s:
        yield s
    command.downgrade(cfg, "base")


def _instrument(session: Session) -> Instrument:
    inst = Instrument(symbol="EURUSD")
    session.add(inst)
    session.commit()
    return inst


@pytest.mark.parametrize("evento", list(AuditEventType))
def test_todo_evento_de_auditoria_e_gravavel(pg_session: Session, evento: AuditEventType) -> None:
    """Um único evento não gravável derruba a trilha inteira em produção."""
    pg_session.add(AuditLog(event_type=evento, actor="teste", payload={}))
    pg_session.commit()

    gravado = pg_session.execute(
        text("SELECT event_type::text FROM audit_log ORDER BY id DESC LIMIT 1")
    ).scalar_one()
    assert gravado == evento.value


@pytest.mark.parametrize("direcao", list(Direction))
def test_toda_direcao_e_gravavel(pg_session: Session, direcao: Direction) -> None:
    inst = _instrument(pg_session)
    pg_session.add(
        Signal(
            instrument_id=inst.id,
            direction=direcao,
            confidence=0.5,
            fused_score=0.0,
            weight_version="v1",
            inputs={},
        )
    )
    pg_session.commit()

    gravado = pg_session.execute(
        text("SELECT direction::text FROM signals ORDER BY id DESC LIMIT 1")
    ).scalar_one()
    assert gravado == direcao.value


@pytest.mark.parametrize("side", list(Side))
@pytest.mark.parametrize("status", list(TradeStatus))
def test_todo_side_e_status_de_trade_sao_gravaveis(
    pg_session: Session, side: Side, status: TradeStatus
) -> None:
    inst = _instrument(pg_session)
    pg_session.add(
        Trade(
            client_request_id=f"req-{side.value}-{status.value}",
            instrument_id=inst.id,
            side=side,
            status=status,
            volume=0.01,
            stop_loss=1.08,
            trading_mode="demo",
        )
    )
    pg_session.commit()

    lado, situacao = pg_session.execute(
        text("SELECT side::text, status::text FROM trades ORDER BY id DESC LIMIT 1")
    ).one()
    assert (lado, situacao) == (side.value, status.value)


@pytest.mark.parametrize("origem", list(DocumentSource))
def test_toda_origem_de_documento_e_gravavel(pg_session: Session, origem: DocumentSource) -> None:
    pg_session.add(
        Document(
            source=origem,
            dedupe_hash=f"hash-{origem.value}",
            url="http://exemplo.invalid",
            title="titulo",
            content="corpo",
            origin="teste",
        )
    )
    pg_session.commit()

    gravado = pg_session.execute(
        text("SELECT source::text FROM documents ORDER BY id DESC LIMIT 1")
    ).scalar_one()
    assert gravado == origem.value


def test_outcome_grava_as_duas_direcoes(pg_session: Session) -> None:
    inst = _instrument(pg_session)
    trade = Trade(
        client_request_id="req-outcome",
        instrument_id=inst.id,
        side=Side.BUY,
        status=TradeStatus.CLOSED,
        volume=0.01,
        stop_loss=1.08,
        trading_mode="demo",
    )
    pg_session.add(trade)
    pg_session.commit()

    pg_session.add(
        Outcome(
            trade_id=trade.id,
            exit_price=1.09,
            pnl=1.0,
            pnl_pct=0.01,
            duration_seconds=60,
            predicted_direction=Direction.BUY,
            actual_direction=Direction.SELL,
            was_correct=False,
        )
    )
    pg_session.commit()

    previsto, real = pg_session.execute(
        text("SELECT predicted_direction::text, actual_direction::text FROM outcomes")
    ).one()
    assert (previsto, real) == (Direction.BUY.value, Direction.SELL.value)
