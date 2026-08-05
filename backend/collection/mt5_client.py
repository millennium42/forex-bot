"""Conector do terminal MetaTrader 5.

Invariante (§6 do PRD): **sem mocks em produção**. Se o terminal não conecta,
toda chamada levanta `MT5ConnectionError`. Em nenhuma hipótese este módulo
devolve preço sintético, último valor conhecido ou zero — um pipeline que
recebe dado inventado toma decisão de trade sobre ficção.

O terminal é injetável (`MT5Terminal`) para que os testes exercitem o protocolo
sem um MetaTrader instalado. A injeção existe para teste; o default de produção
é o módulo `MetaTrader5` real, e não há caminho de código que substitua isso
em runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast, runtime_checkable

import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.config import Settings, get_settings

logger = structlog.get_logger(__name__)

# trade_mode do MT5: 0 = demo, 1 = concurso, 2 = conta real.
MT5_TRADE_MODE_DEMO = 0
MT5_TRADE_MODE_CONTEST = 1
MT5_TRADE_MODE_REAL = 2


class MT5ConnectionError(RuntimeError):
    """Terminal indisponível, login recusado ou dado não obtido."""


class MT5ModeMismatchError(RuntimeError):
    """A conta conectada não corresponde ao modo de operação configurado."""


@runtime_checkable
class MT5Terminal(Protocol):
    """Superfície do módulo `MetaTrader5` que este projeto usa."""

    def initialize(self, *args: Any, **kwargs: Any) -> bool: ...
    def login(self, *args: Any, **kwargs: Any) -> bool: ...
    def shutdown(self) -> None: ...
    def last_error(self) -> tuple[int, str]: ...
    def account_info(self) -> Any: ...
    def symbol_info_tick(self, symbol: str) -> Any: ...
    def symbol_select(self, symbol: str, enable: bool) -> bool: ...
    def order_send(self, request: dict[str, Any]) -> Any: ...
    def positions_get(self) -> Any: ...
    def copy_rates_from_pos(
        self, symbol: str, timeframe: int, start_pos: int, count: int
    ) -> Any: ...

    @property
    def TRADE_ACTION_DEAL(self) -> int: ...  # noqa: N802
    @property
    def ORDER_TYPE_BUY(self) -> int: ...  # noqa: N802
    @property
    def ORDER_TYPE_SELL(self) -> int: ...  # noqa: N802
    @property
    def TRADE_RETCODE_DONE(self) -> int: ...  # noqa: N802


@dataclass(frozen=True, slots=True)
class AccountInfo:
    login: int
    server: str
    currency: str
    balance: float
    equity: float
    margin: float
    margin_free: float
    leverage: int
    trade_mode: int

    @property
    def is_demo(self) -> bool:
        """Conta de concurso (FTMO Challenge) conta como demo: não é capital real."""
        return self.trade_mode in (MT5_TRADE_MODE_DEMO, MT5_TRADE_MODE_CONTEST)


@dataclass(frozen=True, slots=True)
class Tick:
    symbol: str
    time: datetime
    bid: float
    ask: float

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True, slots=True)
class Position:
    ticket: int
    identifier: int
    symbol: str
    volume: float
    type: int
    sl: float
    tp: float
    price_open: float


def _load_terminal() -> MT5Terminal:
    """Importa o MetaTrader5 real. Import tardio: o pacote é win32-only e o CI roda em Linux."""
    try:
        import MetaTrader5
    except ImportError as exc:
        raise MT5ConnectionError(
            "Pacote MetaTrader5 indisponível neste host. O bot não opera sem terminal."
        ) from exc
    return cast(MT5Terminal, MetaTrader5)


class MT5Client:
    """Cliente do terminal. Uma instância por processo."""

    def __init__(
        self,
        terminal: MT5Terminal | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._terminal = terminal
        self._settings = settings or get_settings()
        self._connected = False

    # -- conexão ------------------------------------------------------------
    @property
    def terminal(self) -> MT5Terminal:
        if self._terminal is None:
            self._terminal = _load_terminal()
        return self._terminal

    @property
    def is_connected(self) -> bool:
        return self._connected

    @retry(
        retry=retry_if_exception_type(MT5ConnectionError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def connect(self) -> AccountInfo:
        """Conecta e devolve a conta. Levanta `MT5ConnectionError` se não der.

        `MT5ModeMismatchError` não é retentado: tentar de novo não muda o fato
        de a conta ser real.
        """
        terminal = self.terminal
        kwargs: dict[str, Any] = {"timeout": self._settings.mt5_timeout_ms}
        if self._settings.mt5_terminal_path:
            kwargs["path"] = self._settings.mt5_terminal_path

        if not terminal.initialize(**kwargs):
            raise MT5ConnectionError(f"initialize() falhou: {self._erro()}")

        if self._settings.mt5_login is not None:
            ok = terminal.login(
                self._settings.mt5_login,
                password=self._settings.mt5_password,
                server=self._settings.mt5_server,
            )
            if not ok:
                terminal.shutdown()
                # A mensagem nunca inclui senha nem servidor: audit log e stderr
                # não podem carregar credencial.
                raise MT5ConnectionError(f"login recusado para {self._settings.mt5_login}")

        self._connected = True
        account = self.get_account_info()
        self._assert_modo_compativel(account)
        logger.info(
            "mt5.conectado", login=account.login, server=account.server, demo=account.is_demo
        )
        return account

    def _assert_modo_compativel(self, account: AccountInfo) -> None:
        """Cruza o modo configurado com o tipo real da conta conectada.

        Segunda barreira do invariante §1: mesmo com a config correta, uma conta
        real conectada por engano derruba a sessão em vez de operar.
        """
        if not account.is_demo and not self._settings.is_real_trading:
            self.shutdown()
            raise MT5ModeMismatchError(
                "Conta REAL conectada com o bot em modo demo. Conexão encerrada."
            )
        if account.is_demo and self._settings.is_real_trading:
            self.shutdown()
            raise MT5ModeMismatchError(
                "Modo real habilitado mas a conta conectada é demo. Conexão encerrada."
            )

    def shutdown(self) -> None:
        if self._terminal is not None:
            self._terminal.shutdown()
        self._connected = False

    def _erro(self) -> str:
        try:
            code, msg = self.terminal.last_error()
        except Exception:  # pragma: no cover - terminal sem last_error utilizável
            return "erro desconhecido"
        return f"[{code}] {msg}"

    def _assert_conectado(self) -> None:
        if not self._connected:
            raise MT5ConnectionError("Terminal não conectado. Chame connect() antes.")

    # -- leitura ------------------------------------------------------------
    def get_account_info(self) -> AccountInfo:
        self._assert_conectado()
        raw = self.terminal.account_info()
        if raw is None:
            raise MT5ConnectionError(f"account_info() vazio: {self._erro()}")
        return AccountInfo(
            login=int(raw.login),
            server=str(raw.server),
            currency=str(raw.currency),
            balance=float(raw.balance),
            equity=float(raw.equity),
            margin=float(raw.margin),
            margin_free=float(raw.margin_free),
            leverage=int(raw.leverage),
            trade_mode=int(raw.trade_mode),
        )

    @retry(
        retry=retry_if_exception_type(MT5ConnectionError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    def get_tick(self, symbol: str) -> Tick:
        """Último tick do símbolo. Sem fallback: dado ausente é erro, não zero."""
        self._assert_conectado()
        if not self.terminal.symbol_select(symbol, True):
            raise MT5ConnectionError(f"símbolo {symbol} indisponível: {self._erro()}")

        raw = self.terminal.symbol_info_tick(symbol)
        if raw is None:
            raise MT5ConnectionError(f"symbol_info_tick({symbol}) vazio: {self._erro()}")

        bid, ask = float(raw.bid), float(raw.ask)
        if bid <= 0 or ask <= 0:
            raise MT5ConnectionError(f"tick inválido para {symbol}: bid={bid} ask={ask}")

        return Tick(
            symbol=symbol,
            time=datetime.fromtimestamp(int(raw.time), tz=UTC),
            bid=bid,
            ask=ask,
        )

    def get_ticks(self, symbols: list[str]) -> dict[str, Tick]:
        """Ticks de vários símbolos. Falha inteira se qualquer um falhar.

        Retorno parcial seria pior que erro: o pipeline decidiria sobre uma
        visão incompleta do mercado sem saber que está incompleta.
        """
        self._assert_conectado()
        return {symbol: self.get_tick(symbol) for symbol in symbols}

    @retry(
        retry=retry_if_exception_type(MT5ConnectionError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    def get_positions(self) -> list[Position]:
        self._assert_conectado()
        raw_positions = self.terminal.positions_get()
        if raw_positions is None:
            # positions_get() pode retornar () vazio ou None se erro (MT5 docs: None se erro)
            # Mas na verdade as vezes retorna None quando não tem? Não, None é falha
            # Se a tuple está vazia (), isso indica 0 posições.
            code, _ = self.terminal.last_error()
            if code != 1:  # 1 is SUCCESS in MT5? Treat None as falha
                raise MT5ConnectionError(f"positions_get() falhou: {self._erro()}")
            return []

        return [
            Position(
                ticket=int(p.ticket),
                identifier=int(getattr(p, "identifier", p.ticket)),
                symbol=str(p.symbol),
                volume=float(p.volume),
                type=int(p.type),
                sl=float(p.sl),
                tp=float(p.tp),
                price_open=float(p.price_open),
            )
            for p in raw_positions
        ]

    @retry(
        retry=retry_if_exception_type(MT5ConnectionError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    def get_candles(self, symbol: str, timeframe: int, count: int) -> Any:
        """Busca histórico OHLC (Velás) de um símbolo como DataFrame pandas."""
        self._assert_conectado()
        import pandas as pd

        raw_rates = self.terminal.copy_rates_from_pos(symbol, timeframe, 0, count)
        if raw_rates is None or len(raw_rates) == 0:
            raise MT5ConnectionError(f"copy_rates_from_pos({symbol}) vazio: {self._erro()}")

        df = pd.DataFrame(raw_rates)
        if "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"], unit="s")
        return df

    # -- context manager ----------------------------------------------------
    def __enter__(self) -> MT5Client:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()
