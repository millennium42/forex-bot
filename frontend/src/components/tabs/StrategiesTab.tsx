"use client";

import { AlertTriangle, ChartColumnBig } from "lucide-react";
import type { StrategyPerformance } from "@/lib/api";
import { formatCurrency, formatPercent } from "@/lib/format";

function abaixoDoBreakeven(row: StrategyPerformance): boolean {
  return row.win_rate !== null && row.breakeven_win_rate !== null && row.win_rate < row.breakeven_win_rate;
}

export function StrategiesTab({
  performance,
  currency,
}: {
  performance: StrategyPerformance[];
  currency: string | null;
}) {
  return (
    <div className="glass-card rounded-2xl p-6 animate-slide-up">
      <h2 className="text-xl font-semibold flex items-center gap-2 text-foreground mb-5">
        <ChartColumnBig className="w-5 h-5 text-primary" />
        Performance por estratégia
      </h2>

      {performance.length === 0 ? (
        <p className="text-sm text-fg-muted py-8 text-center">Nenhuma estratégia configurada.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="text-xs text-fg-muted uppercase bg-surface-2">
              <tr>
                <th className="px-4 py-3 rounded-tl-lg">Estratégia</th>
                <th className="px-4 py-3 text-right">Trades</th>
                <th className="px-4 py-3 text-right">Win rate</th>
                <th className="px-4 py-3 text-right">Win rate breakeven</th>
                <th className="px-4 py-3 text-right">Ganho médio</th>
                <th className="px-4 py-3 text-right">Perda média</th>
                <th className="px-4 py-3 text-right rounded-tr-lg">PnL líquido</th>
              </tr>
            </thead>
            <tbody>
              {performance.map((row) => {
                const alerta = abaixoDoBreakeven(row);
                return (
                  <tr key={row.strategy} className="border-b border-card-border/60">
                    <td className="px-4 py-3 font-medium text-foreground">
                      <div className="flex items-center gap-2">
                        {row.strategy}
                        {alerta && (
                          <span
                            title="Win rate observado abaixo do breakeven implicado pelo RR"
                            className="flex items-center gap-1 px-2 py-0.5 rounded text-xs font-semibold bg-danger/20 text-danger"
                          >
                            <AlertTriangle className="w-3 h-3" />
                            abaixo do breakeven
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right text-fg-secondary">
                      {row.trades === 0 ? (
                        <span className="text-fg-muted">Sem trade encerrado</span>
                      ) : (
                        row.trades
                      )}
                    </td>
                    <td className={`px-4 py-3 text-right font-semibold ${alerta ? "text-danger" : "text-fg-secondary"}`}>
                      {formatPercent(row.win_rate)}
                    </td>
                    <td className="px-4 py-3 text-right text-fg-secondary">{formatPercent(row.breakeven_win_rate)}</td>
                    <td className="px-4 py-3 text-right text-success">{formatCurrency(row.avg_win, currency)}</td>
                    <td className="px-4 py-3 text-right text-danger">{formatCurrency(row.avg_loss, currency)}</td>
                    <td
                      className={`px-4 py-3 text-right font-semibold ${
                        row.net_pnl === null ? "text-fg-muted" : row.net_pnl >= 0 ? "text-success" : "text-danger"
                      }`}
                    >
                      {formatCurrency(row.net_pnl, currency)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
