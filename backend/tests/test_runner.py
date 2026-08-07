"""História 21 — cobertura do motor autônomo (BotRunner).

O runner é onde o pipeline inteiro se encontra, então os testes aqui garantem
que nenhum número usado pelo risk manager é inventado: kill switch bloqueia o
ciclo inteiro, ATR inválido não vira ordem, o stop é derivado da volatilidade
medida (não constante), a idempotência tem granularidade de minuto, a perda do
dia vem só de outcomes negativos de hoje, e falha de MT5 encerra o ciclo em
vez de pular o símbolo.
"""

from __future__ import annotations

import contextlib
import itertools
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.analysis.signal_fusion import DEFAULT_WEIGHTS, FusedSignal
from backend.analysis.strategy import StrategySignal
from backend.collection.mt5_client import MT5Client, MT5ConnectionError, Position
from backend.config import Settings
from backend.execution import runner as runner_module
from backend.execution.drawdown_guard import (
    is_drawdown_block_active,
    reset_drawdown_block,
)
from backend.execution.kill_switch import trigger_kill_switch
from backend.execution.order_manager import OrderManager
from backend.execution.risk_manager import RiskManager
from backend.execution.runner import BotRunner
from backend.learning.outcome_recorder import record_outcome
from backend.models.audit_log import AuditLog
from backend.models.enums import AuditEventType, Direction, Side, TradeStatus
from backend.models.instrument import Instrument
from backend.models.outcome import Outcome
from backend.models.signal import Signal
from backend.models.trade import Trade

BUY_SIGNAL = FusedSignal(direction=Direction.BUY, score=0.5, confidence=0.8, weight_version="v1.0")
SELL_SIGNAL = FusedSignal(
    direction=Direction.SELL, score=-0.5, confidence=0.8, weight_version="v1.0"
)


def _instrumento(session: Session, client: MT5Client, symbol: str = "EURUSD") -> Instrument:
    return BotRunner._get_or_create_instrument(session, symbol, client)


def _motivos_bloqueio(session: Session) -> list[str]:
    """Motivos gravados em `ORDER_BLOCKED` (história 36), na ordem em que ocorreram."""
    eventos = (
        session.execute(
            select(AuditLog)
            .where(AuditLog.event_type == AuditEventType.ORDER_BLOCKED)
            .order_by(AuditLog.id)
        )
        .scalars()
        .all()
    )
    return [str(e.payload["motivo"]) for e in eventos]


class _FixedDatetime(datetime):
    """`datetime.now()` congelado, para testar janelas de idempotência sem flake."""

    @classmethod
    def now(cls, tz: Any = None) -> _FixedDatetime:
        return cls(2026, 1, 1, 10, 30, 45, tzinfo=UTC)


class FakeTerminal:
    """Dublê do módulo MetaTrader5, no mesmo formato usado em test_mt5_client.py."""

    def __init__(
        self,
        *,
        positions: tuple[Any, ...] = (),
        tick: Any = None,
        retcode: int | None = 10009,
        equity: float = 100_000.0,
        margin_free: float | None = None,
        candle_rows: list[dict[str, float]] | None = None,
        volume_min: float = 0.01,
        contract_size: float = 100_000.0,
    ) -> None:
        self.positions = positions
        self._tick = tick or SimpleNamespace(time=1_700_000_000, bid=1.0850, ask=1.0852)
        self.retcode = retcode
        self.equity = equity
        # Distinto de `equity` só quando o teste precisa isolar o gate de
        # margem livre do gate de risco por trade (história 36) — ambos usam
        # o mesmo campo da conta real, mas dependem de fórmulas diferentes.
        self.margin_free = margin_free if margin_free is not None else equity
        self.candle_rows = candle_rows
        self.volume_min = volume_min
        self.contract_size = contract_size
        self.last_order: dict[str, Any] | None = None

    def initialize(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    def login(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    def shutdown(self) -> None:
        pass

    def last_error(self) -> tuple[int, str]:
        return (1, "fake error")

    def account_info(self) -> Any:
        return SimpleNamespace(
            login=1,
            server="Demo",
            currency="USD",
            balance=self.equity,
            equity=self.equity,
            margin=0.0,
            margin_free=self.margin_free,
            leverage=100,
            trade_mode=0,
        )

    def symbol_info_tick(self, _symbol: str) -> Any:
        return self._tick

    def symbol_info(self, _symbol: str) -> Any:
        return SimpleNamespace(volume_min=self.volume_min, trade_contract_size=self.contract_size)

    def symbol_select(self, _symbol: str, _enable: bool) -> bool:
        return True

    def positions_get(self) -> Any:
        return self.positions

    def history_deals_get(self, *args: Any, **kwargs: Any) -> Any:
        return ()

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start_pos: int, count: int) -> Any:
        return self.candle_rows

    def order_send(self, request: dict[str, Any]) -> Any:
        self.last_order = request
        if self.retcode is None:
            return None

        class Result:
            retcode = self.retcode
            order = 123
            deal = 456
            price = request.get("price", 1.1)
            volume = request.get("volume", 0.01)
            comment = ""

        return Result()

    @property
    def TRADE_ACTION_DEAL(self) -> int:  # noqa: N802
        return 1

    @property
    def ORDER_TYPE_BUY(self) -> int:  # noqa: N802
        return 0

    @property
    def ORDER_TYPE_SELL(self) -> int:  # noqa: N802
        return 1

    @property
    def TRADE_RETCODE_DONE(self) -> int:  # noqa: N802
        return 10009


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"trading_mode": "demo", "real_trading_unlocked": False}
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def _client(terminal: FakeTerminal | None = None) -> MT5Client:
    client = MT5Client(terminal=terminal or FakeTerminal(), settings=_settings())
    client._connected = True
    return client


