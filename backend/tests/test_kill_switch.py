from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.main import app
from backend.execution.kill_switch import (
    is_kill_switch_active,
    reset_kill_switch,
    trigger_kill_switch,
)
from backend.models.audit_log import AuditLog
from backend.models.enums import AuditEventType


def test_kill_switch_db_logic(session: Session) -> None:
    # Por padrão, está inativo
    assert not is_kill_switch_active(session)

    # Dispara
    trigger_kill_switch(session, reason="Teste", actor="system")
    assert is_kill_switch_active(session)

    # Disparar de novo não deve criar evento duplicado se já está ativo
    trigger_kill_switch(session, reason="Teste 2", actor="system")
    # Actually wait, `select(func.count()).select_from(AuditLog)` is better.
    # We can just check the number of KILL_SWITCH_TRIGGERED events
    events = session.scalars(select(AuditLog).where(AuditLog.event_type == AuditEventType.KILL_SWITCH_TRIGGERED)).all()  # noqa: E501
    assert len(events) == 1

    # Reseta
    reset_kill_switch(session, actor="admin")
    assert not is_kill_switch_active(session)

    events_reset = session.scalars(select(AuditLog).where(AuditLog.event_type == AuditEventType.KILL_SWITCH_RESET)).all()  # noqa: E501
    assert len(events_reset) == 1

    # Resetar de novo não deve criar evento duplicado
    reset_kill_switch(session, actor="admin")
    events_reset2 = session.scalars(select(AuditLog).where(AuditLog.event_type == AuditEventType.KILL_SWITCH_RESET)).all()  # noqa: E501
    assert len(events_reset2) == 1


def test_kill_switch_endpoints() -> None:
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

    # Status inicial
    resp = client.get("/system/kill-switch")
    assert resp.status_code == 200
    assert resp.json() == {"active": False}

    # Trigger manual
    resp = client.post(
        "/system/kill-switch/trigger",
        json={"actor": "user_1", "reason": "Mercado estranho"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"active": True}

    # Status mudou
    resp = client.get("/system/kill-switch")
    assert resp.json() == {"active": True}

    # Reset manual
    resp = client.post(
        "/system/kill-switch/reset",
        json={"actor": "admin", "reason": "Tudo ok agora"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"active": False}

    app.dependency_overrides.clear()
