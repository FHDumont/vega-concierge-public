"use client";
// Content of the palette + scheme (light/dark) picker — reused in the store header
// popup (F-NAV-1). Logic in lib/theme-context.tsx (ADR-012/013).
import { PALETTES, useTheme, type ColorScheme } from "@/lib/theme-context";

const SCHEMES: { id: ColorScheme; label: string }[] = [
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
];

export default function PalettePicker() {
  const { palette, setPalette, colorScheme, setColorScheme } = useTheme();
  return (
    <div className="ns-theme-picker" role="group" aria-label="Theme">
      <b>Palette</b>
      <span className="ns-chips">
        {PALETTES.map((p) => (
          <button
            key={p.id}
            type="button"
            className={`ns-chip${palette === p.id ? " on" : ""}`}
            aria-pressed={palette === p.id}
            onClick={() => setPalette(p.id)}
          >
            {p.label}
          </button>
        ))}
      </span>
      <span className="ns-muted" aria-hidden>·</span>
      <b>Scheme</b>
      <span className="ns-chips">
        {SCHEMES.map((s) => (
          <button
            key={s.id}
            type="button"
            className={`ns-chip${colorScheme === s.id ? " on" : ""}`}
            aria-pressed={colorScheme === s.id}
            onClick={() => setColorScheme(s.id)}
          >
            {s.label}
          </button>
        ))}
      </span>
    </div>
  );
}
