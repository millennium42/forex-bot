"use client";

import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ThemeName } from "@/lib/palette";

const STORAGE_KEY = "forex-bot-theme";

interface ThemeContextValue {
  theme: ThemeName;
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function applyThemeClass(theme: ThemeName): void {
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  root.setAttribute("data-theme", theme);
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  // O default aqui é só o valor inicial da renderização do servidor. O script
  // inline em layout.tsx já aplicou a classe correta antes do primeiro paint;
  // este estado só precisa convergir com o que já está no DOM, sem causar
  // flash. Ver o script `ANTI_FOUC_SCRIPT` em layout.tsx.
  const [theme, setTheme] = useState<ThemeName>("dark");

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    const initial: ThemeName = stored === "light" ? "light" : "dark";
    // eslint-disable-next-line react-hooks/set-state-in-effect -- sincroniza com localStorage, só roda uma vez
    setTheme(initial);
    applyThemeClass(initial);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme((prev) => {
      const next: ThemeName = prev === "dark" ? "light" : "dark";
      window.localStorage.setItem(STORAGE_KEY, next);
      applyThemeClass(next);
      return next;
    });
  }, []);

  return <ThemeContext.Provider value={{ theme, toggleTheme }}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme precisa estar dentro de um ThemeProvider");
  return ctx;
}
