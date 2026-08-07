export type AnswerLayout = {
  lead?: string;
  sections?: { title: string; body: string }[];
  facts?: { label: string; value: string }[];
  bullets?: string[];
};

export function hasLayoutContent(layout?: AnswerLayout | null): boolean {
  if (!layout) return false;
  return Boolean(
    layout.lead?.trim()
    || layout.sections?.length
    || layout.facts?.length
    || layout.bullets?.length,
  );
}

export function AnswerLayoutBlock({ layout }: { layout?: AnswerLayout | null }) {
  if (!hasLayoutContent(layout)) return null;
  const { lead, sections, facts, bullets } = layout!;
  return (
    <div className="ns-chat-artifact ns-chat-answer-layout">
      {lead && <p className="ns-answer-lead">{lead}</p>}
      {facts && facts.length > 0 && (
        <dl className="ns-chat-facts">
          {facts.map((f) => (
            <div key={f.label} className="ns-chat-fact">
              <dt>{f.label}</dt>
              <dd>{f.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {sections && sections.length > 0 && (
        <ul className="ns-chat-sections">
          {sections.map((s) => (
            <li key={s.title}>
              <span className="ic" aria-hidden>•</span>
              <span className="bd">
                <span className="lb">{s.title}</span>
                <span className="dt">{s.body}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
      {bullets && bullets.length > 0 && (
        <ul className="ns-chat-bullets">
          {bullets.map((b) => (
            <li key={b}>{b}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
