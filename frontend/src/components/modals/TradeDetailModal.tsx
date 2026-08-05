"use client";

import { formatCurrency, formatDateTime, formatDuration, formatNumber, formatPercent } from "@/lib/format";
import type { TradeRecord } from "@/lib/api";
import { Modal, ModalField } from "./Modal";

const DIRECTION_LABEL: Record<string, string> = { buy: "Compra", sell: "Venda", hold: "Manter" };

export function TradeDetailModal({
  trade,
  currency,
  onClose,
}: {
  trade: TradeRecord;
  currency: string | null;
  onClose: () => void;
}) {
  return (
    <Modal title={`Trade — ${trade.pair}`} onClose={onClose}>
      <div className="space-y-1">
        <ModalField label="Client request id" value={<code className="text-xs">{trade.client_request_id}</code>} />
        <ModalField label="Lado" value={DIRECTION_LABEL[trade.side] ?? trade.side} />
        <ModalField label="Status" value={trade.status} />
        <ModalField label="Volume" value={formatNumber(trade.volume, 2)} />
        <ModalField label="Entrada" value={formatNumber(trade.entry_price, 5)} />
        <ModalField label="Stop loss" value={formatNumber(trade.stop_loss, 5)} />
        <ModalField label="Take profit" value={formatNumber(trade.take_profit, 5)} />
        <ModalField label="Modo" value={trade.trading_mode} />
        <ModalField label="Aberto em" value={formatDateTime(trade.opened_at)} />
        <ModalField label="Fechado em" value={formatDateTime(trade.closed_at)} />
      </div>

      {trade.signal && (
        <>
          <h4 className="text-sm font-semibold text-foreground mt-5 mb-2">Sinal originador</h4>
          <div className="space-y-1">
            <ModalField label="Direção" value={DIRECTION_LABEL[trade.signal.direction] ?? trade.signal.direction} />
            <ModalField label="Confiança" value={formatPercent(trade.signal.confidence * 100)} />
            <ModalField label="Score fundido" value={formatNumber(trade.signal.fused_score, 3)} />
            <ModalField label="Score técnico" value={formatNumber(trade.signal.technical_score, 3)} />
            <ModalField label="Score de sentimento" value={formatNumber(trade.signal.sentiment_score, 3)} />
            <ModalField label="Versão dos pesos" value={trade.signal.weight_version} />
          </div>
        </>
      )}

      {trade.outcome && (
        <>
          <h4 className="text-sm font-semibold text-foreground mt-5 mb-2">Resultado</h4>
          <div className="space-y-1">
            <ModalField label="Saída" value={formatNumber(trade.outcome.exit_price, 5)} />
            <ModalField
              label="PnL"
              value={
                <span className={trade.outcome.pnl >= 0 ? "text-success" : "text-danger"}>
                  {formatCurrency(trade.outcome.pnl, currency)}
                </span>
              }
            />
            <ModalField label="PnL %" value={formatPercent(trade.outcome.pnl_pct * 100, 2)} />
            <ModalField label="Duração" value={formatDuration(trade.outcome.duration_seconds)} />
            <ModalField label="Previsão correta?" value={trade.outcome.was_correct ? "Sim" : "Não"} />
          </div>
        </>
      )}
    </Modal>
  );
}
