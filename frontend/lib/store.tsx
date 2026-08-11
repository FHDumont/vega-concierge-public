"use client";
// Client-side Shop state shared across routes (home and /product/[sku]).
// Cart persists in localStorage so "add to cart" works from the detail
// page and reflects in the header counter on any route.
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { Product } from "./api";
import { CartItem, Category } from "./shop";

type ShopContextValue = {
  search: string;
  setSearch: (v: string) => void;

  // Category filter shared with the header's horizontal nav (F-009). "All" = no filter.
  category: Category;
  setCategory: (c: Category) => void;

  cart: CartItem[];
  cartCount: number;
  addToCart: (p: Product) => void;
  inc: (sku: string) => void;
  dec: (sku: string) => void;
  setQty: (sku: string, qty: number) => void;
  remove: (sku: string) => void;
  clear: () => void;

  cartOpen: boolean;
  openCart: () => void;
  closeCart: () => void;
};

const ShopContext = createContext<ShopContextValue | null>(null);

const CART_KEY = "vega.cart";

export function ShopProvider({ children }: { children: React.ReactNode }) {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<Category>("All");
  const [cart, setCart] = useState<CartItem[]>([]);
  const [cartOpen, setCartOpen] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  // Loads from localStorage after mount (avoids SSR hydration mismatch).
  useEffect(() => {
    try {
      const c = localStorage.getItem(CART_KEY);
      if (c) setCart(JSON.parse(c));
    } catch {
      /* storage unavailable/corrupted — starts empty */
    }
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (hydrated) localStorage.setItem(CART_KEY, JSON.stringify(cart));
  }, [cart, hydrated]);

  function addToCart(p: Product) {
    setCart((prev) => {
      const found = prev.find((i) => i.product.sku === p.sku);
      if (found) return prev.map((i) => (i.product.sku === p.sku ? { ...i, qty: i.qty + 1 } : i));
      return [...prev, { product: p, qty: 1 }];
    });
    setCartOpen(true);
  }
  function inc(sku: string) {
    setCart((prev) => prev.map((i) => (i.product.sku === sku ? { ...i, qty: i.qty + 1 } : i)));
  }
  function dec(sku: string) {
    setCart((prev) =>
      prev
        .map((i) => (i.product.sku === sku ? { ...i, qty: i.qty - 1 } : i))
        .filter((i) => i.qty > 0),
    );
  }
  // Sets the absolute quantity of an item (used by the cart's Number stepper).
  // qty <= 0 removes the item.
  function setQty(sku: string, qty: number) {
    setCart((prev) =>
      qty <= 0
        ? prev.filter((i) => i.product.sku !== sku)
        : prev.map((i) => (i.product.sku === sku ? { ...i, qty } : i)),
    );
  }
  function remove(sku: string) {
    setCart((prev) => prev.filter((i) => i.product.sku !== sku));
  }
  function clear() {
    setCart([]);
  }

  const cartCount = cart.reduce((s, i) => s + i.qty, 0);

  const value = useMemo<ShopContextValue>(
    () => ({
      search, setSearch,
      category, setCategory,
      cart, cartCount, addToCart, inc, dec, setQty, remove, clear,
      cartOpen, openCart: () => setCartOpen(true), closeCart: () => setCartOpen(false),
    }),
    [search, category, cart, cartCount, cartOpen],
  );

  return <ShopContext.Provider value={value}>{children}</ShopContext.Provider>;
}

export function useShop(): ShopContextValue {
  const ctx = useContext(ShopContext);
  if (!ctx) throw new Error("useShop must be used within ShopProvider");
  return ctx;
}
