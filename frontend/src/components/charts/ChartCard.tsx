"use client";

import type { LucideIcon } from "lucide-react";
import { HelpCircle } from "lucide-react";
import React from "react";

export function InfoTooltip({ text }: { text: string }) {
  return (
    <div className="relative flex items-center group cursor-help ml-1">
      <HelpCircle className="w-4 h-4 text-fg-muted hover:text-primary transition-colors" />
      <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-56 opacity-0 group-hover:opacity-100 pointer-events-none transition-opacity bg-card text-xs text-foreground p-2.5 rounded-lg shadow-xl border border-card-border z-50 text-center">
        {text}
      </div>
    </div>
  );
}

export function ChartCard({
  title,
  icon: Icon,
  tooltip,
  className = "",
  isEmpty,
  emptyMessage = "Sem dados suficientes ainda.",
  children,
}: {
  title: string;
  icon: LucideIcon;
  tooltip?: string;
  className?: string;
  isEmpty?: boolean;
  emptyMessage?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={`glass-card rounded-2xl p-6 ${className}`}>
      <h2 className="text-xl font-semibold mb-4 flex items-center gap-2 text-foreground">
        <Icon className="w-5 h-5 text-primary" />
        {title}
        {tooltip && <InfoTooltip text={tooltip} />}
      </h2>
      {isEmpty ? (
        <div className="h-[260px] flex items-center justify-center text-sm text-fg-muted text-center px-6">
          {emptyMessage}
        </div>
      ) : (
        children
      )}
    </div>
  );
}
