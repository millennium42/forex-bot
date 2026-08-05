"use client";

import { BarChart2 } from "lucide-react";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ChartPalette } from "@/lib/palette";
import type { TradeRecord } from "@/lib/api";
import { ChartCard } from "./ChartCard";
import { tooltipContentStyle } from "./tooltipStyle";

const BIN_COUNT = 8;

interface Bin {
  label: string;
  count: number;
  midpoint: number;
}

/**
 * Casas decimais necessárias para que faixas vizinhas não fiquem com o mesmo
 * rótulo. Com P&L entre 0,08 e 0,38 a faixa mede 0,0375: duas casas ainda
 * colapsam em "0,03 a 0,03", e o gráfico vira oito colunas indistinguíveis.
 */
function decimalsFor(width: number): number {
  if (width <= 0) return 2;
  return Math.min(6, Math.max(2, Math.ceil(-Math.log10(width)) + 1));
}

function buildBins(pnls: number[]): Bin[] {
  const min = Math.min(...pnls);
  const max = Math.max(...pnls);
  if (min === max) {
    return [{ label: min.toFixed(2), count: pnls.length, midpoint: min }];
  }
  const width = (max - min) / BIN_COUNT;
  const casas = decimalsFor(width);
  const bins: Bin[] = Array.from({ length: BIN_COUNT }, (_, i) => {
    const start = min + i * width;
    const end = start + width;
    return {
      label: `${start.toFixed(casas)} a ${end.toFixed(casas)}`,
      count: 0,
      midpoint: (start + end) / 2,
    };
  });
  for (const pnl of pnls) {
    const idx = Math.min(Math.floor((pnl - min) / width), BIN_COUNT - 1);
    bins[idx].count += 1;
  }
  return bins;
}

export function PnlHistogramChart({
  trades,
  currency,
  palette,
}: {
  trades: TradeRecord[];
  currency: string | null;
  palette: ChartPalette;
}) {
  const pnls = trades.map((t) => t.outcome?.pnl).filter((v): v is number => v !== null && v !== undefined);
  const bins = pnls.length > 0 ? buildBins(pnls) : [];

  return (
    <ChartCard
      title="Distribuição de P&L"
      icon={BarChart2}
      tooltip="Quantos trades encerrados caíram em cada faixa de lucro/prejuízo."
      isEmpty={bins.length === 0}
    >
      <div className="h-[260px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={bins} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
            <XAxis dataKey="label" stroke={palette.axis} fontSize={10} tickLine={false} axisLine={false} interval={1} />
            <YAxis stroke={palette.axis} fontSize={12} tickLine={false} axisLine={false} allowDecimals={false} width={30} />
            <Tooltip
              contentStyle={tooltipContentStyle(palette)}
              formatter={(value) => [`${value} trade(s)`, "Frequência"]}
              labelFormatter={(label) => `Faixa (${currency ?? "?"}): ${label}`}
            />
            <Bar dataKey="count" radius={[4, 4, 0, 0]}>
              {/* A key é a posição da faixa, não o rótulo: rótulos podem
                  coincidir quando os valores são muito próximos, e duas Cells
                  com a mesma key fazem o React duplicar ou omitir barras. */}
              {bins.map((bin, i) => (
                <Cell key={i} fill={bin.midpoint >= 0 ? palette.good : palette.critical} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}
