"use client";

import type { LucideIcon } from "lucide-react";

export interface TabDef {
  id: string;
  label: string;
  icon: LucideIcon;
}

export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: TabDef[];
  active: string;
  onChange: (id: string) => void;
}) {
  return (
    <nav className="flex flex-wrap gap-2 mb-6 border-b border-card-border pb-2 animate-slide-up">
      {tabs.map((tab) => {
        const Icon = tab.icon;
        const isActive = tab.id === active;
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => onChange(tab.id)}
            aria-current={isActive ? "page" : undefined}
            className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium transition-colors ${
              isActive
                ? "bg-primary text-primary-foreground shadow-lg shadow-primary/20"
                : "text-fg-secondary hover:bg-surface-2 hover:text-foreground"
            }`}
          >
            <Icon className="h-4 w-4" />
            {tab.label}
          </button>
        );
      })}
    </nav>
  );
}
