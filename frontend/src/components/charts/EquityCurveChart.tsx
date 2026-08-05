"use client";

import { TrendingUp } from "lucide-react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ChartPalette } from "@/lib/palette";
import { formatCurrency, formatDateTime } from "@/lib/format";
import type { TradeRecord } from "@/lib/api";
import { ChartCard } from "./ChartCard";
import { tooltipContentStyle } from "./tooltipStyle";

interface Point {
  time: string;
  fullTime: string;
  equity: number;
}

/**
 * Não existe histórico de equity salvo (o MT5 só dá o valor atual). A curva
 * aqui é reconstruída de trás para frente: parte do equity atual e desconta
 * o PnL de cada trade encerrado, andando no tempo — dado real, não inventado.
 */
function buildEquityCurve(trades: TradeRecord[], currentEquity: number | null): Point[] {
  const closed = trades
    .filter((t) => t.outcome !== null)
    .slice()
    .sort((a, b) => new Date(a.closed_at ?? a.created_at).getTime() - new Date(b.closed_at ?? b.created_at).getTime());

  if (closed.length === 0) return [];

  const totalPnl = closed.reduce((sum, t) => sum + (t.outcome?.pnl ?? 0), 0);
  let running = (currentEquity ?? totalPnl) - totalPnl;

  const points: Point[] = [{ time: "início", fullTime: "", equity: running }];
  for (const trade of closed) {
    running += trade.outcome?.pnl ?? 0;
    const ts = trade.closed_at ?? trade.created_at;
    points.push({ time: formatDateTime(ts).split(" ")[1] ?? ts, fullTime: formatDateTime(ts), equity: running });
  }
  return points;
}

export function EquityCurveChart({
  trades,
  currentEquity,
  currency,
  palette,
}: {
  trades: TradeRecord[];
  currentEquity: number | null;
  currency: string | null;
  palette: ChartPalette;
}) {
  const data = buildEquityCurve(trades, currentEquity);

  return (
    <ChartCard
      title="Curva de capital"
      icon={TrendingUp}
      tooltip="Evolução do equity reconstruída a partir dos trades encerrados, terminando no valor atual da conta."
      isEmpty={data.length === 0}
      emptyMessage="Nenhum trade encerrado ainda — a curva aparece após o primeiro resultado."
    >
      <div className="h-[260px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="colorEquity" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={palette.seriesBlue} stopOpacity={0.35} />
                <stop offset="95%" stopColor={palette.seriesBlue} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke={palette.gridline} vertical={false} />
            <XAxis dataKey="time" stroke={palette.axis} fontSize={12} tickLine={false} axisLine={false} />
            <YAxis
              domain={["dataMin - 2", "dataMax + 2"]}
              stroke={palette.axis}
              fontSize={12}
              tickLine={false}
              axisLine={false}
              tickFormatter={(val: number) => formatCurrency(val, currency)}
              width={80}
            />
            <Tooltip
              contentStyle={tooltipContentStyle(palette)}
              labelFormatter={(_, payload) => payload?.[0]?.payload?.fullTime ?? ""}
              formatter={(value) => [formatCurrency(Number(value ?? 0), currency), "Capital"]}
            />
            <Area
              type="monotone"
              dataKey="equity"
              stroke={palette.seriesBlue}
              strokeWidth={3}
              fillOpacity={1}
              fill="url(#colorEquity)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}
