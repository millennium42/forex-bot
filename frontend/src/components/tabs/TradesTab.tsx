"use client";

import { ArrowDownRight, ArrowUpRight, ListFilter } from "lucide-react";
import { useMemo, useState } from "react";
import type { TradeRecord } from "@/lib/api";
import { formatCurrency, formatDateTime } from "@/lib/format";

const STATUS_LABEL: Record<string, string> = {
  pending: "Pendente",
  open: "Aberto",
  closed: "Fechado",
  rejected: "Rejeitado",
};

const PERIODS = [
  { id: "all", label: "Todo o período", days: null },
  { id: "7d", label: "Últimos 7 dias", days: 7 },
  { id: "30d", label: "Últimos 30 dias", days: 30 },
] as const;

export function TradesTab({
  trades,
  currency,
  onSelectTrade,
}: {
  trades: TradeRecord[];
  currency: string | null;
  onSelectTrade: (trade: TradeRecord) => void;
}) {
  const [symbolFilter, setSymbolFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [periodFilter, setPeriodFilter] = useState<(typeof PERIODS)[number]["id"]>("all");

  const symbols = useMemo(() => Array.from(new Set(trades.map((t) => t.pair))).sort(), [trades]);

  const filtered = useMemo(() => {
    const period = PERIODS.find((p) => p.id === periodFilter);
    // eslint-disable-next-line react-hooks/purity -- filtro por janela de tempo depende do relógio real
    const cutoff = period?.days ? Date.now() - period.days * 24 * 60 * 60 * 1000 : null;
    return trades.filter((t) => {
      if (symbolFilter !== "all" && t.pair !== symbolFilter) return false;
      if (statusFilter !== "all" && t.status !== statusFilter) return false;
      if (cutoff !== null && new Date(t.created_at).getTime() < cutoff) return false;
      return true;
    });
  }, [trades, symbolFilter, statusFilter, periodFilter]);

  return (
    <div className="glass-card rounded-2xl p-6 animate-slide-up">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
        <h2 className="text-xl font-semibold flex items-center gap-2 text-foreground">
          <ListFilter className="w-5 h-5 text-primary" />
          Trades
        </h2>
        <div className="flex flex-wrap gap-2">
          <select
            value={symbolFilter}
            onChange={(e) => setSymbolFilter(e.target.value)}
            className="bg-surface-2 border border-surface-2-border text-sm rounded-lg px-3 py-1.5 text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
          >
            <option value="all">Todos os pares</option>
            {symbols.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="bg-surface-2 border border-surface-2-border text-sm rounded-lg px-3 py-1.5 text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
          >
            <option value="all">Todos os status</option>
            {Object.entries(STATUS_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <select
            value={periodFilter}
            onChange={(e) => setPeriodFilter(e.target.value as (typeof PERIODS)[number]["id"])}
            className="bg-surface-2 border border-surface-2-border text-sm rounded-lg px-3 py-1.5 text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
          >
            {PERIODS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {filtered.length === 0 ? (
        <p className="text-sm text-fg-muted py-8 text-center">Nenhum trade encontrado com esses filtros.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="text-xs text-fg-muted uppercase bg-surface-2">
              <tr>
                <th className="px-4 py-3 rounded-tl-lg">Par</th>
                <th className="px-4 py-3">Lado</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Entrada</th>
                <th className="px-4 py-3">Aberto em</th>
                <th className="px-4 py-3 text-right rounded-tr-lg">PnL</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((trade) => (
                <tr
                  key={trade.id}
                  onClick={() => onSelectTrade(trade)}
                  className="border-b border-card-border/60 hover:bg-surface-2 transition-colors cursor-pointer"
                >
                  <td className="px-4 py-3 font-medium text-foreground">{trade.pair}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`px-2 py-1 rounded text-xs font-semibold ${
                        trade.side === "buy" ? "bg-primary/20 text-primary" : "bg-danger/20 text-danger"
                      }`}
                    >
                      {trade.side === "buy" ? "COMPRA" : "VENDA"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-fg-secondary">{STATUS_LABEL[trade.status] ?? trade.status}</td>
                  <td className="px-4 py-3 text-fg-secondary">{trade.entry_price?.toFixed(5) ?? "—"}</td>
                  <td className="px-4 py-3 text-fg-secondary">{formatDateTime(trade.opened_at)}</td>
                  <td className="px-4 py-3 text-right font-semibold">
                    {trade.outcome ? (
                      <span
                        className={`flex justify-end items-center gap-1 ${trade.outcome.pnl >= 0 ? "text-success" : "text-danger"}`}
                      >
                        {trade.outcome.pnl >= 0 ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                        {formatCurrency(Math.abs(trade.outcome.pnl), currency)}
                      </span>
                    ) : (
                      <span className="text-fg-muted">—</span>
                    )}
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
