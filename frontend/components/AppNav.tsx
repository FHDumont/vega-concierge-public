"use client";
// Nav lateral unificada do portal (conta + admin). Grupos: topo (Store/Account/Purchases),
// Business, Workshop, Global Settings. Colapsável; persistência em localStorage (F-NAV-1).
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { useFlags } from "@/lib/flags";
import { FeatureFlags } from "@/lib/api";
import { WORKSHOP_SIMULATOR_ENABLED } from "@/lib/workshop-config";

type Item = {
  label: string;
  icon: string;
  href: string;
  match: (path: string) => boolean;
  owner?: boolean;
  flag?: keyof FeatureFlags;
};
type Group = { title?: string; owner?: boolean; flag?: keyof FeatureFlags; items: Item[] };

const TOP_ITEMS: Item[] = [
  {
    label: "Store",
    icon: "🏪",
    href: "/",
    match: (p) =>
      p === "/" ||
      p.startsWith("/product") ||
      p === "/checkout" ||
      p === "/chat",
  },
  { label: "Account", icon: "👤", href: "/account", match: (p) => p === "/account" },
  {
    label: "Purchase history",
    icon: "🧾",
    href: "/account/purchases",
    match: (p) => p.startsWith("/account/purchases"),
  },
  {
    label: "Store policies",
    icon: "📋",
    href: "/account/policies",
    match: (p) => p.startsWith("/account/policies"),
  },
];

export const APP_NAV_GROUPS: Group[] = [
  {
    title: "Business",
    flag: "admin",
    items: [
      { label: "Overview", icon: "▦", href: "/admin", match: (p) => p === "/admin" },
      { label: "Orders", icon: "🧾", href: "/admin/orders", match: (p) => p.startsWith("/admin/orders") },
      { label: "Products", icon: "📦", href: "/admin/products", match: (p) => p.startsWith("/admin/products") },
    ],
  },
  {
    title: "Workshop",
    flag: "admin",
    items: [
      { label: "Simulator", icon: "⚙", href: "/admin/simulator", match: (p) => p.startsWith("/admin/simulator"), flag: "simulator" },
      { label: "Advanced", icon: "⚠", href: "/admin/advanced", match: (p) => p.startsWith("/admin/advanced"), owner: true },
    ],
  },
  {
    title: "Global Settings",
    owner: true,
    items: [
      { label: "Inspector", icon: "🔍", href: "/admin/llm-activity", match: (p) => p.startsWith("/admin/llm-activity"), flag: "inspector" },
      { label: "LLM Providers", icon: "🧠", href: "/admin/config", match: (p) => p.startsWith("/admin/config") },
      { label: "Agents", icon: "🕸", href: "/admin/agents", match: (p) => p.startsWith("/admin/agents") },
      { label: "Connection / Hub", icon: "🔗", href: "/admin/connection", match: (p) => p.startsWith("/admin/connection") },
      { label: "Feature Flags", icon: "🚩", href: "/admin/flags", match: (p) => p.startsWith("/admin/flags") },
    ],
  },
];

const STORE_KEY = "vega.appNav";

function NavItem({ it, pathname }: { it: Item; pathname: string }) {
  const active = it.match(pathname);
  return (
    <Link
      key={it.href}
      href={it.href}
      className={`ns-adm-item ${active ? "active" : ""}`}
      scroll={false}
      title={it.label}
      aria-current={active ? "page" : undefined}
    >
      <span className="ns-adm-item-icon" aria-hidden>{it.icon}</span>
      <span className="ns-adm-item-label">{it.label}</span>
      {it.owner && <span className="ns-adm-item-lock" aria-hidden>🔒</span>}
    </Link>
  );
}

export default function AppNav() {
  const pathname = usePathname() || "/";
  const { user } = useAuth();
  const { flags } = useFlags();
  const isOwner = user?.role === "OWNER";

  const flagOk = (it: Item) => !it.flag || isOwner || flags[it.flag];
  const itemVisible = (it: Item) =>
    (it.href !== "/admin/simulator" || WORKSHOP_SIMULATOR_ENABLED) &&
    (!it.owner || isOwner) &&
    flagOk(it);
  const groupOk = (g: Group) =>
    (!g.owner || isOwner) && (!g.flag || isOwner || flags[g.flag]);
  const visibleItems = (g: Group) => g.items.filter(itemVisible);

  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => { setCollapsed(localStorage.getItem(STORE_KEY) === "1"); }, []);
  function toggle() {
    setCollapsed((c) => { const n = !c; localStorage.setItem(STORE_KEY, n ? "1" : "0"); return n; });
  }

  return (
    <aside className={`ns-adm-aside ${collapsed ? "collapsed" : ""}`}>
      <button type="button" className="ns-adm-collapse" onClick={toggle}
        title={collapsed ? "Expand" : "Collapse"} aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}>
        {collapsed ? "›" : "‹"}
      </button>
      <nav className="ns-adm-groups">
        <div className="ns-adm-group">
          {TOP_ITEMS.map((it) => (
            <NavItem key={it.href} it={it} pathname={pathname} />
          ))}
        </div>
        {APP_NAV_GROUPS.filter((g) => groupOk(g) && visibleItems(g).length > 0).map((g) => (
          <div key={g.title} className="ns-adm-group">
            <div className="ns-adm-group-title">{g.title}</div>
            {visibleItems(g).map((it) => (
              <NavItem key={it.href} it={it} pathname={pathname} />
            ))}
          </div>
        ))}
      </nav>
    </aside>
  );
}
