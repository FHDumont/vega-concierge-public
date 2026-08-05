"use client";
// Thread de chat com renderização de artefatos por intent (F-050-CHAT).
import { useEffect, useRef } from "react";
import AiThinking from "@/components/AiThinking";
import { ChatMessage, ChatResult, Product } from "@/lib/api";
import { formatMoney } from "@/lib/shop";
import ProductCard from "./ProductCard";

type Turn = ChatMessage & { result?: ChatResult };

type AnswerLayout = {
  lead?: string;
  sections?: { title: string; body: string }[];
  facts?: { label: string; value: string }[];
  bullets?: string[];
};

function hasLayoutContent(layout?: AnswerLayout | null): boolean {
  if (!layout) return false;
  return Boolean(
    layout.sections?.length || layout.facts?.length || layout.bullets?.length,
  );
}

// Resposta de LLM indisponível não deve renderizar card de produto/comparação: o backend
// marca `llm_unavailable`; o teste de `[stub` cobre resposta offline sem falha de provider.
function shouldShowArtifacts(content: string, result?: ChatResult): boolean {
  if (!result) return false;
  if (result.llm_unavailable) return false;
  if (hasLayoutContent(result.artifacts?.layout as AnswerLayout | undefined)) return true;
  return !content.trim().startsWith("[stub");
}

function RecommendArtifact({
  artifacts,
  onAdd,
}: {
  artifacts: Record<string, unknown>;
  onAdd: (p: Product) => void;
}) {
  const rec = artifacts.recommended as Product | null;
  if (!rec) return null;
  return (
    <div className="ns-chat-artifact">
      <ProductCard
        product={rec}
        highlight
        onAdd={onAdd}
      />
    </div>
  );
}

