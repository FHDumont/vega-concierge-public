"use client";
// Indicador global "AI is working" (F-028) — UM componente reutilizável com identidade de IA
// (sparkle animado + texto "AI is thinking…") exibido enquanto QUALQUER chamada de IA está em
// andamento. Padroniza os antigos spinners locais (Concierge, busca, Q&A, cross-sell, insights…)
// num só sinal consistente. É puro UX. Estilizado pelas variáveis de paleta
// (globals.css › .ns-ai-busy). `role=status`/`aria-live` p/ leitores de tela.
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
