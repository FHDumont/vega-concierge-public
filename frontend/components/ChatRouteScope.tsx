"use client";
// Clears chat context chips when leaving the route that "owns" them (PDP, account, deep links).
import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { useChat } from "@/lib/chat-context";

export default function ChatRouteScope() {
  const pathname = usePathname();
  const { contextSku, contextOrderId, setContextSku, clearContextSku, clearContextOrderId } = useChat();

  useEffect(() => {
    const onHome = pathname === "/";
    const productMatch = pathname.match(/^\/product\/([^/]+)/);
    const routeSku = productMatch?.[1]?.toUpperCase();

    if (routeSku) {
      setContextSku(routeSku);
      return;
    }

    // Home keeps the deep link context (?chat=1&sku= / &orderId=) after the URL is stripped.
    if (contextSku && !onHome) {
      clearContextSku(contextSku);
    }
    if (contextOrderId && !onHome && !pathname.startsWith("/account")) {
      clearContextOrderId(contextOrderId);
    }
  }, [pathname, contextSku, contextOrderId, setContextSku, clearContextSku, clearContextOrderId]);

  return null;
}
