import type { CSSProperties } from "react";
import type { ChartPalette } from "@/lib/palette";

export function tooltipContentStyle(palette: ChartPalette): CSSProperties {
  return {
    backgroundColor: palette.surface,
    borderColor: palette.gridline,
    borderRadius: 8,
    color: palette.textSecondary,
    fontSize: 12,
  };
}
