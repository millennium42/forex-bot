"use client";

import { Activity, Clock, TrendingDown, TrendingUp } from "lucide-react";
import type { SignalRecord } from "@/lib/api";
import { formatDateTime, formatNumber, formatPercent } from "@/lib/format";

const DIRECTION_STYLE: Record<string, { icon: typeof TrendingUp; className: string; label: string }> = {
  buy: { icon: TrendingUp, className: "bg-primary/20 text-primary", label: "COMPRA" },
  sell: { icon: TrendingDown, className: "bg-danger/20 text-danger", label: "VENDA" },
  hold: { icon: Activity, className: "bg-surface-2 text-fg-muted", label: "MANTER" },
};

export function SignalsTab({
  signals,
  onSelectSignal,
}: {
  signals: SignalRecord[];
  onSelectSignal: (signal: SignalRecord) => void;
}) {
  return (
    <div className="glass-card rounded-2xl p-6 animate-slide-up">
      <h2 className="text-xl font-semibold mb-5 flex items-center gap-2 text-foreground">
        <Clock className="w-5 h-5 text-primary" />
        Sinais recentes
      </h2>

      {signals.length === 0 ? (
        <p className="text-sm text-fg-muted py-8 text-center">Nenhum sinal emitido ainda.</p>
      ) : (
        <div className="space-y-3">
          {signals.map((signal) => {
            const style = DIRECTION_STYLE[signal.direction] ?? DIRECTION_STYLE.hold;
            const Icon = style.icon;
            return (
              <button
                key={signal.id}
                type="button"
                onClick={() => onSelectSignal(signal)}
                className="w-full flex items-center justify-between p-4 rounded-xl bg-surface-2 border border-surface-2-border hover:bg-surface-2/70 transition-colors text-left"
              >
                <div className="flex items-center gap-4">
                  <div className={`flex items-center justify-center w-10 h-10 rounded-lg ${style.className}`}>
                    <Icon className="w-5 h-5" />
                  </div>
                  <div>
                    <h3 className="font-semibold text-foreground">{signal.pair}</h3>
                    <p className="text-xs text-fg-muted">
                      técnica {formatNumber(signal.technical_score, 2)} · sentimento{" "}
                      {formatNumber(signal.sentiment_score, 2)} · {formatDateTime(signal.created_at)}
                    </p>
                  </div>
                </div>
                <div className="text-right">
                  <span className={`text-sm font-bold block ${style.className.split(" ")[1]}`}>{style.label}</span>
                  <span className="text-xs text-fg-muted mt-0.5 block">Conf: {formatPercent(signal.confidence * 100, 0)}</span>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
