// Layout das rotas de loja (home + detalhe + conta): provê o estado de cliente
// (carrinho/busca via ShopProvider) e o chrome (header + carrinho).
// ChatProvider (F-051) expõe o widget flutuante global.
import { ShopProvider } from "@/lib/store";
import { ChatProvider } from "@/lib/chat-context";
import StoreChrome from "@/components/StoreChrome";

export default function StoreLayout({ children }: { children: React.ReactNode }) {
  return (
    <ShopProvider>
      <ChatProvider>
        <StoreChrome>{children}</StoreChrome>
      </ChatProvider>
    </ShopProvider>
  );
}
