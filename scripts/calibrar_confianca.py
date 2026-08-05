"""Calibra `MIN_SIGNAL_CONFIDENCE` (história 27) a partir dos sinais reais.

O limiar de confiança mínima não pode ser inventado: precisa vir da
distribuição de `signal.confidence` já gravada pelo runner. Este script lê
os sinais do banco, reporta os percentis 50/75/90/95 e quantos sinais
sobreviveriam se o limiar fosse fixado em cada um deles. Com menos de
`AMOSTRA_MINIMA` sinais, a amostra é insuficiente e o script recusa sugerir
um número — mais dado, não mais suposição.

Uso:
    uv run python scripts/calibrar_confianca.py
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from backend.db import session_scope
from backend.models.signal import Signal

AMOSTRA_MINIMA = 100
PERCENTIS = (50, 75, 90, 95)


def main() -> int:
    with session_scope() as session:
        confidencias = session.execute(select(Signal.confidence)).scalars().all()

    n = len(confidencias)
    print(f"Sinais gravados: {n}")

    if n < AMOSTRA_MINIMA:
        print(
            f"Amostra insuficiente (mínimo {AMOSTRA_MINIMA} sinais). Não é possível "
            "calibrar MIN_SIGNAL_CONFIDENCE a partir de dados reais ainda — rode o "
            "bot (ou o backtester persistindo sinais) até acumular mais histórico "
            "antes de fixar um número novo."
        )
        return 0

    valores = sorted(confidencias)
    cortes = statistics.quantiles(valores, n=100)  # cortes[p-1] = percentil p

    print("Percentis de signal.confidence e sobrevivência por limiar:")
    for p in PERCENTIS:
        limiar = cortes[p - 1]
        sobrevivem = sum(1 for c in valores if c >= limiar)
        pct = (sobrevivem / n) * 100.0
        print(f"  p{p}: confidence >= {limiar:.3f} -> {sobrevivem}/{n} sinais ({pct:.1f}%)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
