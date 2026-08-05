"""Modelos SQLAlchemy.

Importar deste módulo garante que todas as tabelas estejam registradas em
`Base.metadata` — o que o Alembic e o `create_all` dos testes esperam.
"""

from backend.models.audit_log import AuditLog
from backend.models.base import Base
from backend.models.document import Document
from backend.models.enums import AuditEventType, Direction, DocumentSource, Side, TradeStatus
from backend.models.instrument import Instrument
from backend.models.outcome import Outcome
from backend.models.signal import Signal
from backend.models.trade import Trade

__all__ = [
    "AuditEventType",
    "AuditLog",
    "Base",
    "Direction",
    "Document",
    "DocumentSource",
    "Instrument",
    "Outcome",
    "Side",
    "Signal",
    "Trade",
    "TradeStatus",
]