function AnswerLayoutBlock({ layout }: { layout?: AnswerLayout | null }) {
  if (!hasLayoutContent(layout)) return null;
  const { sections, facts, bullets } = layout!;
  return (
    <div className="ns-chat-artifact ns-chat-answer-layout">
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

function CompareArtifact({ artifacts }: { artifacts: Record<string, unknown> }) {
  const a = artifacts.product_a as Product | undefined;
  const b = artifacts.product_b as Product | undefined;
  const verdict = artifacts.verdict as string | undefined;
  const layout = artifacts.layout as AnswerLayout | undefined;
  if (!a || !b) return null;
  return (
    <div className="ns-chat-artifact ns-chat-compare">
      <div className="ns-chat-compare-grid">
        <div className="ns-chat-mini-card">
          <b>{a.name}</b>
          <span>{formatMoney(a.price)}</span>
        </div>
        <div className="ns-chat-mini-card">
          <b>{b.name}</b>
          <span>{formatMoney(b.price)}</span>
        </div>
      </div>
      <AnswerLayoutBlock layout={layout} />
      {!hasLayoutContent(layout) && verdict && <p className="ns-chat-verdict">{verdict}</p>}
    </div>
  );
}

function SearchArtifact({
  artifacts,
  onAdd,
}: {
  artifacts: Record<string, unknown>;
  onAdd: (p: Product) => void;
}) {
  const products = (artifacts.products as Product[] | undefined) || [];
  const interpretation = artifacts.interpretation as string | undefined;
  if (!products.length) return null;
  return (
    <div className="ns-chat-artifact">
      {interpretation && <p className="ns-muted" style={{ margin: "0 0 10px", fontSize: 13 }}>{interpretation}</p>}
      <div className="ns-chat-search-grid">
        {products.slice(0, 4).map((p) => (
          <ProductCard
            key={p.sku}
            product={p}
            onAdd={onAdd}
          />
        ))}
      </div>
    </div>
  );
}

function GiftArtifact({ artifacts }: { artifacts: Record<string, unknown> }) {
  const msg = artifacts.gift_message as string | undefined;
  if (!msg) return null;
  return (
    <div className="ns-chat-artifact ns-chat-gift">
      <blockquote>{msg}</blockquote>
      <button
        type="button"
        className="ns-go sm"
        onClick={() => navigator.clipboard?.writeText(msg)}
      >
        Copy message
      </button>
    </div>
  );
}

function ProductQaChip({ artifacts }: { artifacts: Record<string, unknown> }) {
  const sku = artifacts.sku as string | undefined;
  const layout = artifacts.layout as AnswerLayout | undefined;
  if (!sku && !hasLayoutContent(layout)) return null;
  return (
    <>
      {sku && (
        <div className="ns-chat-artifact">
          <span className="ns-chip">{sku}</span>
        </div>
      )}
      <AnswerLayoutBlock layout={layout} />
    </>
  );
}

function ReturnsArtifact({ artifacts }: { artifacts: Record<string, unknown> }) {
  const approved = artifacts.approved as boolean | undefined;
  const reason = artifacts.reason as string | undefined;
  const steps = (artifacts.steps as { label: string; ok: boolean; detail: string }[] | undefined) || [];
  if (approved === undefined && !reason) return null;
  return (
    <div className="ns-chat-artifact ns-refund-result">
      {reason && (
        <p className={`ns-refund-verdict ${approved ? "ok" : "no"}`}>{reason}</p>
      )}
      {steps.length > 0 && (
        <ul className="ns-refund-steps">
          {steps.map((s) => (
            <li key={s.label} className={s.ok ? "ok" : "no"}>
              <span className="ic" aria-hidden>{s.ok ? "✓" : "✕"}</span>
              <span className="bd">
                <span className="lb">{s.label}</span>
                <span className="dt">{s.detail}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ArtifactBlock({
  result,
  onAdd,
}: {
  result: ChatResult;
  onAdd: (p: Product) => void;
}) {
  const { intent, artifacts } = result;
  if (intent === "recommend") {
    return <RecommendArtifact artifacts={artifacts} onAdd={onAdd} />;
  }
  if (intent === "compare") return <CompareArtifact artifacts={artifacts} />;
  if (intent === "search") {
    return <SearchArtifact artifacts={artifacts} onAdd={onAdd} />;
  }
  if (intent === "gift") return <GiftArtifact artifacts={artifacts} />;
  if (intent === "product_qa") return <ProductQaChip artifacts={artifacts} />;
  if (intent === "returns") return <ReturnsArtifact artifacts={artifacts} />;
  if (intent === "destructive") {
    const sku = artifacts.sku as string | undefined;
    return sku ? (
      <div className="ns-chat-artifact">
        <span className="ns-chip">{sku}</span>
      </div>
    ) : null;
  }
  if (intent === "general" || intent === "stats") {
    return <AnswerLayoutBlock layout={artifacts.layout as AnswerLayout | undefined} />;
  }
  return null;
}

export default function ChatThread({
  turns,
  loading = false,
  active = true,
  onAdd,
}: {
  turns: Turn[];
  loading?: boolean;
  active?: boolean;
  onAdd: (p: Product) => void;
}) {
  const threadRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!active) return;
    const el = threadRef.current;
    if (!el) return;
    const scrollToBottom = () => {
      el.scrollTop = el.scrollHeight;
    };
    scrollToBottom();
    const frame = requestAnimationFrame(scrollToBottom);
    return () => cancelAnimationFrame(frame);
  }, [turns, loading, active]);

  return (
    <div
      ref={threadRef}
      className="ns-chat-thread"
      role="log"
      aria-live="polite"
      aria-label="Chat messages"
    >
      {turns.map((t, i) => (
        <div key={i} className={`ns-chat-bubble ${t.role}`}>
          <p>{t.content}</p>
          {t.role === "assistant" && t.result && shouldShowArtifacts(t.content, t.result) && (
            <ArtifactBlock
              result={t.result}
              onAdd={onAdd}
            />
          )}
        </div>
      ))}
      {loading && (
        <div className="ns-chat-loading">
          <AiThinking label="Thinking…" />
        </div>
      )}
    </div>
  );
}
