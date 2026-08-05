"use client";

import { Clock3 } from "lucide-react";
import type { ChartPalette } from "@/lib/palette";
import type { TradeRecord } from "@/lib/api";
import { ChartCard } from "./ChartCard";

interface HourStat {
  hour: number;
  avgPnl: number;
  count: number;
}

function buildHourStats(trades: TradeRecord[]): HourStat[] {
  const buckets = new Map<number, { total: number; count: number }>();
  for (const trade of trades) {
    if (!trade.outcome || !trade.closed_at) continue;
    const hour = new Date(trade.closed_at).getHours();
    const entry = buckets.get(hour) ?? { total: 0, count: 0 };
    entry.total += trade.outcome.pnl;
    entry.count += 1;
    buckets.set(hour, entry);
  }
  return Array.from({ length: 24 }, (_, hour) => {
    const entry = buckets.get(hour);
    return { hour, avgPnl: entry ? entry.total / entry.count : 0, count: entry?.count ?? 0 };
  });
}

/** Opacidade proporcional à magnitude do PnL médio, saturando em `maxAbs`. */
function intensity(value: number, maxAbs: number): number {
  if (maxAbs === 0) return 0;
  return Math.min(Math.abs(value) / maxAbs, 1);
}

export function HourHeatmapChart({ trades, palette }: { trades: TradeRecord[]; palette: ChartPalette }) {
  const stats = buildHourStats(trades);
  const hasData = stats.some((s) => s.count > 0);
  const maxAbs = Math.max(...stats.map((s) => Math.abs(s.avgPnl)), 0.0001);

  return (
    <ChartCard
      title="Resultado por hora do dia"
      icon={Clock3}
      tooltip="PnL médio dos trades encerrados, agrupado pela hora de fechamento. Verde = lucro médio, vermelho = prejuízo médio; cinza = sem trades naquela hora."
      isEmpty={!hasData}
      emptyMessage="Sem trades encerrados o suficiente para montar o heatmap."
    >
      <div className="flex flex-col gap-1">
        <div className="grid grid-cols-12 gap-1 sm:grid-cols-24">
          {stats.map((stat) => {
            const bg =
              stat.count === 0
                ? palette.divergingNeutral
                : stat.avgPnl >= 0
                  ? palette.good
                  : palette.critical;
            const alpha = stat.count === 0 ? 0.4 : 0.25 + 0.75 * intensity(stat.avgPnl, maxAbs);
            return (
              <div
                key={stat.hour}
                title={
                  stat.count === 0
                    ? `${stat.hour}h — sem trades`
                    : `${stat.hour}h — PnL médio ${stat.avgPnl.toFixed(2)} (${stat.count} trade(s))`
                }
                className="aspect-square rounded flex items-end justify-center text-[9px] text-fg-muted"
                style={{ backgroundColor: bg, opacity: alpha }}
              >
                <span className="mb-0.5 mix-blend-luminosity">{stat.hour}</span>
              </div>
            );
          })}
        </div>
        <div className="flex items-center justify-end gap-3 mt-2 text-xs text-fg-muted">
          <span className="flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: palette.critical }} /> prejuízo
          </span>
          <span className="flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: palette.divergingNeutral }} /> sem dado
          </span>
          <span className="flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: palette.good }} /> lucro
          </span>
        </div>
      </div>
    </ChartCard>
  );
}
