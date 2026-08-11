"use client";
// Global "AI is working" indicator (F-028) — ONE reusable component with an AI identity
// (animated sparkle + "AI is thinking…" text) shown while ANY AI call is in
// progress. Standardizes the old local spinners (Concierge, search, Q&A, cross-sell, insights…)
// into a single consistent signal. It's pure UX. Styled via the palette variables
// (globals.css › .ns-ai-busy). `role=status`/`aria-live` for screen readers.
export default function AiThinking({
  label = "AI is thinking…",
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <div className={`ns-ai-busy${className ? " " + className : ""}`} role="status" aria-live="polite">
      <span className="ns-ai-busy-spark" aria-hidden>✦</span>
      <span className="ns-ai-busy-label">
        {label}
        <span className="ns-ai-busy-dots" aria-hidden>
          <i />
          <i />
          <i />
        </span>
      </span>
    </div>
  );
}
