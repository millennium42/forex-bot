"use client";

import { Activity } from "lucide-react";
import type { OpenPosition } from "@/lib/api";
import { formatCurrency, formatNumber } from "@/lib/format";

/**
 * Posições ao vivo lidas direto do broker (`GET /trades/`), não do status
 * `open` gravado no banco — o histórico do banco pode divergir do broker
 * (ordem manual, reconciliação pendente); esta lista é a verdade do momento.
 */
export function OpenPositionsCard({
  positions,
  currency,
}: {
  positions: OpenPosition[];
  currency: string | null;
}) {
  return (
    <div className="glass-card rounded-2xl p-6 animate-slide-up">
      <h2 className="text-xl font-semibold mb-4 flex items-center gap-2 text-foreground">
        <Activity className="w-5 h-5 text-primary" />
        Posições abertas agora (broker)
      </h2>
      {positions.length === 0 ? (
        <p className="text-sm text-fg-muted py-6 text-center">Nenhuma posição aberta no broker no momento.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="text-xs text-fg-muted uppercase bg-surface-2">
              <tr>
                <th className="px-4 py-3 rounded-tl-lg">Par</th>
                <th className="px-4 py-3">Lado</th>
                <th className="px-4 py-3">Entrada</th>
                <th className="px-4 py-3">Atual</th>
                <th className="px-4 py-3 text-right rounded-tr-lg">PnL flutuante</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.id} className="border-b border-card-border/60">
                  <td className="px-4 py-3 font-medium text-foreground">{p.pair}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`px-2 py-1 rounded text-xs font-semibold ${
                        p.side === "COMPRA" ? "bg-primary/20 text-primary" : "bg-danger/20 text-danger"
                      }`}
                    >
                      {p.side}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-fg-secondary">{formatNumber(p.entry, 5)}</td>
                  <td className="px-4 py-3 text-fg-secondary">{formatNumber(p.current, 5)}</td>
                  <td
                    className={`px-4 py-3 text-right font-semibold ${p.pnl >= 0 ? "text-success" : "text-danger"}`}
                  >
                    {formatCurrency(p.pnl, currency)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
