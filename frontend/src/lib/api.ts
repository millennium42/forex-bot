/**
 * Tipos e funções de acesso à API. Todo fetch passa por `/api/*`, que o
 * `next.config.ts` reescreve para o backend FastAPI (127.0.0.1:8001).
 */

export interface AccountStatus {
  balance: number | null;
  equity: number | null;
  currency: string | null;
  connected: boolean;
  demo: boolean | null;
  detail: string | null;
}

export interface OpenPosition {
  id: string;
  pair: string;
  side: string;
  entry: number;
  current: number;
  pnl: number;
  time: string;
}

export interface SignalSummary {
  id: number;
  direction: string;
  confidence: number;
  fused_score: number;
  sentiment_score: number | null;
  technical_score: number | null;
  weight_version: string;
  inputs: Record<string, unknown>;
}

export interface OutcomeSummary {
  exit_price: number;
  pnl: number;
  pnl_pct: number;
  duration_seconds: number;
  predicted_direction: string | null;
  actual_direction: string;
  was_correct: boolean;
}

export interface TradeRecord {
  id: number;
  client_request_id: string;
  pair: string;
  side: string;
  status: string;
  volume: number;
  entry_price: number | null;
  stop_loss: number;
  take_profit: number | null;
  trading_mode: string;
  opened_at: string | null;
  closed_at: string | null;
  created_at: string;
  signal: SignalSummary | null;
  outcome: OutcomeSummary | null;
}

export interface SignalRecord {
  id: number;
  pair: string;
  direction: string;
  confidence: number;
  fused_score: number;
  sentiment_score: number | null;
  technical_score: number | null;
  weight_version: string;
  inputs: Record<string, unknown>;
  created_at: string;
}

export interface AuditEntry {
  id: number;
  event_type: string;
  client_request_id: string | null;
  actor: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface GateCriterion {
  key: string;
  label: string;
  value: number | null;
  target: number;
  unit: string;
  higher_is_better: boolean;
  passed: boolean;
}

export interface GateStatus {
  win_rate_ok: boolean;
  sharpe_ok: boolean;
  max_drawdown_ok: boolean;
  profit_factor_ok: boolean;
  deviation_ok: boolean;
  trades_ok: boolean;
}

export interface PromotionStatus {
  gates: GateStatus;
  criteria: GateCriterion[];
  total_trades: number;
}

export interface KillSwitchStatus {
  active: boolean;
}

export interface StrategyPerformance {
  strategy: string;
  trades: number;
  win_rate: number | null;
  net_pnl: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  breakeven_win_rate: number | null;
}

export interface TradeHistoryFilters {
  symbol?: string;
  status?: string;
  since?: string;
  until?: string;
  limit?: number;
}

async function getJson<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const query = params
    ? Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== "")
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
        .join("&")
    : "";
  const url = `/api${path}${query ? `?${query}` : ""}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${path} respondeu ${res.status}`);
  return res.json() as Promise<T>;
}

export function getAccountStatus(): Promise<AccountStatus> {
  return getJson<AccountStatus>("/system/account");
}

export function getOpenPositions(): Promise<OpenPosition[]> {
  return getJson<OpenPosition[]>("/trades/");
}

export function getTradeHistory(filters: TradeHistoryFilters = {}): Promise<TradeRecord[]> {
  return getJson<TradeRecord[]>("/trades/history", { ...filters });
}

export function getSignals(limit = 50): Promise<SignalRecord[]> {
  return getJson<SignalRecord[]>("/signals/", { limit });
}

export function getAuditLog(eventType?: string, limit = 100): Promise<AuditEntry[]> {
  return getJson<AuditEntry[]>("/audit/", { event_type: eventType, limit });
}

export function getPromotionStatus(): Promise<PromotionStatus> {
  return getJson<PromotionStatus>("/promotion/status");
}

export function getKillSwitchStatus(): Promise<KillSwitchStatus> {
  return getJson<KillSwitchStatus>("/system/kill-switch");
}

export function getStrategyPerformance(): Promise<StrategyPerformance[]> {
  return getJson<StrategyPerformance[]>("/strategies/performance");
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} respondeu ${res.status}`);
  return res.json() as Promise<T>;
}

export function triggerKillSwitch(actor: string, reason: string): Promise<KillSwitchStatus> {
  return postJson<KillSwitchStatus>("/system/kill-switch/trigger", { actor, reason });
}

export function resetKillSwitch(actor: string, reason: string): Promise<KillSwitchStatus> {
  return postJson<KillSwitchStatus>("/system/kill-switch/reset", { actor, reason });
}
