"use client";

import { Target } from "lucide-react";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ChartPalette } from "@/lib/palette";
import type { TradeRecord } from "@/lib/api";
import { ChartCard } from "./ChartCard";
import { tooltipContentStyle } from "./tooltipStyle";

const WIN_RATE_TARGET = 55;

interface PairStat {
  pair: string;
  winRate: number;
  total: number;
}

function buildPairStats(trades: TradeRecord[]): PairStat[] {
  const byPair = new Map<string, { wins: number; total: number }>();
  for (const trade of trades) {
    if (!trade.outcome) continue;
    const entry = byPair.get(trade.pair) ?? { wins: 0, total: 0 };
    entry.total += 1;
    if (trade.outcome.was_correct) entry.wins += 1;
    byPair.set(trade.pair, entry);
  }
  return Array.from(byPair.entries())
    .map(([pair, { wins, total }]) => ({ pair, winRate: (wins / total) * 100, total }))
    .sort((a, b) => b.winRate - a.winRate);
}

export function WinRateByPairChart({ trades, palette }: { trades: TradeRecord[]; palette: ChartPalette }) {
  const stats = buildPairStats(trades);

  return (
    <ChartCard
      title="Win rate por par"
      icon={Target}
      tooltip={`Percentual de trades vencedores por instrumento. Verde ≥ ${WIN_RATE_TARGET}% (alvo do gate de promoção).`}
      isEmpty={stats.length === 0}
    >
      <div className="h-[260px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={stats} layout="vertical" margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
            <XAxis type="number" domain={[0, 100]} stroke={palette.axis} fontSize={12} tickLine={false} axisLine={false} unit="%" />
            <YAxis type="category" dataKey="pair" stroke={palette.axis} fontSize={12} tickLine={false} axisLine={false} width={70} />
            <Tooltip
              contentStyle={tooltipContentStyle(palette)}
              formatter={(value, _name, item) => [
                `${Number(value).toFixed(1)}% (${item.payload.total} trades)`,
                "Win rate",
              ]}
            />
            <Bar dataKey="winRate" radius={[0, 4, 4, 0]}>
              {stats.map((stat) => (
                <Cell key={stat.pair} fill={stat.winRate >= WIN_RATE_TARGET ? palette.good : palette.critical} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}
