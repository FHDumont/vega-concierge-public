"use client";
// IA-Produto (F-022): assistente na página de detalhe — Q&A fundamentado nos dados do produto.
// Mostra só o conteúdo. Estilizado pelas variáveis de paleta.
import { useState } from "react";
import { askProduct } from "@/lib/api";
import { useChat, useChatPageScope } from "@/lib/chat-context";
import AiThinking from "./AiThinking";

const SUGGESTIONS = ["What's it best for?", "How's the battery life?", "Is it good for travel?"];

export default function ProductAI({ sku, name }: { sku: string; name: string }) {
  const { openChat } = useChat();
  useChatPageScope({ sku });
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
        <button
          type="button"
          className="ns-btn-ghost sm"
          onClick={() => openChat({ sku, seed: question || `Tell me about ${name}` })}
        >
          Open in chat
        </button>
      </div>

      <div className="ns-pai-chips">
        {SUGGESTIONS.map((s) => (
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
