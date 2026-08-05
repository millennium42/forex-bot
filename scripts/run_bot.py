"""Entrypoint do motor autônomo.

Uso:
    uv run python scripts/run_bot.py                      # EURUSD e GBPUSD, ciclo de 60s
    uv run python scripts/run_bot.py --symbols EURUSD     # um par só
    uv run python scripts/run_bot.py --cycles 1           # um ciclo e sai (dry run)

Exige terminal MetaTrader 5 aberto e conectado. Sem ele o processo encerra com
erro — nunca simula.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.collection.mt5_client import MT5ConnectionError, MT5ModeMismatchError
from backend.config import get_settings
from backend.execution.runner import BotRunner


def main() -> int:
    parser = argparse.ArgumentParser(description="Motor autônomo do forex-bot")
    parser.add_argument("--symbols", nargs="+", default=["EURUSD", "GBPUSD"])
    parser.add_argument("--interval", type=int, default=60, help="segundos entre ciclos")
    parser.add_argument(
        "--cycles", type=int, default=None, help="número de ciclos; omitido roda indefinidamente"
    )
    args = parser.parse_args()

    settings = get_settings()
    modo = settings.effective_trading_mode.value
    print(f"forex-bot | modo={modo} | símbolos={' '.join(args.symbols)}")
    if settings.is_real_trading:
        print("ATENÇÃO: modo REAL habilitado. Ordens usarão capital real.")

    try:
        BotRunner(symbols=args.symbols, interval_seconds=args.interval, settings=settings).run(
            max_cycles=args.cycles
        )
    except (MT5ConnectionError, MT5ModeMismatchError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nEncerrado pelo operador.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
