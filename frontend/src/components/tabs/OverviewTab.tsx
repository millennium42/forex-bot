"use client";

import { Activity, DollarSign, Power, ShieldCheck, Target } from "lucide-react";
import { EquityCurveChart } from "@/components/charts/EquityCurveChart";
import { HourHeatmapChart } from "@/components/charts/HourHeatmapChart";
import { PnlHistogramChart } from "@/components/charts/PnlHistogramChart";
import { SentimentVsTechnicalChart } from "@/components/charts/SentimentVsTechnicalChart";
import { WinRateByPairChart } from "@/components/charts/WinRateByPairChart";
import { InsightCard } from "@/components/InsightCard";
import { MetricCard } from "@/components/MetricCard";
import type { AccountStatus, PromotionStatus, SignalRecord, TradeRecord } from "@/lib/api";
import { formatCurrency, formatPercent } from "@/lib/format";
import { computeInsights } from "@/lib/insights";
import type { ChartPalette } from "@/lib/palette";

function isToday(iso: string): boolean {
  const d = new Date(iso);
  const now = new Date();
  return d.toDateString() === now.toDateString();
}

export function OverviewTab({
  account,
  trades,
  signals,
  promotion,
  killSwitchActive,
  palette,
  onOpenKillSwitch,
}: {
  account: AccountStatus;
  trades: TradeRecord[];
  signals: SignalRecord[];
  promotion: PromotionStatus | null;
  killSwitchActive: boolean;
  palette: ChartPalette;
  onOpenKillSwitch: () => void;
}) {
  const closedToday = trades.filter((t) => t.outcome && t.closed_at && isToday(t.closed_at));
  const pnlToday = closedToday.reduce((sum, t) => sum + (t.outcome?.pnl ?? 0), 0);
  const winRateCriterion = promotion?.criteria.find((c) => c.key === "win_rate");
  const insights = computeInsights(trades);

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 animate-slide-up">
        <MetricCard
          title="Capital total"
          tooltip="Saldo atual (equity) incluindo lucros/prejuízos de trades abertos."
          value={formatCurrency(account.equity, account.currency)}
          icon={DollarSign}
        />
        <MetricCard
          title="Resultado hoje"
          tooltip="Soma do PnL dos trades encerrados hoje."
          value={formatCurrency(pnlToday, account.currency)}
          valueClassName={pnlToday >= 0 ? "text-success" : "text-danger"}
          subtitle={`${closedToday.length} trade(s) encerrado(s) hoje`}
          icon={Activity}
        />
        <MetricCard
          title="Taxa de acerto"
          tooltip="Percentual de trades vencedores no forward test (conta demo)."
          value={formatPercent(winRateCriterion?.value ?? null)}
          subtitle={`Alvo: ≥ ${winRateCriterion?.target ?? 55}%`}
          icon={Target}
        />
        <button type="button" onClick={onOpenKillSwitch} className="text-left">
          <MetricCard
            title="Kill switch"
            tooltip="Interrompe imediatamente qualquer nova ordem. Clique para acionar ou resetar."
            value={killSwitchActive ? "Ativo" : "Inativo"}
            valueClassName={killSwitchActive ? "text-danger" : "text-success"}
            subtitle="Clique para gerenciar"
            icon={killSwitchActive ? Power : ShieldCheck}
          />
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 animate-slide-up">
        {insights.map((insight) => (
          <InsightCard key={insight.id} insight={insight} />
        ))}
      </div>

      <div className="animate-slide-up">
        <EquityCurveChart trades={trades} currentEquity={account.equity} currency={account.currency} palette={palette} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 animate-slide-up">
        <PnlHistogramChart trades={trades} currency={account.currency} palette={palette} />
        <WinRateByPairChart trades={trades} palette={palette} />
        <SentimentVsTechnicalChart signals={signals} palette={palette} />
        <HourHeatmapChart trades={trades} palette={palette} />
      </div>
    </div>
  );
}
