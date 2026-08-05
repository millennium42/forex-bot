"use client";

import { ScrollText } from "lucide-react";
import { useMemo, useState } from "react";
import type { AuditEntry } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

export function AuditTab({ entries }: { entries: AuditEntry[] }) {
  const [eventTypeFilter, setEventTypeFilter] = useState("all");

  const eventTypes = useMemo(() => Array.from(new Set(entries.map((e) => e.event_type))).sort(), [entries]);
  const filtered = useMemo(
    () => (eventTypeFilter === "all" ? entries : entries.filter((e) => e.event_type === eventTypeFilter)),
    [entries, eventTypeFilter]
  );

  return (
    <div className="glass-card rounded-2xl p-6 animate-slide-up">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
        <h2 className="text-xl font-semibold flex items-center gap-2 text-foreground">
          <ScrollText className="w-5 h-5 text-primary" />
          Auditoria
        </h2>
        <select
          value={eventTypeFilter}
          onChange={(e) => setEventTypeFilter(e.target.value)}
          className="bg-surface-2 border border-surface-2-border text-sm rounded-lg px-3 py-1.5 text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
        >
          <option value="all">Todos os tipos de evento</option>
          {eventTypes.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
      </div>

      {filtered.length === 0 ? (
        <p className="text-sm text-fg-muted py-8 text-center">Nenhum evento encontrado.</p>
      ) : (
        <div className="space-y-2 max-h-[600px] overflow-y-auto">
          {filtered.map((entry) => (
            <div key={entry.id} className="p-3 rounded-xl bg-surface-2 border border-surface-2-border">
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs font-mono font-semibold text-primary">{entry.event_type}</span>
                <span className="text-xs text-fg-muted">{formatDateTime(entry.created_at)}</span>
              </div>
              <div className="flex items-center justify-between gap-3 mt-1">
                <span className="text-xs text-fg-secondary">ator: {entry.actor}</span>
                {entry.client_request_id && (
                  <span className="text-xs text-fg-muted font-mono">{entry.client_request_id}</span>
                )}
              </div>
              {Object.keys(entry.payload).length > 0 && (
                <pre className="text-xs text-fg-muted mt-2 overflow-x-auto">{JSON.stringify(entry.payload)}</pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
