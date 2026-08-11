"use client";
// Store client boundary: cart + chat + chrome. Lives in an explicit "use client" module
// so pages like /checkout share the same ShopContext during SSR/HMR (Next 16).
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
