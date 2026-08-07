"use client";
// Limpa chips de contexto do chat ao sair da rota que os “dona” (PDP, conta, deep links).
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

    // Home mantém contexto de deep link (?chat=1&sku= / &orderId=) após strip da URL.
    if (contextSku && !onHome) {
      clearContextSku(contextSku);
    }
    if (contextOrderId && !onHome && !pathname.startsWith("/account")) {
      clearContextOrderId(contextOrderId);
    }
  }, [pathname, contextSku, contextOrderId, setContextSku, clearContextSku, clearContextOrderId]);

  return null;
}
