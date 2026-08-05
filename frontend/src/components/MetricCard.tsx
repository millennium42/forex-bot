"use client";

import type { LucideIcon } from "lucide-react";
import { InfoTooltip } from "@/components/charts/ChartCard";

export function MetricCard({
  title,
  value,
  subtitle,
  icon: Icon,
  tooltip,
  valueClassName = "text-foreground",
}: {
  title: string;
  value: string;
  subtitle?: string;
  icon: LucideIcon;
  tooltip?: string;
  valueClassName?: string;
}) {
  return (
    <div className="glass-card rounded-2xl p-5 relative overflow-hidden group hover:border-fg-muted/40 transition-all">
      <div className="absolute top-0 right-0 p-4 opacity-15 group-hover:opacity-30 transition-opacity transform group-hover:scale-110 group-hover:rotate-12 duration-300">
        <Icon className="w-10 h-10 text-primary" />
      </div>
      <div className="flex items-center gap-2 mb-3">
        <div className="p-2 bg-surface-2 rounded-lg shadow-sm border border-surface-2-border">
          <Icon className="w-5 h-5 text-primary" />
        </div>
        <h3 className="text-sm font-medium text-fg-secondary flex items-center gap-1.5">
          {title}
          {tooltip && <InfoTooltip text={tooltip} />}
        </h3>
      </div>
      <h4 className={`text-2xl font-bold ${valueClassName}`}>{value}</h4>
      {subtitle && <p className="text-xs text-fg-muted mt-2">{subtitle}</p>}
    </div>
  );
}
