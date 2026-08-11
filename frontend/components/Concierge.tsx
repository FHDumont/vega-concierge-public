"use client";
// Concierge banner on the storefront — CTA opens the floating widget (F-051).
import { useEffect, useRef, useState } from "react";
import { useChat } from "@/lib/chat-context";

type ConciergeChip = { label: string; question: string };

const CONCIERGE_CHIPS: ConciergeChip[] = [
  { label: "Birthday gift under $300", question: "Birthday gift under $300" },
  { label: "Something compact for travel", question: "Something compact for travel" },
  { label: "Gift for a coffee lover", question: "Gift for a coffee lover" },
  { label: "Search headphones", question: "search for wireless headphones" },
  { label: "Compare two products", question: "compare NS-001 and NS-002" },
  { label: "How much have I spent?", question: "How much have I spent?" },
  { label: "Most expensive product", question: "What is the most expensive product?" },
  { label: "Best-selling product", question: "What is the best-selling product?" },
  { label: "Store policies", question: "What are your store policies?" },
  { label: "Returns & refunds", question: "How do returns and refunds work?" },
  { label: "Return window", question: "How many days do I have to return?" },
  { label: "Shipping", question: "How does shipping and delivery work?" },
  { label: "Warranty", question: "What does the warranty cover?" },
  { label: "Payment", question: "When am I charged for my order?" },
];

export default function Concierge({
  initialRequest,
  autoRun,
}: {
  initialRequest?: string;
  autoRun?: boolean;
}) {
  const chat = useChat();
  const [request, setRequest] = useState(initialRequest ?? "");
  const seeded = useRef(false);

  useEffect(() => {
    if (initialRequest) setRequest(initialRequest);
  }, [initialRequest]);

  useEffect(() => {
    if ((autoRun || initialRequest) && initialRequest && !seeded.current) {
      seeded.current = true;
      chat.openChat({ seed: initialRequest });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRun, initialRequest]);

  function openWithSeed(seed?: string) {
    const text = (seed ?? request).trim();
    if (!text) return;
    chat.openChat({ seed: text });
  }

  return (
    <section className="ns-concierge" aria-label="AI concierge">
      <div className="ns-concierge-row">
        <div className="ns-spark" aria-hidden>✦</div>
        <div className="ns-ask">
          <input
            value={request}
            onChange={(e) => setRequest(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && openWithSeed()}
            placeholder="e.g. a birthday gift under $300 that arrives by Friday"
            aria-label="Describe what you're looking for"
          />
        </div>
        <button type="button" className="ns-go" onClick={() => openWithSeed()} disabled={!request.trim()}>
          Ask concierge
        </button>
      </div>

      <div className="ns-concierge-chips">
        {CONCIERGE_CHIPS.map((chip) => (
          <button
            key={chip.question}
            type="button"
            className="ns-chip"
            onClick={() => openWithSeed(chip.question)}
          >
            {chip.label}
          </button>
        ))}
      </div>
    </section>
  );
}
