"use client";

import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { resetKillSwitch, triggerKillSwitch, type KillSwitchStatus } from "@/lib/api";
import { Modal } from "./Modal";

export function KillSwitchModal({
  active,
  onClose,
  onDone,
}: {
  active: boolean;
  onClose: () => void;
  onDone: (status: KillSwitchStatus) => void;
}) {
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const actionLabel = active ? "Resetar kill switch" : "Ativar kill switch";

  async function handleConfirm() {
    setSubmitting(true);
    setError(null);
    try {
      const actor = "operador-dashboard";
      const status = active
        ? await resetKillSwitch(actor, reason || "Reset manual via dashboard")
        : await triggerKillSwitch(actor, reason || "Acionamento manual via dashboard");
      onDone(status);
      onClose();
    } catch {
      setError("Não foi possível concluir a ação. Verifique a conexão com a API.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={actionLabel} onClose={onClose}>
      <div className="flex items-start gap-3 p-3 rounded-xl bg-warning-bg border border-warning/30 mb-4">
        <AlertTriangle className="h-5 w-5 text-warning shrink-0 mt-0.5" />
        <p className="text-sm text-foreground">
          {active
            ? "O bot está impedido de operar. Resetar libera novas ordens imediatamente. Essa ação fica registrada no audit log."
            : "Isso interrompe imediatamente qualquer nova ordem do bot. Só um reset manual libera a operação de novo. Essa ação fica registrada no audit log."}
        </p>
      </div>

      <label className="block text-sm text-fg-secondary mb-1" htmlFor="kill-switch-reason">
        Motivo (opcional)
      </label>
      <textarea
        id="kill-switch-reason"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        rows={3}
        className="w-full rounded-lg bg-surface-2 border border-surface-2-border p-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
        placeholder="Ex: mercado anômalo, manutenção programada..."
      />

      {error && <p className="text-sm text-danger mt-2">{error}</p>}

      <div className="flex justify-end gap-3 mt-5">
        <button
          type="button"
          onClick={onClose}
          className="px-4 py-2 rounded-xl text-sm font-medium text-fg-secondary hover:bg-surface-2 transition-colors"
        >
          Cancelar
        </button>
        <button
          type="button"
          onClick={handleConfirm}
          disabled={submitting}
          className="px-4 py-2 rounded-xl text-sm font-medium text-white bg-danger hover:brightness-110 disabled:opacity-60 transition-all"
        >
          {submitting ? "Enviando..." : actionLabel}
        </button>
      </div>
    </Modal>
  );
}
