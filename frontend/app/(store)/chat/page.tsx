"use client";
// Redirect /chat → home com query params p/ abrir o widget (F-051).
import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

function ChatRedirectInner() {
  const router = useRouter();
  const params = useSearchParams();

  useEffect(() => {
    const q = new URLSearchParams();
    q.set("chat", "1");
    const seed = params.get("seed");
    const sku = params.get("sku");
    const orderId = params.get("orderId") || params.get("order_id");
    if (seed) q.set("seed", seed);
    if (sku) q.set("sku", sku);
    if (orderId) q.set("orderId", orderId);
    router.replace(`/?${q.toString()}`);
  }, [router, params]);

  return (
    <main className="ns-wrap">
      <p className="ns-muted">Opening chat…</p>
    </main>
  );
}

export default function ChatPage() {
  return (
    <Suspense fallback={<main className="ns-wrap"><p className="ns-muted">Loading…</p></main>}>
      <ChatRedirectInner />
    </Suspense>
  );
}
