"""História 3 — o conector falha fechado. Nunca simula preço."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from backend.collection.mt5_client import (
    MT5_TRADE_MODE_CONTEST,
    MT5_TRADE_MODE_DEMO,
    MT5_TRADE_MODE_REAL,
    MT5Client,
    MT5ConnectionError,
    MT5ModeMismatchError,
)
from backend.config import Settings


class FakeTerminal:
    """Dublê do módulo MetaTrader5. Existe só para teste — produção usa o real."""

    def __init__(
        self,
        *,
        initialize_ok: bool = True,
        login_ok: bool = True,
        trade_mode: int = MT5_TRADE_MODE_DEMO,
        tick: Any = None,
        account: Any = "default",
        symbol_info: Any = "default",
    ) -> None:
        self.initialize_ok = initialize_ok
        self.login_ok = login_ok
        self.trade_mode = trade_mode
        self._tick = (
            tick
            if tick is not None
            else SimpleNamespace(time=1_700_000_000, bid=1.0850, ask=1.0852)
        )
        self._account = account
        self._symbol_info = symbol_info
        self.initialize_calls = 0
        self.login_calls = 0
        self.shutdown_calls = 0
        self.selected: list[str] = []
        self._fail_select = False
        self._rates: Any = None

    def initialize(self, *_args: Any, **_kwargs: Any) -> bool:
        self.initialize_calls += 1
        return self.initialize_ok

    def login(self, *_args: Any, **_kwargs: Any) -> bool:
        self.login_calls += 1
        return self.login_ok

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def last_error(self) -> tuple[int, str]:
        return (-10001, "IPC initialize failed")

    def account_info(self) -> Any:
        if self._account != "default":
            return self._account
        return SimpleNamespace(
            login=5_012_345,
            server="FTMO-Demo",
            currency="USD",
            balance=100_000.0,
            equity=100_120.5,
            margin=250.0,
            margin_free=99_870.5,
            leverage=100,
            trade_mode=self.trade_mode,
        )

    def symbol_info_tick(self, _symbol: str) -> Any:
        return self._tick

    def symbol_info(self, _symbol: str) -> Any:
        if self._symbol_info != "default":
            return self._symbol_info
        return SimpleNamespace(volume_min=0.01)

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        if self._fail_select:
            return False
        self.selected.append(symbol)
        return True

    def positions_get(self) -> Any:
        return ()

    def history_deals_get(self, *args: Any, **kwargs: Any) -> Any:
        return ()

    def order_send(self, request: dict[str, Any]) -> Any:
        return SimpleNamespace(retcode=10009, order=123, deal=456, price=1.1, volume=0.1)

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start_pos: int, count: int) -> Any:
        return self._rates

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


def _settings(**kw: object) -> Settings:
    base: dict[str, object] = {
        "trading_mode": "demo",
        "real_trading_unlocked": False,
        "mt5_login": None,
    }
    base.update(kw)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def _client(terminal: FakeTerminal, **kw: object) -> MT5Client:
    return MT5Client(terminal=terminal, settings=_settings(**kw))


# --- conexão ---------------------------------------------------------------
def test_connect_devolve_a_conta() -> None:
    client = _client(FakeTerminal())
    account = client.connect()

    assert account.login == 5_012_345
    assert account.is_demo is True
    assert client.is_connected is True


def test_initialize_falho_levanta_erro_apos_retentar() -> None:
    """Falha de terminal é erro, nunca dado sintético."""
    terminal = FakeTerminal(initialize_ok=False)
    with pytest.raises(MT5ConnectionError, match="initialize"):
        _client(terminal).connect()

    assert terminal.initialize_calls == 3  # retry com backoff


def test_login_recusado_encerra_o_terminal() -> None:
    terminal = FakeTerminal(login_ok=False)
    with pytest.raises(MT5ConnectionError, match="login recusado"):
        _client(terminal, mt5_login=999).connect()

    assert terminal.shutdown_calls >= 1


def test_erro_de_login_nao_vaza_credencial() -> None:
    terminal = FakeTerminal(login_ok=False)
    with pytest.raises(MT5ConnectionError) as exc:
        _client(terminal, mt5_login=999, mt5_password="s3nha", mt5_server="FTMO-Server").connect()

    assert "s3nha" not in str(exc.value)
    assert "FTMO-Server" not in str(exc.value)


# --- coerência de modo -----------------------------------------------------
def test_conta_real_com_bot_em_demo_e_recusada() -> None:
    """Segunda barreira do invariante §1, agora no lado do broker."""
    terminal = FakeTerminal(trade_mode=MT5_TRADE_MODE_REAL)
    with pytest.raises(MT5ModeMismatchError, match="Conta REAL"):
        _client(terminal).connect()

    assert terminal.shutdown_calls >= 1


def test_conta_demo_com_bot_em_real_e_recusada() -> None:
    terminal = FakeTerminal(trade_mode=MT5_TRADE_MODE_DEMO)
    with pytest.raises(MT5ModeMismatchError, match="conta conectada é demo"):
        _client(terminal, trading_mode="real", real_trading_unlocked=True).connect()


def test_conta_de_concurso_conta_como_demo() -> None:
    """FTMO Challenge é trade_mode=contest: não é capital real, então passa em demo."""
    account = _client(FakeTerminal(trade_mode=MT5_TRADE_MODE_CONTEST)).connect()
    assert account.is_demo is True


def test_mismatch_de_modo_nao_e_retentado() -> None:
    """Retentar não muda o tipo da conta — insistir só atrasa a falha."""
    terminal = FakeTerminal(trade_mode=MT5_TRADE_MODE_REAL)
    with pytest.raises(MT5ModeMismatchError):
        _client(terminal).connect()

    assert terminal.initialize_calls == 1


# --- leitura ---------------------------------------------------------------
def test_get_tick_exige_conexao() -> None:
    with pytest.raises(MT5ConnectionError, match="não conectado"):
        _client(FakeTerminal()).get_tick("EURUSD")


def test_get_tick_devolve_bid_ask_e_spread() -> None:
    client = _client(FakeTerminal())
    client.connect()
    tick = client.get_tick("EURUSD")

    assert tick.bid == pytest.approx(1.0850)
    assert tick.spread == pytest.approx(0.0002)
    assert tick.mid == pytest.approx(1.0851)
    assert tick.time.tzinfo is not None


def test_tick_ausente_levanta_erro() -> None:
    terminal = FakeTerminal()
    client = _client(terminal)
    client.connect()
    terminal._tick = None

    with pytest.raises(MT5ConnectionError, match="vazio"):
        client.get_tick("EURUSD")


def test_tick_com_preco_zero_e_rejeitado() -> None:
    """Zero é o valor que o MT5 devolve quando não sabe. Aceitar seria operar sobre ficção."""
    terminal = FakeTerminal(tick=SimpleNamespace(time=1_700_000_000, bid=0.0, ask=0.0))
    client = _client(terminal)
    client.connect()

    with pytest.raises(MT5ConnectionError, match="inválido"):
        client.get_tick("EURUSD")


def test_get_ticks_falha_inteira_se_um_simbolo_falhar() -> None:
    """Retorno parcial esconderia do pipeline que a visão de mercado está incompleta."""
    terminal = FakeTerminal(tick=SimpleNamespace(time=1_700_000_000, bid=-1.0, ask=1.0))
    client = _client(terminal)
    client.connect()

    with pytest.raises(MT5ConnectionError):
        client.get_ticks(["EURUSD", "GBPUSD"])


def test_get_ticks_devolve_mapa_por_simbolo() -> None:
    client = _client(FakeTerminal())
    client.connect()
    ticks = client.get_ticks(["EURUSD", "GBPUSD"])

    assert set(ticks) == {"EURUSD", "GBPUSD"}
    assert ticks["EURUSD"].symbol == "EURUSD"


# --- lote mínimo por símbolo (história 23) ---------------------------------
def test_get_symbol_info_exige_conexao() -> None:
    with pytest.raises(MT5ConnectionError, match="não conectado"):
        _client(FakeTerminal()).get_symbol_info("EURUSD")


def test_get_symbol_info_devolve_volume_min_do_broker() -> None:
    terminal = FakeTerminal(symbol_info=SimpleNamespace(volume_min=0.1))
    client = _client(terminal)
    client.connect()

    info = client.get_symbol_info("EURUSD")

    assert info.symbol == "EURUSD"
    assert info.volume_min == pytest.approx(0.1)


def test_get_symbol_info_ausente_levanta_erro() -> None:
    """Broker que não respondeu é erro, não é o mínimo padrão assumido."""
    terminal = FakeTerminal(symbol_info=None)
    client = _client(terminal)
    client.connect()

    with pytest.raises(MT5ConnectionError, match="vazio"):
        client.get_symbol_info("EURUSD")


def test_get_symbol_info_com_volume_min_zero_e_rejeitado() -> None:
    terminal = FakeTerminal(symbol_info=SimpleNamespace(volume_min=0.0))
    client = _client(terminal)
    client.connect()

    with pytest.raises(MT5ConnectionError, match="inválido"):
        client.get_symbol_info("EURUSD")


def test_account_info_vazio_levanta_erro() -> None:
    terminal = FakeTerminal(account=None)
    client = _client(terminal)
    with pytest.raises(MT5ConnectionError, match="account_info"):
        client.connect()


# --- ciclo de vida ---------------------------------------------------------
def test_context_manager_conecta_e_encerra() -> None:
    terminal = FakeTerminal()
    with MT5Client(terminal=terminal, settings=_settings()) as client:
        assert client.is_connected

    assert terminal.shutdown_calls == 1


def test_pacote_ausente_falha_fechado() -> None:
    """Sem o pacote MetaTrader5 o bot para — não existe modo degradado."""
    import backend.collection.mt5_client as mod

    original = mod._load_terminal

    def _sem_pacote() -> Any:
        raise MT5ConnectionError("Pacote MetaTrader5 indisponível neste host.")

    mod._load_terminal = _sem_pacote
    try:
        with pytest.raises(MT5ConnectionError, match="indisponível"):
            MT5Client(settings=_settings()).connect()
    finally:
        mod._load_terminal = original
