"use client";

import { AlertCircle, ShieldCheck } from "lucide-react";
import type { GateCriterion, PromotionStatus } from "@/lib/api";
import { formatNumber } from "@/lib/format";

function distanceLabel(criterion: GateCriterion): string {
  if (criterion.value === null) return "Sem dado suficiente para calcular ainda.";
  if (criterion.passed) return "Alvo atingido.";
  const gap = criterion.higher_is_better ? criterion.target - criterion.value : criterion.value - criterion.target;
  return `Faltam ${formatNumber(Math.abs(gap), 1)}${criterion.unit} para o alvo.`;
}

function GateCard({ criterion }: { criterion: GateCriterion }) {
  return (
    <div className="glass-card rounded-2xl p-5 flex items-start gap-4">
      <div className={`p-2 rounded-full ${criterion.passed ? "bg-success-bg text-success" : "bg-danger-bg text-danger"}`}>
        {criterion.passed ? <ShieldCheck className="w-5 h-5" /> : <AlertCircle className="w-5 h-5" />}
      </div>
      <div className="flex-1">
        <h3 className="font-semibold text-foreground">{criterion.label}</h3>
        <div className="flex items-baseline gap-2 mt-1">
          <span className="text-2xl font-bold text-foreground">
            {criterion.value === null ? "—" : `${formatNumber(criterion.value, 1)}${criterion.unit}`}
          </span>
          <span className="text-sm text-fg-muted">
            alvo {criterion.higher_is_better ? "≥" : "≤"} {formatNumber(criterion.target, 1)}
            {criterion.unit}
          </span>
        </div>
        <p className="text-xs text-fg-muted mt-2">{distanceLabel(criterion)}</p>
      </div>
    </div>
  );
}

export function GatesTab({ promotion }: { promotion: PromotionStatus | null }) {
  if (!promotion) {
    return (
      <div className="glass-card rounded-2xl p-6 animate-slide-up">
        <p className="text-sm text-fg-muted text-center py-8">Carregando status dos gates...</p>
      </div>
    );
  }

  const allPassed = promotion.criteria.every((c) => c.passed);

  return (
    <div className="space-y-6 animate-slide-up">
      <div className={`glass-card rounded-2xl p-5 flex items-center gap-3 ${allPassed ? "border-success/40" : ""}`}>
        <ShieldCheck className={`w-6 h-6 ${allPassed ? "text-success" : "text-fg-muted"}`} />
        <div>
          <p className="font-semibold text-foreground">
            {allPassed ? "Todos os gates atendidos" : "Ainda faltam critérios"}
          </p>
          <p className="text-xs text-fg-muted">
            {promotion.total_trades} trade(s) encerrado(s) no forward test. Passar nos gates não promove
            automaticamente — a promoção demo → real é sempre manual.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {promotion.criteria.map((criterion) => (
          <GateCard key={criterion.key} criterion={criterion} />
        ))}
      </div>
    </div>
  );
}
