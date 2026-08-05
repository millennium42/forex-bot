/**
 * Cores de dado dos gráficos — validadas pela skill `dataviz` com
 * `scripts/validate_palette.js` contra as superfícies claro/escuro deste app.
 *
 * O par azul/laranja (categórico, slots 1-2) substitui o azul/roxo usado no
 * resto da UI: aquele par falha o piso de visão normal (ΔE 12, abaixo de 15)
 * e a separação CVD (ΔE 1.3 deutan) quando as duas séries aparecem lado a
 * lado num mesmo gráfico. Verde/vermelho de status são fixos entre os modos
 * (mesmo hex, contraste diferente por superfície) — nunca reaproveitados
 * como cor de série.
 */

export type ThemeName = "light" | "dark";

export interface ChartPalette {
  seriesBlue: string;
  seriesOrange: string;
  good: string;
  critical: string;
  warning: string;
  gridline: string;
  axis: string;
  textSecondary: string;
  surface: string;
  divergingNeutral: string;
}

const PALETTES: Record<ThemeName, ChartPalette> = {
  light: {
    seriesBlue: "#2a78d6",
    seriesOrange: "#eb6834",
    good: "#0ca30c",
    critical: "#d03b3b",
    warning: "#fab219",
    gridline: "#e1e0d9",
    axis: "#c3c2b7",
    textSecondary: "#52514e",
    surface: "#fcfcfb",
    divergingNeutral: "#f0efec",
  },
  dark: {
    seriesBlue: "#3987e5",
    seriesOrange: "#d95926",
    good: "#0ca30c",
    critical: "#e66767",
    warning: "#fab219",
    gridline: "#2c2c2a",
    axis: "#383835",
    textSecondary: "#c3c2b7",
    surface: "#1a1a19",
    divergingNeutral: "#383835",
  },
};

export function getPalette(theme: ThemeName): ChartPalette {
  return PALETTES[theme];
}
