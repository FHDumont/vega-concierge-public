"use client";
// Tema da app = paleta + esquema (light/dark), selecionáveis e persistidos — ADR-012/013.
// TODA a app (loja, Behind the Scenes, Admin) usa a paleta via variáveis CSS
// ([data-palette]/[data-scheme] no <html>) — design custom, sem @splunk (F-015 removeu
// o SplunkThemeProvider). Default Splunk/light. A preferência persiste em localStorage e
// o script anti-flash no layout aplica os atributos antes do paint.
import { createContext, useCallback, useContext, useEffect, useState } from "react";

export type ColorScheme = "light" | "dark";
export type Palette = "splunk" | "blue" | "sunset" | "mono";

export const PALETTES: { id: Palette; label: string }[] = [
  // Splunk (default, F-038): copia o tema Hugo Splunk do guia (rosa #FF007F + neutros/fontes).
  { id: "splunk", label: "Splunk" },
  // "blue" é a antiga paleta "Splunk" azul (renomeada p/ liberar o nome ao tema do guia).
  { id: "blue", label: "Blue" },
  { id: "sunset", label: "Sunset" },
  { id: "mono", label: "Mono" },
];

const SCHEME_KEY = "vega.colorScheme";
const PALETTE_KEY = "vega.palette";
const DEFAULT_PALETTE: Palette = "splunk";
const DEFAULT_SCHEME: ColorScheme = "light";

function isPalette(v: unknown): v is Palette {
  return v === "splunk" || v === "blue" || v === "sunset" || v === "mono";
}

type ThemeContextValue = {
  palette: Palette;
  setPalette: (p: Palette) => void;
  colorScheme: ColorScheme;
  setColorScheme: (s: ColorScheme) => void;
  toggle: () => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function PaletteProvider({ children }: { children: React.ReactNode }) {
  // Defaults SSR-safe (servidor e 1º render do cliente coincidem); a preferência salva
  // é aplicada no efeito após montar — sem mismatch de hidratação.
  const [palette, setPaletteState] = useState<Palette>(DEFAULT_PALETTE);
  const [colorScheme, setSchemeState] = useState<ColorScheme>(DEFAULT_SCHEME);

  useEffect(() => {
    try {
      const s = localStorage.getItem(SCHEME_KEY);
      if (s === "dark" || s === "light") setSchemeState(s);
      const p = localStorage.getItem(PALETTE_KEY);
      if (isPalette(p)) setPaletteState(p);
    } catch {
      /* storage indisponível — mantém os defaults */
    }
  }, []);

  // Mantém os atributos no <html> em sincronia (fora do React): a loja resolve as
  // variáveis CSS por [data-palette]/[data-scheme]. O script inline no layout já os
  // define antes do paint (evita flash); aqui só reagimos a mudanças do usuário.
  useEffect(() => {
    document.documentElement.dataset.palette = palette;
  }, [palette]);
  useEffect(() => {
    document.documentElement.dataset.scheme = colorScheme;
  }, [colorScheme]);

  const setPalette = useCallback((p: Palette) => {
    setPaletteState(p);
    try {
      localStorage.setItem(PALETTE_KEY, p);
    } catch {
      /* storage indisponível — vale só nesta sessão */
    }
  }, []);

  const setColorScheme = useCallback((s: ColorScheme) => {
    setSchemeState(s);
    try {
      localStorage.setItem(SCHEME_KEY, s);
    } catch {
      /* storage indisponível — vale só nesta sessão */
    }
  }, []);

  const toggle = useCallback(
    () => setColorScheme(colorScheme === "dark" ? "light" : "dark"),
    [colorScheme, setColorScheme],
  );

  return (
    <ThemeContext.Provider value={{ palette, setPalette, colorScheme, setColorScheme, toggle }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within PaletteProvider");
  return ctx;
}
