"""Auditoria contínua do banco (história 37).

Acompanha o que o bot está gravando de fato: sinais avaliados, trades
executados, e o motivo de cada bloqueio (confiança insuficiente, cooldown,
margem, kill switch, drawdown, rejeição do risk manager ou do broker) — tudo
lido do `audit_log` e das tabelas que o runner já grava, nunca inventado.

Sem `--follow`, mostra o estado atual da conta e um resumo da janela recente
(`--lookback-minutos`) e sai. Com `--follow`, faz polling a cada
`--intervalo` segundos e imprime só o que é novo desde a rodada anterior.

Uso:
    uv run python scripts/auditar_db.py
    uv run python scripts/auditar_db.py --follow
    uv run python scripts/auditar_db.py --follow --intervalo 10
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.collection.mt5_client import (
    MT5Client,
    MT5ConnectionError,
    MT5ModeMismatchError,
)
from backend.db import session_scope
from backend.execution.drawdown_guard import get_peak_equity, is_drawdown_block_active
from backend.execution.kill_switch import is_kill_switch_active
from backend.models.audit_log import AuditLog
from backend.models.enums import AuditEventType, TradeStatus
from backend.models.signal import Signal
from backend.models.trade import Trade

INTERVALO_PADRAO_SEGUNDOS = 5
LOOKBACK_PADRAO_MINUTOS = 15

# Eventos que representam um bloqueio (ordem que não saiu ou trava do sistema).
# Só estes viram linha de "motivo" no resumo — os demais (ORDER_PLACED,
# ORDER_MODIFIED, ORDER_CLOSED, EQUITY_PEAK_UPDATED, ...) não são bloqueio.
EVENTOS_DE_BLOQUEIO = {
    AuditEventType.ORDER_BLOCKED,
    AuditEventType.ORDER_REJECTED,
    AuditEventType.KILL_SWITCH_TRIGGERED,
    AuditEventType.DRAWDOWN_LIMIT_TRIGGERED,
}

# Chaves de payload conhecidas e já auditadas como seguras (§6: sem PII, sem
# credencial). Nunca imprimimos o payload cru — só o que já sabemos ser
# metadado de trading (motivo, símbolo, direção, números de risco).
CHAVES_PAYLOAD_SEGURAS = ("motivo", "reason", "symbol", "direcao", "confianca", "limiar")


def _motivo_legivel(evento: AuditLog) -> str:
    """Descrição curta do bloqueio, extraída só das chaves conhecidas do payload."""
    payload = evento.payload or {}
    partes = [f"{chave}={payload[chave]}" for chave in CHAVES_PAYLOAD_SEGURAS if chave in payload]
    detalhe = ", ".join(partes) if partes else evento.event_type.value
    return f"{evento.event_type.value}: {detalhe}"


def _maiores_ids(session: Session) -> tuple[int, int, int]:
    """Maior id já gravado em cada tabela observada — ponto de partida do `--follow`."""
    maior_signal = session.execute(select(func.max(Signal.id))).scalar() or 0
    maior_trade = session.execute(select(func.max(Trade.id))).scalar() or 0
    maior_audit = session.execute(select(func.max(AuditLog.id))).scalar() or 0
    return maior_signal, maior_trade, maior_audit


def _imprimir_resumo(sinais: list[Signal], trades: list[Trade], eventos: list[AuditLog]) -> None:
    """Resumo de uma janela: sinais avaliados, executados e bloqueados (com motivo)."""
    bloqueios = [e for e in eventos if e.event_type in EVENTOS_DE_BLOQUEIO]
    executados = [t for t in trades if t.status in (TradeStatus.PENDING, TradeStatus.OPEN)]

    if not sinais and not trades and not eventos:
        return

    for s in sinais:
        print(f"  sinal #{s.id}: {s.direction.value} confianca={s.confidence:.3f}")
    for t in trades:
        print(f"  trade #{t.id}: {t.side.value} vol={t.volume} status={t.status.value}")
    for e in bloqueios:
        print(f"  bloqueio: {_motivo_legivel(e)}")

    print(
        f"resumo: {len(sinais)} sinais avaliados, {len(executados)} executados, "
        f"{len(bloqueios)} bloqueados"
    )


def _janela_recente(
    session: Session, desde: datetime
) -> tuple[list[Signal], list[Trade], list[AuditLog]]:
    sinais = list(
        session.execute(
            select(Signal).where(Signal.created_at >= desde).order_by(Signal.id)
        ).scalars()
    )
    trades = list(
        session.execute(select(Trade).where(Trade.created_at >= desde).order_by(Trade.id)).scalars()
    )
    eventos = list(
        session.execute(
            select(AuditLog).where(AuditLog.created_at >= desde).order_by(AuditLog.id)
        ).scalars()
    )
    return sinais, trades, eventos


def _novidades(
    session: Session, desde_signal_id: int, desde_trade_id: int, desde_audit_id: int
) -> tuple[list[Signal], list[Trade], list[AuditLog]]:
    sinais = list(
        session.execute(
            select(Signal).where(Signal.id > desde_signal_id).order_by(Signal.id)
        ).scalars()
    )
    trades = list(
        session.execute(select(Trade).where(Trade.id > desde_trade_id).order_by(Trade.id)).scalars()
    )
    eventos = list(
        session.execute(
            select(AuditLog).where(AuditLog.id > desde_audit_id).order_by(AuditLog.id)
        ).scalars()
    )
    return sinais, trades, eventos


def _imprimir_estado_atual(session: Session) -> None:
    """Equity, pico, drawdown, kill switch e posições abertas — nunca inventado.

    Segue o mesmo padrão do endpoint `/system/account`: se o MT5 não responde,
    mostra "indisponível" em vez de um número forjado.
    """
    pico = get_peak_equity(session)
    kill_switch_ativo = is_kill_switch_active(session)
    drawdown_bloqueado = is_drawdown_block_active(session)

    equity: float | None = None
    posicoes: list[Any] = []
    conectado = False
    detalhe: str | None = None
    try:
        with MT5Client() as client:
            equity = client.get_account_info().equity
            posicoes = client.get_positions()
            conectado = True
    except (MT5ConnectionError, MT5ModeMismatchError) as exc:
        detalhe = str(exc)

    drawdown_pct: float | None = None
    if pico is not None and equity is not None and pico > 0:
        drawdown_pct = max(0.0, (pico - equity) / pico * 100.0)

    print("--- estado atual ---")
    print(f"MT5 conectado: {conectado}" + (f" ({detalhe})" if detalhe else ""))
    print(f"equity: {equity if equity is not None else 'indisponivel'}")
    print(f"pico de equity: {pico if pico is not None else 'nenhum registrado ainda'}")
    drawdown_txt = f"{drawdown_pct:.2f}%" if drawdown_pct is not None else "indisponivel"
    print(f"drawdown do pico: {drawdown_txt}")
    print(f"kill switch: {'ATIVO' if kill_switch_ativo else 'inativo'}")
    print(f"bloqueio por drawdown: {'ATIVO' if drawdown_bloqueado else 'inativo'}")
    print(f"posicoes abertas: {len(posicoes)}")
    for p in posicoes:
        print(f"  {p.symbol}: vol={p.volume} pnl={p.profit:.2f}")
    print("--------------------")


def _rodada_unica(lookback_minutos: int) -> None:
    with session_scope() as session:
        _imprimir_estado_atual(session)
        desde = datetime.now(UTC) - timedelta(minutes=lookback_minutos)
        sinais, trades, eventos = _janela_recente(session, desde)
        print(f"--- últimos {lookback_minutos} minutos ---")
        _imprimir_resumo(sinais, trades, eventos)


def _seguir(intervalo_segundos: int) -> None:
    with session_scope() as session:
        _imprimir_estado_atual(session)
        desde_signal, desde_trade, desde_audit = _maiores_ids(session)

    print(f"acompanhando novidades a cada {intervalo_segundos}s (Ctrl+C para sair)...")
    try:
        while True:
            time.sleep(intervalo_segundos)
            with session_scope() as session:
                sinais, trades, eventos = _novidades(
                    session, desde_signal, desde_trade, desde_audit
                )
                if sinais or trades or eventos:
                    _imprimir_estado_atual(session)
                    _imprimir_resumo(sinais, trades, eventos)
                if sinais:
                    desde_signal = sinais[-1].id
                if trades:
                    desde_trade = trades[-1].id
                if eventos:
                    desde_audit = eventos[-1].id
    except KeyboardInterrupt:
        print("encerrado.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Auditoria contínua do banco do forex-bot")
    parser.add_argument(
        "--follow", action="store_true", help="acompanha novidades em tempo real (polling)"
    )
    parser.add_argument(
        "--intervalo",
        type=int,
        default=INTERVALO_PADRAO_SEGUNDOS,
        help=f"segundos entre polls no modo --follow (padrão {INTERVALO_PADRAO_SEGUNDOS})",
    )
    parser.add_argument(
        "--lookback-minutos",
        type=int,
        default=LOOKBACK_PADRAO_MINUTOS,
        help=f"janela do resumo no modo sem --follow (padrão {LOOKBACK_PADRAO_MINUTOS})",
    )
    args = parser.parse_args()

    # Sem isso, stdout redirecionado (pipe/arquivo) fica bufferizado até o
    # processo terminar — "--follow" deixaria de imprimir em tempo real.
    sys.stdout.reconfigure(line_buffering=True)

    if args.follow:
        _seguir(args.intervalo)
    else:
        _rodada_unica(args.lookback_minutos)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
