import type { AuditEntry } from "./api";

/**
 * Tipos de evento de auditoria relevantes para "por que a última ordem foi
 * bloqueada" (história 36). `order_blocked` cobre confiança/cooldown/margem/
 * ATR/stop/risco (gravados pelo runner só nesta história); kill switch e
 * drawdown já persistem seu próprio motivo desde as histórias 18/29.
 */
const RELEVANT_TYPES = new Set([
  "order_placed",
  "order_blocked",
  "order_rejected",
  "kill_switch_triggered",
  "kill_switch_reset",
  "drawdown_limit_triggered",
  "drawdown_limit_reset",
]);

const MOTIVO_LABEL: Record<string, string> = {
  confianca_insuficiente: "Confiança do sinal abaixo do limiar mínimo",
  cooldown: "Mesma leitura de mercado ainda em cooldown",
  margem_insuficiente: "Margem livre da conta insuficiente",
  atr_invalido: "Volatilidade (ATR) não medida",
  stop_invalido: "Stop loss calculado ficou inválido",
  risco_insuficiente: "Risco configurado não cobre o lote mínimo do broker",
};

export interface LastBlock {
  label: string;
  detail: string | null;
  at: string;
  blocked: boolean;
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

/** Deriva o motivo do bloqueio mais recente a partir do audit log já carregado (mais recente primeiro). */
export function lastBlockReason(entries: AuditEntry[]): LastBlock | null {
  const last = entries.find((e) => RELEVANT_TYPES.has(e.event_type));
  if (!last) return null;

  if (last.event_type === "order_placed") {
    return { label: "Nenhum bloqueio — a última ordem foi executada", detail: null, at: last.created_at, blocked: false };
  }
  if (last.event_type === "kill_switch_reset" || last.event_type === "drawdown_limit_reset") {
    return { label: "Nenhum bloqueio ativo — último evento foi um reset manual", detail: null, at: last.created_at, blocked: false };
  }
  if (last.event_type === "kill_switch_triggered") {
    return { label: "Kill switch ativo", detail: asString(last.payload.reason), at: last.created_at, blocked: true };
  }
  if (last.event_type === "drawdown_limit_triggered") {
    return { label: "Drawdown a partir do pico de equity bloqueou o ciclo", detail: asString(last.payload.reason), at: last.created_at, blocked: true };
  }
  if (last.event_type === "order_rejected") {
    return {
      label: asString(last.payload.reason) ?? "Ordem rejeitada pelo risk manager",
      detail: asString(last.payload.symbol),
      at: last.created_at,
      blocked: true,
    };
  }

  // order_blocked
  const motivo = asString(last.payload.motivo) ?? "desconhecido";
  return {
    label: MOTIVO_LABEL[motivo] ?? motivo,
    detail: asString(last.payload.symbol),
    at: last.created_at,
    blocked: true,
  };
}
