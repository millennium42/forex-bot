"use client";

import { Waves } from "lucide-react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ChartPalette } from "@/lib/palette";
import { formatTime } from "@/lib/format";
import type { SignalRecord } from "@/lib/api";
import { ChartCard } from "./ChartCard";
import { tooltipContentStyle } from "./tooltipStyle";

export function SentimentVsTechnicalChart({
  signals,
  palette,
}: {
  signals: SignalRecord[];
  palette: ChartPalette;
}) {
  const data = signals
    .slice()
    .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
    .map((s) => ({
      time: formatTime(s.created_at),
      pair: s.pair,
      technical: s.technical_score,
      sentiment: s.sentiment_score,
    }));

  return (
    <ChartCard
      title="Sentimento vs. técnica"
      icon={Waves}
      tooltip="Os dois componentes de entrada da fusão ao longo do tempo. Convergência entre eles é o que produz sinais de alta confiança."
      isEmpty={data.length === 0}
    >
      <div className="h-[260px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={palette.gridline} vertical={false} />
            <XAxis dataKey="time" stroke={palette.axis} fontSize={12} tickLine={false} axisLine={false} />
            <YAxis domain={[-1, 1]} stroke={palette.axis} fontSize={12} tickLine={false} axisLine={false} width={40} />
            <Tooltip contentStyle={tooltipContentStyle(palette)} labelFormatter={(_, payload) => payload?.[0]?.payload?.pair ?? ""} />
            <Legend wrapperStyle={{ fontSize: 12, color: palette.textSecondary }} />
            <Line
              type="monotone"
              dataKey="technical"
              name="Técnica"
              stroke={palette.seriesBlue}
              strokeWidth={2}
              dot={{ r: 3 }}
              connectNulls
            />
            <Line
              type="monotone"
              dataKey="sentiment"
              name="Sentimento"
              stroke={palette.seriesOrange}
              strokeWidth={2}
              dot={{ r: 3 }}
              connectNulls
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}
