"use client";
// Reads query ?chat=1&seed=&sku=&orderId= on the home page and opens the widget (F-051).
import { Suspense, useEffect, useRef } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useChat } from "@/lib/chat-context";

function ChatDeepLinkInner() {
  const params = useSearchParams();
  const router = useRouter();
  const chat = useChat();
  const handled = useRef(false);

  useEffect(() => {
    if (handled.current) return;
    if (params.get("chat") !== "1") return;
    handled.current = true;

    const seed = params.get("seed") || undefined;
    const sku = params.get("sku") || undefined;
    const orderId = params.get("orderId") || undefined;
    chat.openChat({ seed, sku, orderId });

    const q = new URLSearchParams(params.toString());
    q.delete("chat");
    q.delete("seed");
    q.delete("sku");
    q.delete("orderId");
    const rest = q.toString();
    router.replace(rest ? `/?${rest}` : "/");
  }, [params, chat, router]);

  return null;
}

export default function ChatDeepLink() {
  return (
    <Suspense fallback={null}>
      <ChatDeepLinkInner />
    </Suspense>
  );
}
