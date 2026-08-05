"use client";

import { Lightbulb, TrendingDown, TrendingUp } from "lucide-react";
import type { Insight } from "@/lib/insights";

const TONE_STYLES: Record<Insight["tone"], { icon: typeof Lightbulb; className: string }> = {
  good: { icon: TrendingUp, className: "text-success bg-success-bg border-success/30" },
  bad: { icon: TrendingDown, className: "text-danger bg-danger-bg border-danger/30" },
  neutral: { icon: Lightbulb, className: "text-fg-secondary bg-surface-2 border-surface-2-border" },
};

export function InsightCard({ insight }: { insight: Insight }) {
  const { icon: Icon, className } = TONE_STYLES[insight.tone];
  return (
    <div className={`rounded-2xl p-5 border glass-card`}>
      <div className={`inline-flex items-center gap-2 px-2.5 py-1 rounded-full text-xs font-semibold border mb-3 ${className}`}>
        <Icon className="h-3.5 w-3.5" />
        Insight
      </div>
      <p className="text-sm text-fg-secondary mb-1">{insight.question}</p>
      <p className="text-base font-medium text-foreground">{insight.answer}</p>
    </div>
  );
}
