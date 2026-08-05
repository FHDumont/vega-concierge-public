"use client";
// Limpa chips de contexto do chat ao sair da rota que os “dona” (PDP, conta, deep links).
import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { useChat } from "@/lib/chat-context";

export default function ChatRouteScope() {
  const pathname = usePathname();
  const { contextSku, contextOrderId, clearContextSku, clearContextOrderId } = useChat();

  useEffect(() => {
    const onHome = pathname === "/";
    const productMatch = pathname.match(/^\/product\/([^/]+)/);
    const routeSku = productMatch?.[1];

    // Home mantém contexto de deep link (?chat=1&sku= / &orderId=) após strip da URL.
    if (contextSku && !onHome && routeSku !== contextSku) {
      clearContextSku(contextSku);
    }
    if (contextOrderId && !onHome && !pathname.startsWith("/account")) {
      clearContextOrderId(contextOrderId);
    }
  }, [pathname, contextSku, contextOrderId, clearContextSku, clearContextOrderId]);

  return null;
}
