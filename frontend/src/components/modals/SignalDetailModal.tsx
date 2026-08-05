"use client";

import { formatDateTime, formatNumber, formatPercent } from "@/lib/format";
import type { SignalRecord } from "@/lib/api";
import { Modal, ModalField } from "./Modal";

const DIRECTION_LABEL: Record<string, string> = { buy: "Compra", sell: "Venda", hold: "Manter" };

export function SignalDetailModal({ signal, onClose }: { signal: SignalRecord; onClose: () => void }) {
  const inputEntries = Object.entries(signal.inputs ?? {});

  return (
    <Modal title={`Sinal — ${signal.pair}`} onClose={onClose}>
      <div className="space-y-1">
        <ModalField label="Direção" value={DIRECTION_LABEL[signal.direction] ?? signal.direction} />
        <ModalField label="Confiança" value={formatPercent(signal.confidence * 100)} />
        <ModalField label="Score fundido" value={formatNumber(signal.fused_score, 3)} />
        <ModalField label="Score técnico" value={formatNumber(signal.technical_score, 3)} />
        <ModalField label="Score de sentimento" value={formatNumber(signal.sentiment_score, 3)} />
        <ModalField label="Versão dos pesos" value={signal.weight_version} />
        <ModalField label="Gerado em" value={formatDateTime(signal.created_at)} />
      </div>

      {inputEntries.length > 0 && (
        <>
          <h4 className="text-sm font-semibold text-foreground mt-5 mb-2">Indicadores crus (inputs)</h4>
          <pre className="text-xs bg-surface-2 border border-surface-2-border rounded-lg p-3 overflow-x-auto text-fg-secondary">
            {JSON.stringify(signal.inputs, null, 2)}
          </pre>
        </>
      )}
    </Modal>
  );
}
