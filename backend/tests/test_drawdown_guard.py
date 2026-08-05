"""História 29 — pico de equity persistido e bloqueio por drawdown acumulado."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.main import app
from backend.execution.drawdown_guard import (
    get_peak_equity,
    is_drawdown_block_active,
    record_equity,
    reset_drawdown_block,
    trigger_drawdown_block,
)
from backend.models.audit_log import AuditLog
from backend.models.enums import AuditEventType


def test_get_peak_equity_sem_evento_e_none(session: Session) -> None:
    assert get_peak_equity(session) is None


def test_record_equity_grava_primeiro_pico(session: Session) -> None:
    peak = record_equity(session, 100_000.0)

    assert peak == 100_000.0
    assert get_peak_equity(session) == 100_000.0


def test_record_equity_atualiza_so_quando_supera_o_pico(session: Session) -> None:
    record_equity(session, 100_000.0)
    peak = record_equity(session, 90_000.0)  # queda: não é novo pico

    assert peak == 100_000.0
    assert get_peak_equity(session) == 100_000.0

    peak = record_equity(session, 110_000.0)  # novo pico
    assert peak == 110_000.0
    assert get_peak_equity(session) == 110_000.0


def test_record_equity_nao_cria_evento_quando_pico_nao_muda(session: Session) -> None:
    record_equity(session, 100_000.0)
    record_equity(session, 100_000.0)
    record_equity(session, 95_000.0)

    eventos = session.scalars(
        select(AuditLog).where(AuditLog.event_type == AuditEventType.EQUITY_PEAK_UPDATED)
    ).all()
    assert len(eventos) == 1


def test_pico_persiste_reiniciar_processo_nao_zera(session: Session) -> None:
    """O pico não vive em memória do runner: uma nova instância lê o mesmo valor do banco."""
    record_equity(session, 100_000.0)
    record_equity(session, 120_000.0)

    # Simula um "processo novo": nenhuma referência ao valor anterior, só a sessão.
    assert get_peak_equity(session) == 120_000.0


def test_drawdown_block_db_logic(session: Session) -> None:
    assert not is_drawdown_block_active(session)

    trigger_drawdown_block(session, reason="Teste")
    assert is_drawdown_block_active(session)

    # Disparar de novo com o bloqueio já ativo não duplica o evento.
    trigger_drawdown_block(session, reason="Teste 2")
    eventos = session.scalars(
        select(AuditLog).where(AuditLog.event_type == AuditEventType.DRAWDOWN_LIMIT_TRIGGERED)
    ).all()
    assert len(eventos) == 1

    reset_drawdown_block(session, actor="admin")
    assert not is_drawdown_block_active(session)

    # Resetar de novo sem estar ativo não duplica o evento.
    reset_drawdown_block(session, actor="admin")
    eventos_reset = session.scalars(
        select(AuditLog).where(AuditLog.event_type == AuditEventType.DRAWDOWN_LIMIT_RESET)
    ).all()
    assert len(eventos_reset) == 1


def test_drawdown_block_endpoints() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from backend.api.dependencies import get_db
    from backend.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session_local = sessionmaker(bind=engine)

    from collections.abc import Iterator

    def override_get_db() -> Iterator[Session]:
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    client = TestClient(app)

    resp = client.get("/system/drawdown-block")
    assert resp.status_code == 200
    assert resp.json() == {"active": False}

    db = testing_session_local()
    trigger_drawdown_block(db, reason="Drawdown de 12% a partir do pico")
    db.close()

    resp = client.get("/system/drawdown-block")
    assert resp.json() == {"active": True}

    resp = client.post(
        "/system/drawdown-block/reset",
        json={"actor": "admin", "reason": "Revisado manualmente"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"active": False}

    resp = client.get("/system/drawdown-block")
    assert resp.json() == {"active": False}

    app.dependency_overrides.clear()
