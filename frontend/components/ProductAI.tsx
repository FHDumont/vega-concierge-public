"use client";
// IA-Produto (F-022): assistente na página de detalhe — Q&A fundamentado nos dados do produto.
import { useMemo, useState } from "react";
import { askProduct } from "@/lib/api";
import AiThinking from "./AiThinking";

const CHIP_POOL: Record<string, string[]> = {
  audio: [
    "How's the battery life?",
    "Is it good for travel?",
    "How's the sound quality?",
    "Does it have noise cancellation?",
  ],
  wearable: [
    "What health metrics does it track?",
    "Is it water resistant?",
    "How long does the battery last?",
    "Is it comfortable to wear all day?",
  ],
  casa: [
    "What's included in the box?",
    "Is it beginner-friendly?",
    "Would this make a good gift?",
    "What room is it best for?",
  ],
  presente: [
    "Would this make a good gift?",
    "What's it best for?",
    "Is it easy to use?",
  ],
};

const DEFAULT_CHIPS = ["What's it best for?", "What are the key specs?", "Is it in stock?"];

const TAG_PRIORITY = ["audio", "wearable", "casa", "presente"];

function chipsForTags(tags: string[]): string[] {
  const picked: string[] = [];
  for (const tag of TAG_PRIORITY) {
    if (!tags.includes(tag)) continue;
    for (const chip of CHIP_POOL[tag] ?? []) {
      if (!picked.includes(chip)) picked.push(chip);
      if (picked.length >= 3) return picked;
    }
  }
  for (const chip of DEFAULT_CHIPS) {
    if (!picked.includes(chip)) picked.push(chip);
    if (picked.length >= 3) break;
  }
  return picked.slice(0, 3);
}

export default function ProductAI({ sku, name, tags = [] }: { sku: string; name: string; tags?: string[] }) {
  const suggestions = useMemo(() => chipsForTags(tags), [tags]);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState(false);

  async function ask(q?: string) {
    const query = (q ?? question).trim();
    if (!query || asking) return;
    setQuestion(query);
    setAsking(true);
    setAnswer(null);
    setAskError(false);
    try {
      setAnswer((await askProduct(sku, query)).answer);
    } catch {
      setAskError(true);
    } finally {
      setAsking(false);
    }
  }

  return (
    <section className="ns-pai" aria-label="AI product assistant">
      <div className="ns-pai-head">
        <span className="ns-spark sm" aria-hidden>✦</span>
        <h2>Ask about this product</h2>
      </div>

      <div className="ns-pai-ask">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && ask()}
          placeholder={`e.g. Is the ${name} good for travel?`}
          aria-label="Ask a question about this product"
        />
        <button type="button" className="ns-go" onClick={() => ask()} disabled={asking}>
          {asking ? "Asking…" : "Ask"}
        </button>
      </div>

      <div className="ns-pai-chips">
        {suggestions.map((s) => (
          <button key={s} type="button" className="ns-chip" onClick={() => ask(s)} disabled={asking}>
            {s}
          </button>
        ))}
      </div>

      {(asking || answer || askError) && (
        <div className="ns-pai-answer">
          {asking && <AiThinking label="Reading the product details" />}
          {!asking && answer && <p>{answer}</p>}
          {!asking && askError && (
            <div className="ns-note" role="status">
              We couldn’t answer that right now. Please try again.
            </div>
          )}
        </div>
      )}
    </section>
  );
}