def _runner(symbols: list[str] | None = None, **settings_overrides: object) -> BotRunner:
    return BotRunner(symbols or ["EURUSD"], settings=_settings(**settings_overrides))


# -- kill switch impede o ciclo inteiro (AC1) -------------------------------


def test_run_cycle_kill_switch_ativo_impede_o_ciclo_inteiro(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    trigger_kill_switch(session, reason="teste", actor="tester")

    processed: list[str] = []
    runner = _runner(["EURUSD", "GBPUSD"])
    monkeypatch.setattr(runner, "_process_symbol", lambda *a, **kw: processed.append(a[0]))

    runner.run_cycle(_client(), session)

    assert processed == []


# -- _drawdown_pct: unitário -------------------------------------------------


def test_drawdown_pct_pico_nao_positivo_nao_quebra() -> None:
    assert BotRunner._drawdown_pct(0.0, -10.0) == 0.0
    assert BotRunner._drawdown_pct(-5.0, -10.0) == 0.0


def test_drawdown_pct_novo_pico_nao_e_negativo() -> None:
    assert BotRunner._drawdown_pct(100.0, 110.0) == 0.0


# -- história 29: drawdown acumulado do pico bloqueia o ciclo ----------------


def test_run_cycle_drawdown_conta_a_partir_do_pico_nao_do_inicio(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lucro de 10% seguido de queda de 10% é drawdown do PICO, não do início.

    Partindo de 100k: sobe para 110k (novo pico) e cai para 99k. Do início
    (100k) isso seria só 1% de queda — não bateria um limiar de 10%. Do pico
    (110k) é exatamente 10%, e deve bloquear.
    """
    processed: list[str] = []
    runner = _runner(["EURUSD"], max_drawdown_from_peak_pct=10.0)
    monkeypatch.setattr(runner, "_process_symbol", lambda *a, **kw: processed.append(a[0]))
    terminal = FakeTerminal(equity=100_000.0)
    client = _client(terminal)

    runner.run_cycle(client, session)
    assert not is_drawdown_block_active(session)

    terminal.equity = 110_000.0
    runner.run_cycle(client, session)
    assert not is_drawdown_block_active(session)

    terminal.equity = 99_000.0
    runner.run_cycle(client, session)

    assert is_drawdown_block_active(session)
    assert processed == ["EURUSD", "EURUSD"]  # o 3º ciclo não chegou a processar símbolo


def test_run_cycle_drawdown_bloqueado_impede_ciclos_seguintes_ate_reset(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    processed: list[str] = []
    runner = _runner(["EURUSD"], max_drawdown_from_peak_pct=10.0)
    monkeypatch.setattr(runner, "_process_symbol", lambda *a, **kw: processed.append(a[0]))
    terminal = FakeTerminal(equity=100_000.0)
    client = _client(terminal)

    runner.run_cycle(client, session)
    terminal.equity = 89_000.0
    runner.run_cycle(client, session)
    assert is_drawdown_block_active(session)

    terminal.equity = 100_000.0  # equity já recuperou...
    runner.run_cycle(client, session)
    # ...mas o ciclo continua bloqueado sem reset manual: só o 1º ciclo (antes
    # do drawdown) processou símbolo; o 2º foi bloqueado pelo drawdown e o 3º
    # continua bloqueado mesmo com o equity recuperado.
    assert processed == ["EURUSD"]

    reset_drawdown_block(session, actor="admin")
    runner.run_cycle(client, session)
    assert processed == ["EURUSD", "EURUSD"]


def test_run_cycle_sem_atingir_limiar_de_drawdown_nao_bloqueia(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    processed: list[str] = []
    runner = _runner(["EURUSD"], max_drawdown_from_peak_pct=10.0)
    monkeypatch.setattr(runner, "_process_symbol", lambda *a, **kw: processed.append(a[0]))
    terminal = FakeTerminal(equity=100_000.0)
    client = _client(terminal)

    runner.run_cycle(client, session)
    terminal.equity = 95_000.0  # 5% abaixo do pico: não bate os 10%
    runner.run_cycle(client, session)

    assert not is_drawdown_block_active(session)
    assert processed == ["EURUSD", "EURUSD"]


# -- ATR inválido não gera ordem (AC2) ---------------------------------------


@pytest.mark.parametrize("atr_invalido", [0.0, -0.001])
def test_executar_atr_invalido_nao_gera_ordem(session: Session, atr_invalido: float) -> None:
    runner = _runner()
    client = _client()
    order_manager = OrderManager(client, session, RiskManager(runner.settings))
    instrumento = _instrumento(session, client)

    runner._executar(
        "EURUSD", BUY_SIGNAL, atr_invalido, client, session, order_manager, instrumento
    )

    assert session.execute(select(Trade)).scalars().all() == []
    assert _motivos_bloqueio(session) == ["atr_invalido"]


# -- stop loss derivado do ATR, não constante (AC3) --------------------------


@pytest.mark.parametrize(("symbol", "atr"), [("EURUSD", 0.0010), ("GBPUSD", 0.0030)])
def test_executar_stop_loss_e_derivado_do_atr(session: Session, symbol: str, atr: float) -> None:
    runner = _runner()
    client = _client()
    order_manager = OrderManager(client, session, RiskManager(runner.settings))
    instrumento = _instrumento(session, client, symbol)

    runner._executar(symbol, BUY_SIGNAL, atr, client, session, order_manager, instrumento)

    trade = session.execute(select(Trade)).scalars().one()
    distancia_sl = atr * runner.settings.atr_sl_multiplier
    entrada = client.get_tick(symbol).ask
    assert trade.stop_loss == pytest.approx(entrada - distancia_sl)
    assert trade.take_profit == pytest.approx(
        entrada + distancia_sl * runner.settings.take_profit_rr
    )


def test_executar_sl_tp_usam_multiplicadores_configurados(session: Session) -> None:
    runner = _runner(atr_sl_multiplier=3.0, take_profit_rr=0.5)
    client = _client()
    order_manager = OrderManager(client, session, RiskManager(runner.settings))
    instrumento = _instrumento(session, client)

    atr = 0.0010
    runner._executar("EURUSD", BUY_SIGNAL, atr, client, session, order_manager, instrumento)

    trade = session.execute(select(Trade)).scalars().one()
    entrada = client.get_tick("EURUSD").ask
    distancia_sl = atr * 3.0
    assert trade.stop_loss == pytest.approx(entrada - distancia_sl)
    assert trade.take_profit == pytest.approx(entrada + distancia_sl * 0.5)


def test_executar_stop_loss_nao_e_constante_entre_atrs_diferentes(session: Session) -> None:
    runner = _runner()
    client = _client()
    order_manager = OrderManager(client, session, RiskManager(runner.settings))
    instrumento_eur = _instrumento(session, client, "EURUSD")
    instrumento_gbp = _instrumento(session, client, "GBPUSD")

    runner._executar("EURUSD", BUY_SIGNAL, 0.0010, client, session, order_manager, instrumento_eur)
    runner._executar("GBPUSD", BUY_SIGNAL, 0.0030, client, session, order_manager, instrumento_gbp)

    trades = session.execute(select(Trade).order_by(Trade.id)).scalars().all()
    assert len(trades) == 2
    assert trades[0].stop_loss != trades[1].stop_loss


# -- volume derivado do risco (história 30) -----------------------------------


def test_executar_volume_e_derivado_do_risco(session: Session) -> None:
    runner = _runner()
    client = _client()
    order_manager = OrderManager(client, session, RiskManager(runner.settings))
    instrumento = _instrumento(session, client)

    # ATR escolhido para o volume calculado pelo risco cair exato num múltiplo
    # do volume_step do broker (evita ruído de arredondamento no approx) e
    # ainda ficar abaixo do teto de tamanho da história 32 (default 2.0
    # lotes), já considerando o atr_sl_multiplier default 1.0 da história 33
    # — este teste cobre só a derivação por risco (história 30), não a
    # interação com o teto.
    atr = 0.0040
    runner._executar("EURUSD", BUY_SIGNAL, atr, client, session, order_manager, instrumento)

    trade = session.execute(select(Trade)).scalars().one()
    distancia_sl = atr * runner.settings.atr_sl_multiplier
    esperado = (100_000.0 * runner.settings.max_risk_per_trade_pct / 100.0) / (
        distancia_sl * instrumento.contract_size
    )
    assert esperado < runner.settings.volume_max_per_order_lots
    assert trade.volume == pytest.approx(esperado)


def test_executar_volume_nunca_excede_o_teto_por_ordem(session: Session) -> None:
    """História 32: perfil agressivo — VOLUME_MAX_POR_ORDEM é o teto duro de tamanho.

    ATR pequeno faz o risco calculado (história 30) pedir mais que o teto fixo
    de lotes; o volume final tem que ficar preso no teto, nunca no valor bruto.
    """
    runner = _runner()
    client = _client()
    order_manager = OrderManager(client, session, RiskManager(runner.settings))
    instrumento = _instrumento(session, client)

    atr = 0.0010  # risco bruto pediria 2.5 lotes, acima do teto de 2.0
    runner._executar("EURUSD", BUY_SIGNAL, atr, client, session, order_manager, instrumento)

    trade = session.execute(select(Trade)).scalars().one()
    assert trade.volume == pytest.approx(runner.settings.volume_max_per_order_lots)


def _instrumento_padrao(**overrides: object) -> Instrument:
    base: dict[str, object] = {
        "symbol": "EURUSD",
        "contract_size": 100_000.0,
        "min_volume": 0.01,
        "volume_step": 0.01,
        "volume_max": 100.0,
    }
    base.update(overrides)
    return Instrument(**base)


def test_executar_equity_maior_gera_volume_maior_proporcionalmente() -> None:
    distancia_sl = 0.0010 * 2.0

    volume_equity_baixo = BotRunner._calcular_volume(
        50_000.0, distancia_sl, _instrumento_padrao(), 0.5
    )
    volume_equity_alto = BotRunner._calcular_volume(
        100_000.0, distancia_sl, _instrumento_padrao(), 0.5
    )

    assert volume_equity_baixo is not None
    assert volume_equity_alto == pytest.approx(volume_equity_baixo * 2, rel=1e-6)


def test_executar_atr_maior_gera_volume_menor_para_mesmo_risco() -> None:
    volume_stop_curto = BotRunner._calcular_volume(100_000.0, 0.002, _instrumento_padrao(), 0.5)
    volume_stop_longo = BotRunner._calcular_volume(100_000.0, 0.006, _instrumento_padrao(), 0.5)

    assert volume_stop_curto is not None
    assert volume_stop_longo is not None
    assert volume_stop_longo < volume_stop_curto


def test_calcular_volume_arredonda_para_baixo_no_step_do_broker() -> None:
    instrumento = _instrumento_padrao(volume_step=0.1)

    # risco alvo = 100_000 * 0.5% = 500; distância_sl*contract = 0.0021*100_000 = 210
    # volume bruto = 500/210 = 2.3809... — deve arredondar para 2.3, nunca 2.4.
    volume = BotRunner._calcular_volume(100_000.0, 0.0021, instrumento, 0.5)

    assert volume == pytest.approx(2.3)


def test_calcular_volume_respeita_volume_max_do_broker() -> None:
    instrumento = _instrumento_padrao(volume_max=1.0)

    volume = BotRunner._calcular_volume(1_000_000.0, 0.001, instrumento, 0.5)

    assert volume == pytest.approx(1.0)


def test_executar_ordem_rejeitada_quando_risco_nao_cobre_lote_minimo(session: Session) -> None:
    """Risco não paga o lote mínimo: a ordem é rejeitada, nunca arredondada para cima.

    `volume_min=10.0` fica acima do volume bruto calculado pelo risco (5.0
    lotes com estes parâmetros) para acionar só o primeiro gate
    (`_calcular_volume` devolve `None`) — com um mínimo menor (ex.: 5.0) o
    teto por ordem (`volume_max_per_order_lots=2.0`) entra antes e o volume
    cai por outro motivo (margem/teto), não pela conta de risco em si.
    """
    runner = _runner()
    client = _client(FakeTerminal(volume_min=10.0))
    order_manager = OrderManager(client, session, RiskManager(runner.settings))
    instrumento = _instrumento(session, client)

    runner._executar("EURUSD", BUY_SIGNAL, 0.0010, client, session, order_manager, instrumento)

    assert session.execute(select(Trade)).scalars().all() == []
    assert _motivos_bloqueio(session) == ["risco_insuficiente"]


def test_executar_ordem_rejeitada_quando_margem_insuficiente(session: Session) -> None:
    """Margem livre real não cobre nem o lote mínimo: a ordem é rejeitada (história 32/36)."""
    runner = _runner()
    # Risco por trade sozinho pediria ~5 lotes (equity alta, ATR pequeno); a
    # margem livre real de US$5 só cobre uma fração de lote, abaixo do mínimo.
    client = _client(FakeTerminal(equity=100_000.0, margin_free=5.0))
    order_manager = OrderManager(client, session, RiskManager(runner.settings))
    instrumento = _instrumento(session, client)

    runner._executar("EURUSD", BUY_SIGNAL, 0.0010, client, session, order_manager, instrumento)

    assert session.execute(select(Trade)).scalars().all() == []
    assert _motivos_bloqueio(session) == ["margem_insuficiente"]


# -- história 34: múltiplas posições por símbolo, leitura distinta -----------


def _posicao_aberta(symbol: str = "EURUSD", tipo: int = 0) -> Position:
    return Position(
        ticket=1,
        identifier=1,
        symbol=symbol,
        volume=0.01,
        type=tipo,
        sl=1.15,
        tp=1.16,
        price_open=1.1545,
    )


def test_executar_mesmo_sinal_dois_ciclos_nao_abre_duas_posicoes(session: Session) -> None:
    runner = _runner()
    terminal = FakeTerminal()
    client = _client(terminal)
    order_manager = OrderManager(client, session, RiskManager(runner.settings))
    instrumento = _instrumento(session, client)

    runner._executar("EURUSD", BUY_SIGNAL, 0.0040, client, session, order_manager, instrumento)
    assert len(session.execute(select(Trade)).scalars().all()) == 1

    # O ciclo seguinte enxerga a posição que o broker acabou de confirmar.
    terminal.positions = (_posicao_aberta("EURUSD", tipo=0),)
    runner._executar("EURUSD", BUY_SIGNAL, 0.0040, client, session, order_manager, instrumento)

    assert len(session.execute(select(Trade)).scalars().all()) == 1
    assert _motivos_bloqueio(session) == ["cooldown"]


def test_executar_direcao_invertida_abre_posicao_mesmo_com_uma_ja_aberta(session: Session) -> None:
    runner = _runner()
    terminal = FakeTerminal(positions=(_posicao_aberta("EURUSD", tipo=0),))  # BUY já aberto
    client = _client(terminal)
    order_manager = OrderManager(client, session, RiskManager(runner.settings))
    instrumento = _instrumento(session, client)

    runner._executar("EURUSD", SELL_SIGNAL, 0.0040, client, session, order_manager, instrumento)

    trades = session.execute(select(Trade)).scalars().all()
    assert len(trades) == 1
    assert trades[0].side == Side.SELL


def test_executar_mesma_direcao_apos_cooldown_abre_nova_posicao(session: Session) -> None:
    runner = _runner(signal_repeat_cooldown_minutes=30)
    terminal = FakeTerminal(positions=(_posicao_aberta("EURUSD", tipo=0),))  # BUY já aberto
    client = _client(terminal)
    order_manager = OrderManager(client, session, RiskManager(runner.settings))
    instrumento = _instrumento(session, client)

    session.add(
        Trade(
            client_request_id="antigo-1",
            instrument_id=instrumento.id,
            side=Side.BUY,
            status=TradeStatus.OPEN,
            volume=0.01,
            stop_loss=1.0,
            trading_mode="demo",
            created_at=datetime.now(UTC) - timedelta(minutes=31),
        )
    )
    session.commit()

    runner._executar("EURUSD", BUY_SIGNAL, 0.0040, client, session, order_manager, instrumento)

    trades = session.execute(select(Trade).order_by(Trade.id)).scalars().all()
    assert len(trades) == 2


# -- idempotência com granularidade de minuto (AC4) --------------------------


def test_client_request_id_mesmo_minuto_mesmo_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_module, "datetime", _FixedDatetime)

    id1 = BotRunner._client_request_id("EURUSD", Side.BUY)
    id2 = BotRunner._client_request_id("EURUSD", Side.BUY)

    assert id1 == id2


def test_client_request_id_muda_entre_simbolos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_module, "datetime", _FixedDatetime)

    id_eur = BotRunner._client_request_id("EURUSD", Side.BUY)
    id_gbp = BotRunner._client_request_id("GBPUSD", Side.BUY)

    assert id_eur != id_gbp


# -- perda do dia soma só outcomes negativos de hoje (AC5) -------------------


_outcome_counter = itertools.count()


def _outcome_de(session: Session, *, pnl: float, created_at: datetime) -> None:
    n = next(_outcome_counter)
    instrument = Instrument(symbol=f"SYM{n}")
    session.add(instrument)
    session.flush()

    trade = Trade(
        client_request_id=f"trade-{n}",
        instrument_id=instrument.id,
        side=Side.BUY,
        volume=0.01,
        stop_loss=1.0,
        trading_mode="demo",
    )
    session.add(trade)
    session.flush()

    outcome = Outcome(
        trade_id=trade.id,
        exit_price=1.0,
        pnl=pnl,
        pnl_pct=pnl / 100.0,
        duration_seconds=60,
        actual_direction=Direction.BUY if pnl >= 0 else Direction.SELL,
        was_correct=pnl >= 0,
        created_at=created_at,
    )
    session.add(outcome)
    session.commit()


def test_perda_do_dia_soma_apenas_outcomes_negativos_de_hoje(session: Session) -> None:
    agora = datetime.now(UTC)
    ontem = agora - timedelta(days=1)

    _outcome_de(session, pnl=-50.0, created_at=agora)
    _outcome_de(session, pnl=30.0, created_at=agora)  # positivo: não conta
    _outcome_de(session, pnl=-20.0, created_at=ontem)  # de ontem: não conta

    assert BotRunner._perda_do_dia(session, _client()) == pytest.approx(50.0)


# -- história 29: perda do dia inclui o P&L flutuante das posições abertas ---


def test_perda_do_dia_inclui_pnl_flutuante_das_posicoes_abertas(session: Session) -> None:
    agora = datetime.now(UTC)
    _outcome_de(session, pnl=-50.0, created_at=agora)  # realizado

    posicoes = (
        SimpleNamespace(
            ticket=1,
            identifier=1,
            symbol="EURUSD",
            volume=0.1,
            type=0,
            sl=0.0,
            tp=0.0,
            price_open=1.1,
            profit=-300.0,
        ),
        SimpleNamespace(
            ticket=2,
            identifier=2,
            symbol="GBPUSD",
            volume=0.1,
            type=1,
            sl=0.0,
            tp=0.0,
            price_open=1.3,
            profit=50.0,  # lucro flutuante não abate a perda
        ),
    )
    client = _client(FakeTerminal(positions=posicoes))

    # Flutuante é o P&L líquido das posições (-300 + 50 = -250): o lucro de
    # uma posição reduz o risco líquido em aberto, mas o resultado do dia só
    # piora quando esse líquido é negativo — 50 (realizado) + 250 (flutuante
    # líquido negativo), nunca os 300 da perna perdedora isolada.
    assert BotRunner._perda_do_dia(session, client) == pytest.approx(50.0 + 250.0)


def test_perda_do_dia_sem_posicoes_abertas_e_so_o_realizado(session: Session) -> None:
    _outcome_de(session, pnl=-40.0, created_at=datetime.now(UTC))

    assert BotRunner._perda_do_dia(session, _client()) == pytest.approx(40.0)


# -- falha de MT5 encerra o ciclo em vez de pular o símbolo (AC7) ------------


def test_run_cycle_falha_de_mt5_encerra_o_ciclo(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    processados: list[str] = []

    def fake_process_symbol(
        symbol: str, _client: MT5Client, _session: Session, _order_manager: OrderManager
    ) -> None:
        processados.append(symbol)
        if symbol == "EURUSD":
            raise MT5ConnectionError("terminal caiu")

    runner = _runner(["EURUSD", "GBPUSD"])
    monkeypatch.setattr(runner, "_process_symbol", fake_process_symbol)

    with pytest.raises(MT5ConnectionError):
        runner.run_cycle(_client(), session)

    assert processados == ["EURUSD"]


def test_run_cycle_erro_generico_nao_interrompe_outros_simbolos(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    processados: list[str] = []

    def fake_process_symbol(
        symbol: str, _client: MT5Client, _session: Session, _order_manager: OrderManager
    ) -> None:
        processados.append(symbol)
        if symbol == "EURUSD":
            raise ValueError("erro inesperado")

    runner = _runner(["EURUSD", "GBPUSD"])
    monkeypatch.setattr(runner, "_process_symbol", fake_process_symbol)

    runner.run_cycle(_client(), session)

    assert processados == ["EURUSD", "GBPUSD"]


# -- utilitários auxiliares ---------------------------------------------------


def test_get_or_create_instrument_reaproveita_instrumento_existente(session: Session) -> None:
    client = _client()
    primeiro = BotRunner._get_or_create_instrument(session, "EURUSD", client)
    segundo = BotRunner._get_or_create_instrument(session, "EURUSD", client)

    assert primeiro.id == segundo.id
    assert len(session.execute(select(Instrument)).scalars().all()) == 1


def test_get_or_create_instrument_le_volume_minimo_do_broker(session: Session) -> None:
    """História 23: lote mínimo vem do broker por símbolo, não de uma constante."""
    client = _client(FakeTerminal(volume_min=0.25))

    instrumento = BotRunner._get_or_create_instrument(session, "XAUUSD", client)

    assert instrumento.min_volume == pytest.approx(0.25)


def test_get_or_create_instrument_atualiza_volume_minimo_quando_broker_muda(
    session: Session,
) -> None:
    client_antigo = _client(FakeTerminal(volume_min=0.01))
    BotRunner._get_or_create_instrument(session, "EURUSD", client_antigo)

    client_novo = _client(FakeTerminal(volume_min=0.5))
    instrumento = BotRunner._get_or_create_instrument(session, "EURUSD", client_novo)

    assert instrumento.min_volume == pytest.approx(0.5)
    assert len(session.execute(select(Instrument)).scalars().all()) == 1


def test_get_or_create_instrument_atualiza_contract_size_quando_broker_muda(
    session: Session,
) -> None:
    """História 31: contract_size desatualizado (ex: XAUUSD com o default 100_000
    de EURUSD) é corrigido no mesmo ciclo que sincroniza volume_min, sem esperar
    o instrumento ser recriado."""
    client_antigo = _client(FakeTerminal(contract_size=100_000.0))
    BotRunner._get_or_create_instrument(session, "XAUUSD", client_antigo)

    client_novo = _client(FakeTerminal(contract_size=100.0))
    instrumento = BotRunner._get_or_create_instrument(session, "XAUUSD", client_novo)

    assert instrumento.contract_size == pytest.approx(100.0)
    assert len(session.execute(select(Instrument)).scalars().all()) == 1


# -- _process_symbol: do candle cru até a ordem -------------------------------


def _candle_rows(closes: list[float], amplitude: float = 0.5) -> list[dict[str, float]]:
    return [
        {"open": c - amplitude / 2, "high": c + amplitude, "low": c - amplitude, "close": c}
        for c in closes
    ]


def test_process_symbol_com_poucos_candles_nao_avalia(session: Session) -> None:
    candles = _candle_rows([100.0 + i * 0.1 for i in range(10)])  # abaixo do mínimo (35)
    client = _client(FakeTerminal(candle_rows=candles))
    runner = _runner()
    order_manager = OrderManager(client, session, RiskManager(runner.settings))

    runner._process_symbol("EURUSD", client, session, order_manager)

    assert session.execute(select(Trade)).scalars().all() == []


def test_process_symbol_com_sinal_direcional_executa_ordem(session: Session) -> None:
    # Amplitude pequena o bastante para o risco monetário (ATR-based) caber no
    # limite de 1% do equity; a direção em si (compra ou venda) não importa
    # aqui — o que se testa é que _process_symbol chega até _executar.
    # min_signal_confidence baixo porque, numa rampa reta, os fatores de
    # reversão (rsi/bollinger/mean_reversion) e os de tendência
    # (momentum/trend_strength) brigam entre si por natureza — a confiança
    # da fusão fica baixa mesmo com sinal direcional claro (história 35).
    candles = _candle_rows([100.0 + i * 0.05 for i in range(60)], amplitude=0.02)
    client = _client(FakeTerminal(candle_rows=candles))
    runner = _runner(min_signal_confidence=0.01)
    order_manager = OrderManager(client, session, RiskManager(runner.settings))

    runner._process_symbol("EURUSD", client, session, order_manager)

    trade = session.execute(select(Trade)).scalars().one()
    assert trade.stop_loss != trade.entry_price  # SL vem da volatilidade, não é constante


# -- história 24: runner persiste o Signal ------------------------------------


def test_process_symbol_grava_signal_antes_da_ordem_e_liga_trade(session: Session) -> None:
    candles = _candle_rows([100.0 + i * 0.05 for i in range(60)], amplitude=0.02)
    client = _client(FakeTerminal(candle_rows=candles))
    runner = _runner(min_signal_confidence=0.01)
    order_manager = OrderManager(client, session, RiskManager(runner.settings))

    runner._process_symbol("EURUSD", client, session, order_manager)

    signal = session.execute(select(Signal)).scalars().one()
    trade = session.execute(select(Trade)).scalars().one()

    assert trade.signal_id == signal.id
    assert trade.strategy == "technical"
    assert signal.strategy == "technical"
    assert signal.direction in (Direction.BUY, Direction.SELL)
    assert signal.weight_version == DEFAULT_WEIGHTS.version
    assert signal.sentiment_score is None
    assert signal.sentiment_confidence is None
    assert signal.technical_score is not None
    assert "components" in signal.inputs
    assert "rsi" in signal.inputs["components"]
    assert signal.inputs["weights"]["version"] == DEFAULT_WEIGHTS.version


def test_process_symbol_grava_signal_mesmo_em_hold(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    candles = _candle_rows([100.0 + i * 0.1 for i in range(60)])
    client = _client(FakeTerminal(candle_rows=candles))
    runner = _runner()
    order_manager = OrderManager(client, session, RiskManager(runner.settings))

    hold = FusedSignal(direction=Direction.HOLD, score=0.02, confidence=0.05, weight_version="v1.0")
    monkeypatch.setattr(runner_module, "fuse_signals", lambda **_kw: hold)

    runner._process_symbol("EURUSD", client, session, order_manager)

    signal = session.execute(select(Signal)).scalars().one()
    assert signal.direction == Direction.HOLD
    assert session.execute(select(Trade)).scalars().all() == []


# -- história 27: filtro de confiança mínima ---------------------------------


def _process_com_fused(
    session: Session, monkeypatch: pytest.MonkeyPatch, fused: FusedSignal
) -> None:
    candles = _candle_rows([100.0 + i * 0.05 for i in range(60)], amplitude=0.02)
    client = _client(FakeTerminal(candle_rows=candles))
    runner = _runner()
    order_manager = OrderManager(client, session, RiskManager(runner.settings))
    monkeypatch.setattr(runner_module, "fuse_signals", lambda **_kw: fused)

    runner._process_symbol("EURUSD", client, session, order_manager)


def test_process_symbol_confianca_abaixo_do_limiar_nao_executa(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fraco = FusedSignal(direction=Direction.BUY, score=0.5, confidence=0.09, weight_version="v1.0")

    _process_com_fused(session, monkeypatch, fraco)

    signal = session.execute(select(Signal)).scalars().one()
    assert signal.direction == Direction.BUY  # decisão é gravada mesmo rejeitada
    assert session.execute(select(Trade)).scalars().all() == []
    assert _motivos_bloqueio(session) == ["confianca_insuficiente"]


def test_process_symbol_confianca_igual_ao_limiar_executa(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_limiar = FusedSignal(
        direction=Direction.BUY, score=0.5, confidence=0.1, weight_version="v1.0"
    )

    _process_com_fused(session, monkeypatch, no_limiar)

    assert session.execute(select(Trade)).scalars().all() != []


def test_process_symbol_confianca_acima_do_limiar_executa(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    forte = FusedSignal(direction=Direction.BUY, score=0.5, confidence=0.8, weight_version="v1.0")

    _process_com_fused(session, monkeypatch, forte)

    assert session.execute(select(Trade)).scalars().all() != []


def test_outcome_de_trade_com_signal_tem_predicted_direction(session: Session) -> None:
    candles = _candle_rows([100.0 + i * 0.05 for i in range(60)], amplitude=0.02)
    client = _client(FakeTerminal(candle_rows=candles))
    runner = _runner(min_signal_confidence=0.01)
    order_manager = OrderManager(client, session, RiskManager(runner.settings))

    runner._process_symbol("EURUSD", client, session, order_manager)

    trade = session.execute(select(Trade)).scalars().one()
    assert trade.signal_id is not None
    assert trade.entry_price is not None
    entry_price = trade.entry_price
    trade.status = TradeStatus.CLOSED
    trade.opened_at = datetime.now(UTC) - timedelta(minutes=5)
    trade.closed_at = datetime.now(UTC)
    session.commit()

    outcome = record_outcome(session, trade, exit_price=entry_price + 0.001, pnl=1.0)

    assert outcome.predicted_direction is not None


# -- stop loss não positivo aborta a ordem ------------------------------------


def test_executar_com_stop_loss_nao_positivo_nao_gera_ordem(session: Session) -> None:
    preco_baixo = SimpleNamespace(time=1_700_000_000, bid=0.0011, ask=0.0012)
    client = _client(FakeTerminal(tick=preco_baixo))
    runner = _runner()
    order_manager = OrderManager(client, session, RiskManager(runner.settings))
    instrumento = _instrumento(session, client)

    # distância do SL (atr * multiplier) maior que o próprio preço de entrada.
    runner._executar("EURUSD", BUY_SIGNAL, 1.0, client, session, order_manager, instrumento)

    assert session.execute(select(Trade)).scalars().all() == []
    assert _motivos_bloqueio(session) == ["stop_invalido"]


# -- run(): conecta, roda N ciclos e dorme entre eles -------------------------


def test_run_conecta_e_executa_max_cycles(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mt5_client = MT5Client(terminal=FakeTerminal(), settings=_settings())
    monkeypatch.setattr(runner_module, "MT5Client", lambda **_kw: fake_mt5_client)

    @contextlib.contextmanager
    def _fake_session_scope() -> Any:
        yield session

    monkeypatch.setattr(runner_module, "session_scope", _fake_session_scope)
    monkeypatch.setattr(time, "sleep", lambda _segundos: None)

    runner = _runner(["EURUSD"])
    ciclos: list[int] = []
    monkeypatch.setattr(runner, "run_cycle", lambda *_a, **_kw: ciclos.append(1))

    runner.run(max_cycles=2)

    assert len(ciclos) == 2
    assert fake_mt5_client.is_connected is False  # __exit__ desligou o terminal


# -- história 39: registro de estratégias paralelas --------------------------


class _StubStrategy:
    """Estratégia falsa: devolve um resultado fixo ou lança uma exceção fixa."""

    def __init__(
        self,
        name: str,
        resultado: StrategySignal | None = None,
        excecao: Exception | None = None,
    ) -> None:
        self.name = name
        self._resultado = resultado
        self._excecao = excecao
        self.chamadas = 0

    def evaluate(self, candles: object) -> StrategySignal | None:
        self.chamadas += 1
        if self._excecao is not None:
            raise self._excecao
        return self._resultado


def test_process_symbol_isola_estrategia_que_lanca_excecao(session: Session) -> None:
    """AC7 da história 39: uma estratégia quebrada não derruba as outras no ciclo."""
    candles = _candle_rows([100.0 + i * 0.05 for i in range(60)], amplitude=0.02)
    client = _client(FakeTerminal(candle_rows=candles))
    quebrada = _StubStrategy("quebrada", excecao=RuntimeError("boom"))
    boa = _StubStrategy(
        "boa", resultado=StrategySignal(direction=Direction.BUY, confidence=0.9, score=0.9)
    )
    runner = _runner(min_signal_confidence=0.01)
    runner._strategies = [quebrada, boa]
    order_manager = OrderManager(client, session, RiskManager(runner.settings))

    runner._process_symbol("EURUSD", client, session, order_manager)

    assert quebrada.chamadas == 1
    assert boa.chamadas == 1
    trades = session.execute(select(Trade)).scalars().all()
    assert len(trades) == 1
    assert trades[0].strategy == "boa"


def test_process_symbol_estrategia_sem_setup_nao_gera_signal_nem_ordem(session: Session) -> None:
    candles = _candle_rows([100.0 + i * 0.05 for i in range(60)], amplitude=0.02)
    client = _client(FakeTerminal(candle_rows=candles))
    sem_setup = _StubStrategy("sem_setup", resultado=None)
    runner = _runner()
    runner._strategies = [sem_setup]
    order_manager = OrderManager(client, session, RiskManager(runner.settings))

    runner._process_symbol("EURUSD", client, session, order_manager)

    assert sem_setup.chamadas == 1
    assert session.execute(select(Signal)).scalars().all() == []
    assert session.execute(select(Trade)).scalars().all() == []


def test_process_symbol_duas_estrategias_geram_dois_signals_e_duas_ordens(
    session: Session,
) -> None:
    """AC4/AC5: todas as estratégias habilitadas rodam, cada uma com sua própria ordem."""
    candles = _candle_rows([100.0 + i * 0.05 for i in range(60)], amplitude=0.02)
    client = _client(FakeTerminal(candle_rows=candles))
    a = _StubStrategy(
        "estrategia_a", resultado=StrategySignal(direction=Direction.BUY, confidence=0.9, score=0.9)
    )
    b = _StubStrategy(
        "estrategia_b", resultado=StrategySignal(direction=Direction.BUY, confidence=0.9, score=0.9)
    )
    runner = _runner(min_signal_confidence=0.01)
    runner._strategies = [a, b]
    order_manager = OrderManager(client, session, RiskManager(runner.settings))

    runner._process_symbol("EURUSD", client, session, order_manager)

    signals = session.execute(select(Signal)).scalars().all()
    trades = session.execute(select(Trade)).scalars().all()
    assert {s.strategy for s in signals} == {"estrategia_a", "estrategia_b"}
    assert {t.strategy for t in trades} == {"estrategia_a", "estrategia_b"}
    # client_request_id inclui a estratégia: as duas ordens não colidem.
    assert len({t.client_request_id for t in trades}) == 2


def test_pode_abrir_posicao_estrategias_diferentes_nao_bloqueiam_entre_si(
    session: Session,
) -> None:
    """AC6: o cooldown passa a ser por (símbolo, direção, estratégia)."""
    runner = _runner(signal_repeat_cooldown_minutes=30)
    client = _client(FakeTerminal(positions=(_posicao_aberta("EURUSD", tipo=0),)))
    order_manager = OrderManager(client, session, RiskManager(runner.settings))
    instrumento = _instrumento(session, client)

    runner._executar(
        "EURUSD", BUY_SIGNAL, 0.0040, client, session, order_manager, instrumento, strategy="a"
    )
    runner._executar(
        "EURUSD", BUY_SIGNAL, 0.0040, client, session, order_manager, instrumento, strategy="b"
    )

    trades = session.execute(select(Trade)).scalars().all()
    assert len(trades) == 2
    assert {t.strategy for t in trades} == {"a", "b"}


def test_client_request_id_muda_entre_estrategias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_module, "datetime", _FixedDatetime)

    id_a = BotRunner._client_request_id("EURUSD", Side.BUY, "estrategia_a")
    id_b = BotRunner._client_request_id("EURUSD", Side.BUY, "estrategia_b")

    assert id_a != id_b


def test_registrar_bloqueio_nao_duplica_mesmo_motivo_simbolo_e_estrategia(
    session: Session,
) -> None:
    runner = _runner()

    runner._registrar_bloqueio(session, "cooldown", "EURUSD", "technical", direcao="buy")
    runner._registrar_bloqueio(session, "cooldown", "EURUSD", "technical", direcao="buy")

    eventos = (
        session.execute(select(AuditLog).where(AuditLog.event_type == AuditEventType.ORDER_BLOCKED))
        .scalars()
        .all()
    )
    assert len(eventos) == 1


def test_registrar_bloqueio_estrategias_diferentes_nao_sao_deduplicadas(session: Session) -> None:
    runner = _runner()

    runner._registrar_bloqueio(session, "cooldown", "EURUSD", "technical", direcao="buy")
    runner._registrar_bloqueio(session, "cooldown", "EURUSD", "bbrsi", direcao="buy")

    eventos = (
        session.execute(select(AuditLog).where(AuditLog.event_type == AuditEventType.ORDER_BLOCKED))
        .scalars()
        .all()
    )
    assert len(eventos) == 2


def test_botrunner_falha_no_boot_com_estrategia_desconhecida() -> None:
    with pytest.raises(ValueError, match="estrategia desconhecida"):
        _runner(strategies_enabled="bbrsi")
