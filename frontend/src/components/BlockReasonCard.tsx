"use client";

import { Ban, CheckCircle2 } from "lucide-react";
import type { LastBlock } from "@/lib/blockReason";
import { formatDateTime } from "@/lib/format";

export function BlockReasonCard({ info }: { info: LastBlock | null }) {
  if (!info) {
    return (
      <div className="glass-card rounded-2xl p-5 flex items-start gap-4 animate-slide-up">
        <CheckCircle2 className="w-5 h-5 text-fg-muted mt-0.5" />
        <div>
          <h3 className="font-semibold text-foreground">Por que a última ordem foi bloqueada</h3>
          <p className="text-sm text-fg-muted mt-1">Ainda não há eventos de decisão registrados na auditoria.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="glass-card rounded-2xl p-5 flex items-start gap-4 animate-slide-up">
      <div className={`p-2 rounded-full ${info.blocked ? "bg-danger-bg text-danger" : "bg-success-bg text-success"}`}>
        {info.blocked ? <Ban className="w-5 h-5" /> : <CheckCircle2 className="w-5 h-5" />}
      </div>
      <div>
        <h3 className="font-semibold text-foreground">Por que a última ordem foi bloqueada</h3>
        <p className="text-sm text-fg-secondary mt-1">{info.label}</p>
        {info.detail && <p className="text-xs text-fg-muted mt-0.5">{info.detail}</p>}
        <p className="text-xs text-fg-muted mt-1">{formatDateTime(info.at)}</p>
      </div>
    </div>
  );
}
