"""O MT5 devolve hora do servidor de trading, não UTC.

Bug encontrado em produção: `closed_at` (vindo do MT5) ficava 3h à frente de
`opened_at` (relógio do Python), e a duração mínima de 239 outcomes reais era de
3h04 — o offset exato do MetaQuotes-Demo, que opera em GMT+3. Trades que duravam
minutos apareciam como se durassem horas.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from backend.collection.mt5_client import MT5Client
from backend.config import Settings

UMA_HORA = 3600


class FakeTerminal:
    """Terminal cujo relógio está deslocado do UTC por um offset configurável."""

    def __init__(self, offset_horas: float = 0.0) -> None:
        self.offset = offset_horas * UMA_HORA
        self._deal_ts: int | None = None

    def _agora_do_servidor(self) -> int:
        return int(datetime.now(UTC).timestamp() + self.offset)

    def initialize(self, *a: Any, **k: Any) -> bool:
        return True

    def login(self, *a: Any, **k: Any) -> bool:
        return True

    def shutdown(self) -> None: ...

    def last_error(self) -> tuple[int, str]:
        return (0, "")

    def account_info(self) -> Any:
        return SimpleNamespace(
            login=1,
            server="Fake",
            currency="USD",
            balance=1e5,
            equity=1e5,
            margin=0.0,
            margin_free=1e5,
            leverage=100,
            trade_mode=0,
        )

    def symbol_info_tick(self, _symbol: str) -> Any:
        return SimpleNamespace(time=self._agora_do_servidor(), bid=1.0850, ask=1.0852)

    def symbol_info(self, _symbol: str) -> Any:
        return SimpleNamespace(volume_min=0.01, trade_contract_size=100_000.0)

    def symbol_select(self, _symbol: str, _enable: bool) -> bool:
        return True

    def positions_get(self) -> Any:
        return ()

    def order_send(self, request: dict[str, Any]) -> Any:
        return None

    def copy_rates_from_pos(self, s: str, tf: int, start: int, count: int) -> Any:
        return None

    def history_deals_get(self, *a: Any, **k: Any) -> Any:
        ts = self._deal_ts if self._deal_ts is not None else self._agora_do_servidor()
        return (
            SimpleNamespace(
                ticket=1,
                order=1,
                position_id=1,
                symbol="EURUSD",
                price=1.1,
                profit=5.0,
                volume=0.01,
                entry=1,
                time=ts,
            ),
        )

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


def _client(offset_horas: float) -> MT5Client:
    c = MT5Client(terminal=FakeTerminal(offset_horas), settings=Settings(_env_file=None))
    c.connect()
    return c


@pytest.mark.parametrize("offset", [3.0, -5.0, 2.0, 5.5])
def test_tick_volta_para_utc_real(offset: float) -> None:
    """O tick precisa refletir o instante real, não o relógio do servidor."""
    tick = _client(offset).get_tick("EURUSD")

    erro = abs((tick.time - datetime.now(UTC)).total_seconds())
    assert erro < 60, f"tick {erro:.0f}s fora do UTC real com offset de {offset}h"


def test_sem_offset_nada_muda() -> None:
    """Servidor já em UTC: a conversão não pode introduzir deslocamento."""
    tick = _client(0.0).get_tick("EURUSD")

    assert abs((tick.time - datetime.now(UTC)).total_seconds()) < 60


@pytest.mark.parametrize("offset", [3.0, -5.0])
def test_deal_de_saida_tambem_e_normalizado(offset: float) -> None:
    """O `closed_at` vem do deal: sem normalizar, a duração do trade sai inflada."""
    deal = _client(offset).get_exit_deal(1)

    assert deal is not None
    assert abs((deal.time - datetime.now(UTC)).total_seconds()) < 60


def test_duracao_de_trade_nao_e_inflada_pelo_offset() -> None:
    """Reproduz o bug: trade de 5 min não pode aparecer como 3h05.

    `opened_at` vem do relógio do Python (UTC correto) e `closed_at` do MT5.
    Com o offset não tratado, a diferença entre os dois carregava as 3h.
    """
    client = _client(3.0)
    abertura = datetime.now(UTC) - timedelta(minutes=5)

    deal = client.get_exit_deal(1)

    assert deal is not None
    duracao = (deal.time - abertura).total_seconds()
    assert 4 * 60 < duracao < 6 * 60, f"duração de {duracao / 3600:.1f}h — offset vazou"


def test_offset_e_arredondado_para_meia_hora() -> None:
    """Fusos são múltiplos de 30min; o arredondamento absorve latência da chamada."""
    client = _client(3.0)

    assert client._offset_servidor == timedelta(hours=3)


def test_offset_negativo_e_suportado() -> None:
    """Servidor a oeste de Greenwich (ex.: broker nas Américas)."""
    client = _client(-5.0)

    assert client._offset_servidor == timedelta(hours=-5)
