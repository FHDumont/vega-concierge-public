"use client";
// Palette/scheme popup in the store header (F-NAV-1). Visible to everyone (logged in or not).
// Closes with Esc, outside click, or the close button. Global palette via data-palette/data-scheme
// on <html> keeps applying across all layers (ADR-013).
import { useCallback, useEffect, useRef, useState } from "react";
import PalettePicker from "./PalettePicker";

export default function ThemePopup() {
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const close = useCallback(() => {
    setOpen(false);
    btnRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") close();
    }
    function onClick(e: MouseEvent) {
      const t = e.target as Node;
      if (panelRef.current?.contains(t) || btnRef.current?.contains(t)) return;
      close();
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open, close]);

  useEffect(() => {
    if (!open) return;
    panelRef.current?.querySelector<HTMLElement>("button")?.focus();
  }, [open]);

  return (
    <div className="ns-theme-wrap">
      <button
        ref={btnRef}
        type="button"
        className="ns-icon"
        aria-label="Theme settings"
        aria-haspopup="dialog"
        aria-expanded={open}
        title="Theme"
        onClick={() => setOpen((v) => !v)}
      >
        <span aria-hidden>🎨</span>
      </button>

      {open && (
        <div
          ref={panelRef}
          className="ns-theme-popup"
          role="dialog"
          aria-modal="true"
          aria-label="Theme settings"
        >
          <header className="ns-theme-popup-head">
            <b>Theme</b>
            <button type="button" className="ns-btn-ghost sm" onClick={close} aria-label="Close">
              ✕
            </button>
          </header>
          <PalettePicker />
        </div>
      )}
    </div>
  );
}
