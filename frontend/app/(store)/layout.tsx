// Layout das rotas de loja (home + detalhe + conta): provê o estado de cliente
// (carrinho/busca via ShopProvider) e o chrome (header + carrinho).
// ChatProvider (F-051) expõe o widget flutuante global.
import StoreProviders from "@/components/StoreProviders";

export default function StoreLayout({ children }: { children: React.ReactNode }) {
  return <StoreProviders>{children}</StoreProviders>;
}
