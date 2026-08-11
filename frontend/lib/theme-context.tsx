"use client";
// App theme = palette + scheme (light/dark), selectable and persisted — ADR-012/013.
// The ENTIRE app (shop, Behind the Scenes, Admin) uses the palette via CSS variables
// ([data-palette]/[data-scheme] on <html>) — custom design, no @splunk (F-015 removed
// the SplunkThemeProvider). Default Splunk/light. The preference persists in localStorage and
// the anti-flash script in the layout applies the attributes before paint.
import { createContext, useCallback, useContext, useEffect, useState } from "react";

export type ColorScheme = "light" | "dark";
export type Palette = "splunk" | "blue" | "sunset" | "mono";

export const PALETTES: { id: Palette; label: string }[] = [
  // Splunk (default, F-038): copies the guide's Hugo Splunk theme (pink #FF007F + neutrals/fonts).
  { id: "splunk", label: "Splunk" },
  // "blue" is the former blue "Splunk" palette (renamed to free up the name for the guide's theme).
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
  // SSR-safe defaults (server and 1st client render match); the saved preference
  // is applied in the effect after mount — no hydration mismatch.
  const [palette, setPaletteState] = useState<Palette>(DEFAULT_PALETTE);
  const [colorScheme, setSchemeState] = useState<ColorScheme>(DEFAULT_SCHEME);

  useEffect(() => {
    try {
      const s = localStorage.getItem(SCHEME_KEY);
      if (s === "dark" || s === "light") setSchemeState(s);
      const p = localStorage.getItem(PALETTE_KEY);
      if (isPalette(p)) setPaletteState(p);
    } catch {
      /* storage unavailable — keeps the defaults */
    }
  }, []);

  // Keeps the <html> attributes in sync (outside React): the shop resolves its
  // CSS variables via [data-palette]/[data-scheme]. The inline script in the layout already
  // sets them before paint (avoids flash); here we only react to user changes.
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
      /* storage unavailable — only valid for this session */
    }
  }, []);

  const setColorScheme = useCallback((s: ColorScheme) => {
    setSchemeState(s);
    try {
      localStorage.setItem(SCHEME_KEY, s);
    } catch {
      /* storage unavailable — only valid for this session */
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
