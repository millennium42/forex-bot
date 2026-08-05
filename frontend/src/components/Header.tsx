"use client";

import { AlertTriangle, Moon, ShieldCheck, Sun, WifiOff } from "lucide-react";
import { useTheme } from "@/lib/theme";
import type { AccountStatus } from "@/lib/api";

/**
 * Badge de modo — sempre visível, nunca condicional. Em modo real fica
 * vermelho e com pulso: é a barreira visual para o operador nunca confundir
 * demo com real (§7 do HANDOFF: "impossível de ignorar").
 */
function ModeBadge({ account }: { account: AccountStatus }) {
  if (!account.connected || account.demo === null) {
    return (
      <div className="flex items-center gap-2 rounded-full border border-fg-muted/30 bg-surface-2 px-4 py-2 text-fg-muted">
        <WifiOff className="h-4 w-4" />
        <span className="text-sm font-semibold">MODO DESCONHECIDO</span>
      </div>
    );
  }

  if (account.demo === false) {
    return (
      <div className="flex items-center gap-2 rounded-full border-2 border-red-500 bg-red-600 px-4 py-2 text-white shadow-[0_0_16px_rgba(239,68,68,0.55)]">
        <AlertTriangle className="h-4 w-4 animate-pulse" />
        <span className="text-sm font-extrabold tracking-wide">MODO REAL — DINHEIRO DE VERDADE</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2 rounded-full border border-success/30 bg-success-bg px-4 py-2 text-success">
      <ShieldCheck className="h-4 w-4" />
      <span className="text-sm font-semibold">MODO DEMO</span>
    </div>
  );
}

export function Header({ account }: { account: AccountStatus }) {
  const { theme, toggleTheme } = useTheme();

  return (
    <header className="flex flex-col md:flex-row justify-between items-start md:items-center mb-8 gap-4 animate-slide-up">
      <div>
        <h1 className="text-3xl md:text-4xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-primary to-accent">
          Forex Bot
        </h1>
        <p className="text-fg-secondary mt-1">Painel operacional do agente de trading</p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-3 bg-surface-2 py-2 px-4 rounded-full border border-surface-2-border backdrop-blur-md">
          <div
            className={`h-2.5 w-2.5 rounded-full ${
              account.connected ? "bg-success shadow-[0_0_8px_rgba(16,185,129,0.6)] animate-pulse" : "bg-fg-muted"
            }`}
          />
          <span className={`text-sm font-medium ${account.connected ? "text-success" : "text-fg-muted"}`}>
            {account.connected ? "Sistema online" : "MT5 desconectado"}
          </span>
        </div>
        <ModeBadge account={account} />
        <button
          type="button"
          onClick={toggleTheme}
          aria-label={theme === "dark" ? "Ativar tema claro" : "Ativar tema escuro"}
          className="flex items-center justify-center h-10 w-10 rounded-full border border-card-border bg-card hover:bg-surface-2 transition-colors"
        >
          {theme === "dark" ? <Sun className="h-4 w-4 text-warning" /> : <Moon className="h-4 w-4 text-fg-secondary" />}
        </button>
      </div>
    </header>
  );
}
