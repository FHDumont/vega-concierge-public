"use client";
// Header da Loja — design custom (ADR-012): top promo bar + barra horizontal fixa com
// logo, nav de categorias, busca, carrinho e conta. Estilizado pelas variáveis
// de paleta (globals.css › .ns-header). SEM @splunk/react-ui. O TierBadge (telas técnicas)
// é reusado no atalho da conta — pequeno e reativo ao tema, sem refator de carona.
import Link from "next/link";
import { usePathname } from "next/navigation";
import { User } from "@/lib/api";
import { CATEGORIES, Category } from "@/lib/shop";
import { useAuth } from "@/lib/auth";
import { useFlags } from "@/lib/flags";
import TierBadge from "./TierBadge";

const NAV_CATEGORIES = CATEGORIES.filter((c) => c !== "All");

export default function Header({
  search,
  onSearch,
  category,
  onSelectCategory,
  onHome,
  cartCount,
  onOpenCart,
  user,
  themeControl,
}: {
  search: string;
  onSearch: (v: string) => void;
  category: Category;
  onSelectCategory: (c: Category) => void;
  onHome: () => void;
  cartCount: number;
  onOpenCart: () => void;
  user: User | null;
  themeControl?: React.ReactNode;
}) {
  const pathname = usePathname() || "/";
  const useCasesActive = pathname.startsWith("/use-cases");
  const { user: authUser } = useAuth();
  const { flags } = useFlags();
  const showUseCases = authUser?.role === "OWNER" || flags.behind_the_scenes;

  return (
    <header className="ns-header">
      <div className="ns-topbar">
        Free shipping on orders $200+ · $9.99 standard below · 30-day returns · US domestic · Powered by the Vega AI concierge
      </div>
      <div className="ns-wrap ns-nav">
        <Link href="/" className="ns-logo" aria-label="Vega home" onClick={onHome}>
          <svg className="dot" viewBox="0 0 24 24" aria-hidden role="img" style={{ background: "transparent" }}>
            <path d="M12 1.5l2.6 7.9 7.9 2.6-7.9 2.6L12 22.5l-2.6-7.9L1.5 12l7.9-2.6z" fill="var(--accent)" />
          </svg>
          Vega
        </Link>

        <nav className="ns-cats" aria-label="Categories">
          {NAV_CATEGORIES.map((c) => (
            <button
              key={c}
              type="button"
              className={category === c ? "on" : undefined}
              aria-pressed={category === c}
              onClick={() => onSelectCategory(c)}
            >
              {c}
            </button>
          ))}
        </nav>

        <div className="ns-search">
          <span aria-hidden>⌕</span>
          <input
            type="search"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Search products…"
            aria-label="Search products"
          />
        </div>

        <div className="ns-icons">
          <button
            type="button"
            className="ns-icon"
            onClick={onOpenCart}
            aria-label={cartCount > 0 ? `Cart (${cartCount})` : "Cart"}
            title="Cart"
          >
            <span aria-hidden>🛍</span>
            {cartCount > 0 && <span className="count">{cartCount}</span>}
          </button>

          {themeControl}

          {showUseCases && (
            <Link
              href="/use-cases"
              className={`ns-header-cta${useCasesActive ? " on" : ""}`}
              aria-current={useCasesActive ? "page" : undefined}
            >
              Use Cases
            </Link>
          )}

          {user ? (
            <Link href="/account" className="ns-account" aria-label={`Account: ${user.name}`}>
              <span className="who">{user.name}</span>
              <TierBadge tier={user.tier} />
            </Link>
          ) : (
            <Link href="/account" className="ns-account">
              <span aria-hidden>👤</span> Sign in
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}
