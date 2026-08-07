"use client";
// Client boundary da loja: carrinho + chat + chrome. Vive num módulo "use client" explícito
// para que páginas como /checkout compartilhem o mesmo ShopContext durante SSR/HMR (Next 16).
import { ShopProvider } from "@/lib/store";
import { ChatProvider } from "@/lib/chat-context";
import { WorkshopProblemsProvider } from "@/lib/workshop-problems";
import StoreChrome from "@/components/StoreChrome";

export default function StoreProviders({ children }: { children: React.ReactNode }) {
  return (
    <ShopProvider>
      <WorkshopProblemsProvider>
        <ChatProvider>
          <StoreChrome>{children}</StoreChrome>
        </ChatProvider>
      </WorkshopProblemsProvider>
    </ShopProvider>
  );
}
