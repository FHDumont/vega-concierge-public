"use client";
// Chrome da Loja (header + carrinho slide-over) presente em todas as rotas de loja.
// Lê o estado de cliente do ShopProvider. Buscar fora da home navega para "/".
import { usePathname, useRouter } from "next/navigation";
import Header from "@/components/Header";
import Cart from "@/components/Cart";
import ConciergeLauncher from "@/components/ConciergeLauncher";
import ThemePopup from "@/components/ThemePopup";
import ChatDeepLink from "@/components/ChatDeepLink";
import ChatRouteScope from "@/components/ChatRouteScope";
import { useShop } from "@/lib/store";
import { useAuth } from "@/lib/auth";

export default function StoreChrome({ children }: { children: React.ReactNode }) {
  const shop = useShop();
  const auth = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  function onCheckout() {
    shop.closeCart();
    if (auth.ready && !auth.user) {
      router.push("/account?return=/checkout");
      return;
    }
    router.push("/checkout");
  }

  function onSearch(v: string) {
    shop.setSearch(v);
    if (pathname !== "/") router.push("/");
  }

  function onSelectCategory(c: typeof shop.category) {
    shop.setCategory(c);
    if (pathname !== "/") router.push("/");
  }

  function onHome() {
    shop.setSearch("");
    shop.setCategory("All");
    if (pathname !== "/") router.push("/");
  }

  return (
    <div className="ns-store">
      <Header
        search={shop.search}
        onSearch={onSearch}
        category={shop.category}
        onSelectCategory={onSelectCategory}
        onHome={onHome}
        cartCount={shop.cartCount}
        onOpenCart={shop.openCart}
        user={auth.user}
        themeControl={<ThemePopup />}
      />
      {children}
      <Cart
        open={shop.cartOpen}
        items={shop.cart}
        onClose={shop.closeCart}
        onSetQty={shop.setQty}
        onRemove={shop.remove}
        onAdd={shop.addToCart}
        onCheckout={onCheckout}
      />
      <ChatDeepLink />
      <ChatRouteScope />
      <ConciergeLauncher />
    </div>
  );
}
