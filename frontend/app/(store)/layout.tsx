// Layout for the store routes (home + detail + account): provides the customer state
// (cart/search via ShopProvider) and the chrome (header + cart).
// ChatProvider (F-051) exposes the global floating widget.
import StoreProviders from "@/components/StoreProviders";

export default function StoreLayout({ children }: { children: React.ReactNode }) {
  return <StoreProviders>{children}</StoreProviders>;
}
