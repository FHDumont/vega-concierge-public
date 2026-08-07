"use client";
// Views compartilhadas do dashboard Business (/admin, /admin/orders, /admin/products).
// Rotas separadas (sem ?v=) — evita useSearchParams no shell e loops de navegação no Next 16.
import { useCallback, useEffect, useState } from "react";
import {
  getAdminSummary, getAdminOrders, getAdminProducts, seedAdminOrders, clearAdminOrders,
  getAdminInsights, SalesSummary, Order, OrderStatus, AdminProduct, AdminInsights,
} from "@/lib/api";
import { formatMoney, LOW_STOCK } from "@/lib/shop";
import StatusPill from "@/components/StatusPill";
import OrderDetail, { fmtDate } from "@/components/OrderDetail";
import AiThinking from "@/components/AiThinking";

const STATUS_ORDER: OrderStatus[] = ["PENDING", "PAID", "SHIPPED", "DELIVERED", "FAILED", "REFUNDED"];
const PAGE_SIZES = [10, 25, 50];

/** Chrome comum (título + seed/clear) das três views de negócio. */
export function AdminBusinessShell({
  refreshAll,
  children,
}: {
  refreshAll: () => void;
  children: React.ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function seed() {
    setBusy(true);
    setNote(null);
    try {
      const n = await seedAdminOrders();
      refreshAll();
      setNote(`Seeded ${n} sample orders.`);
    } catch {
      setNote("Seed failed.");
    } finally {
      setBusy(false);
    }
  }

  async function clearSales() {
    setBusy(true);
    setNote(null);
    setConfirming(false);
    try {
      const { cleared, stock_restored, catalog_restored } = await clearAdminOrders();
      refreshAll();
      const catalogNote = catalog_restored ? ` · restored ${catalog_restored} deleted products` : "";
      setNote(`Cleared ${cleared} orders · restored stock for ${stock_restored} products${catalogNote}.`);
    } catch {
      setNote("Clear failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ns-adm-wrap">
      <div className="ns-adm-top">
        <div>
          <h1>Admin</h1>
          <p className="sub">Business view — sales, orders and inventory for the store owner.</p>
        </div>
        <div className="ns-adm-actions">
          {note && <span className="ns-adm-note">{note}</span>}
          <button type="button" className="ns-adm-btn primary" onClick={seed} disabled={busy}>
            {busy ? "Working…" : "Seed sample orders"}
          </button>
          {confirming ? (
            <span className="ns-adm-confirm">
              Delete all orders &amp; restore stock?
              <button type="button" className="ns-adm-btn danger" onClick={clearSales} disabled={busy}>
                Yes, clear
              </button>
              <button type="button" className="ns-adm-btn" onClick={() => setConfirming(false)} disabled={busy}>
                Cancel
              </button>
            </span>
          ) : (
            <button
              type="button"
              className="ns-adm-btn danger"
              onClick={() => { setConfirming(true); setNote(null); }}
              disabled={busy}
            >
              Clear sales
            </button>
          )}
        </div>
      </div>
      <div className="ns-adm-main">{children}</div>
    </div>
  );
}

/** Refetch ao retomar foco da aba (F-032). */
export function useVisibilityRefresh(refresh: () => void) {
  useEffect(() => {
    function onVisible() {
      if (document.visibilityState === "visible") refresh();
    }
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [refresh]);
}

export function useOverviewData() {
  const [summary, setSummary] = useState<SalesSummary | null>(null);
  const loadSummary = useCallback(async () => {
    setSummary(await getAdminSummary().catch(() => null));
  }, []);
  const refreshAll = useCallback(() => {
    loadSummary();
    getAdminOrders().catch(() => {});
    getAdminProducts().catch(() => {});
  }, [loadSummary]);
  useEffect(() => { loadSummary(); }, [loadSummary]);
  useVisibilityRefresh(refreshAll);
  return { summary, loadSummary, refreshAll };
}

export function useOrdersData() {
  const [orders, setOrders] = useState<Order[] | null>(null);
  const loadOrders = useCallback(async () => {
    setOrders(await getAdminOrders().catch(() => []));
  }, []);
  const refreshAll = useCallback(() => { loadOrders(); }, [loadOrders]);
  useEffect(() => { loadOrders(); }, [loadOrders]);
  useVisibilityRefresh(refreshAll);
  return { orders, loadOrders, refreshAll };
}

export function useProductsData() {
  const [products, setProducts] = useState<AdminProduct[] | null>(null);
  const loadProducts = useCallback(async () => {
    setProducts(await getAdminProducts().catch(() => []));
  }, []);
  const refreshAll = useCallback(() => { loadProducts(); }, [loadProducts]);
  useEffect(() => { loadProducts(); }, [loadProducts]);
  useVisibilityRefresh(refreshAll);
  return { products, loadProducts, refreshAll };
}

function RefreshButton({ onRefresh }: { onRefresh: () => Promise<void> | void }) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      className="ns-adm-btn"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try { await onRefresh(); } finally { setBusy(false); }
      }}
    >
      {busy ? "Refreshing…" : "↻ Refresh"}
    </button>
  );
}

export function OverviewView({ summary, onRefresh }: { summary: SalesSummary | null; onRefresh: () => Promise<void> | void }) {
  if (!summary)
    return (
      <>
        <div className="ns-adm-toolbar"><RefreshButton onRefresh={onRefresh} /></div>
        <div className="ns-adm-empty">Loading sales summary…</div>
      </>
    );
  return (
    <>
      <div className="ns-adm-toolbar"><RefreshButton onRefresh={onRefresh} /></div>
      <InsightsCard />
      <div className="ns-adm-kpis">
        <Kpi label="Net revenue" value={formatMoney(summary.net_revenue)} hint="gross − refunds" />
        <Kpi label="Gross revenue" value={formatMoney(summary.gross_revenue)} />
        <Kpi label="Refunded" value={formatMoney(summary.refunded_amount)} />
        <Kpi label="Returns" value={String(summary.returned_orders)} hint="refunded orders" />
        <Kpi label="Orders" value={String(summary.orders)} />
        <Kpi label="Paid orders" value={String(summary.paid_orders)} />
        <Kpi label="Avg ticket" value={formatMoney(summary.avg_ticket)} />
      </div>
      <div className="ns-adm-card">
        <h2>Orders by status</h2>
        {summary.orders === 0 ? (
          <p className="ns-adm-empty">No orders yet — seed sample data or place an order in the store.</p>
        ) : (
          <div className="ns-adm-status-cards">
            {STATUS_ORDER.map((s) => {
              const n = summary.by_status[s] || 0;
              return (
                <div className={`ns-adm-status-card ${statusClass(s)}`} key={s}>
                  <span className={`ns-status ${statusClass(s)}`}>{s}</span>
                  <span className="val">{n}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}

export function OrdersView({ orders, onRefresh }: { orders: Order[] | null; onRefresh: () => Promise<void> | void }) {
  const [selected, setSelected] = useState<Order | null>(null);
  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(1);

  const back = () => { setSelected(null); onRefresh(); };

  if (!orders) return <div className="ns-adm-empty">Loading orders…</div>;
  if (selected) return <OrderDetail order={selected} onBack={back} backLabel="← Orders" />;
  if (orders.length === 0)
    return (
      <div className="ns-adm-card">
        <div className="ns-adm-cardhead">
          <h2>Orders (0)</h2>
          <RefreshButton onRefresh={onRefresh} />
        </div>
        <p className="ns-adm-empty">No orders yet — seed sample data or place an order in the store.</p>
      </div>
    );

  const totalPages = Math.max(1, Math.ceil(orders.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const start = (safePage - 1) * pageSize;
  const slice = orders.slice(start, start + pageSize);

  return (
    <div className="ns-adm-card">
      <div className="ns-adm-cardhead">
        <h2>Orders ({orders.length})</h2>
        <RefreshButton onRefresh={onRefresh} />
      </div>
      <table className="ns-adm-table">
        <thead>
          <tr>
            <th>Order</th>
            <th>Date</th>
            <th>Customer</th>
            <th>Items</th>
            <th>Status</th>
            <th style={{ textAlign: "right" }}>Total</th>
          </tr>
        </thead>
        <tbody>
          {slice.map((o) => (
            <tr key={o.id} onClick={() => setSelected(o)}>
              <td className="id">{o.id}</td>
              <td className="when">{fmtDate(o.created_at)}</td>
              <td>{o.customer.name}</td>
              <td>{o.items.reduce((s, i) => s + i.qty, 0)}</td>
              <td><StatusPill status={o.status} /></td>
              <td className="num">{formatMoney(o.total)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="ns-adm-pager">
        <label className="size">
          Per page
          <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}>
            {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <span className="range">
          {start + 1}–{Math.min(start + pageSize, orders.length)} of {orders.length}
        </span>
        <div className="nav">
          <button type="button" className="ns-adm-btn" disabled={safePage <= 1} onClick={() => setPage(safePage - 1)}>← Prev</button>
          <span className="page">Page {safePage} of {totalPages}</span>
          <button type="button" className="ns-adm-btn" disabled={safePage >= totalPages} onClick={() => setPage(safePage + 1)}>Next →</button>
        </div>
      </div>
    </div>
  );
}

export function ProductsView({ products, onRefresh }: { products: AdminProduct[] | null; onRefresh: () => Promise<void> | void }) {
  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(1);

  if (!products) return <div className="ns-adm-empty">Loading products…</div>;
  if (products.length === 0)
    return (
      <div className="ns-adm-card">
        <div className="ns-adm-cardhead">
          <h2>Products (0)</h2>
          <RefreshButton onRefresh={onRefresh} />
        </div>
        <p className="ns-adm-empty">No products in the catalog.</p>
      </div>
    );

  const totalPages = Math.max(1, Math.ceil(products.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const start = (safePage - 1) * pageSize;
  const slice = products.slice(start, start + pageSize);

  return (
    <div className="ns-adm-card">
      <div className="ns-adm-cardhead">
        <h2>Products ({products.length})</h2>
        <RefreshButton onRefresh={onRefresh} />
      </div>
      <table className="ns-adm-table">
        <thead>
          <tr>
            <th>SKU</th>
            <th>Product</th>
            <th style={{ textAlign: "right" }}>Price</th>
            <th style={{ textAlign: "right" }}>Stock</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {slice.map((p) => (
            <tr key={p.sku} style={{ cursor: "default", opacity: p.deleted ? 0.55 : 1 }}>
              <td className="ns-adm-sku">{p.sku}</td>
              <td>{p.name}</td>
              <td className="num">{formatMoney(p.price)}</td>
              <td className="num"><Stock units={p.stock} /></td>
              <td>{p.deleted ? "Deleted" : "Active"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="ns-adm-pager">
        <label className="size">
          Per page
          <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}>
            {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <span className="range">
          {start + 1}–{Math.min(start + pageSize, products.length)} of {products.length}
        </span>
        <div className="nav">
          <button type="button" className="ns-adm-btn" disabled={safePage <= 1} onClick={() => setPage(safePage - 1)}>← Prev</button>
          <span className="page">Page {safePage} of {totalPages}</span>
          <button type="button" className="ns-adm-btn" disabled={safePage >= totalPages} onClick={() => setPage(safePage + 1)}>Next →</button>
        </div>
      </div>
    </div>
  );
}

function Kpi({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="ns-adm-kpi">
      <p className="lbl">{label}</p>
      <p className="val">{value}</p>
      {hint && <p className="ns-adm-kpi-hint">{hint}</p>}
    </div>
  );
}

function InsightsCard() {
  const [data, setData] = useState<AdminInsights | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [requested, setRequested] = useState(false);

  const load = useCallback(async () => {
    setRequested(true);
    setLoading(true);
    setFailed(false);
    try {
      setData(await getAdminInsights());
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, []);

  return (
    <div className="ns-adm-card ns-adm-insights">
      <div className="ns-adm-cardhead">
        <h2><span className="ns-spark sm" aria-hidden>✦</span> AI insights</h2>
        {requested && (
          <button type="button" className="ns-adm-btn" disabled={loading} onClick={load}>
            {loading ? "Thinking…" : "↻ Regenerate"}
          </button>
        )}
      </div>
      <p className="ns-adm-sub" style={{ margin: "0 0 14px" }}>
        AI summarizes recent sales, flags anomalies, and suggests restock — optional, on demand.
      </p>
      {!requested && (
        <button type="button" className="ns-adm-btn" onClick={load}>
          Generate insights
        </button>
      )}
      {requested && loading && !data && <AiThinking label="Analyzing recent sales" />}
      {requested && failed && (
        <p className="ns-adm-empty">We couldn’t load insights right now. Please try again.</p>
      )}
      {data && (
        <>
          <p className="lead">{data.summary}</p>
          <p className="ns-adm-sub" style={{ marginTop: 4 }}>Last {data.period_days} days</p>
          {data.anomalies.length > 0 && (
            <ul className="ns-adm-anom">
              {data.anomalies.map((a, i) => (
                <li key={i}><span className="ns-status warn">Anomaly</span> {a}</li>
              ))}
            </ul>
          )}
          <p className="ns-adm-sub" style={{ marginTop: 14 }}>Restock suggestions</p>
          {data.restock.length === 0 ? (
            <p className="ns-adm-empty">Stock looks healthy — nothing to reorder.</p>
          ) : (
            <div className="ns-adm-restock">
              {data.restock.map((r) => (
                <span key={r.sku} className={`chip ${r.stock === 0 ? "out" : "low"}`}>
                  {r.name} <b>{r.stock}</b>
                </span>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Stock({ units }: { units: number }) {
  const state = units === 0 ? "out" : units <= LOW_STOCK ? "low" : "in";
  const tag = state === "out" ? "Out of stock" : state === "low" ? "Low" : "In stock";
  return (
    <span className={`ns-adm-stock ${state}`}>
      {units} <span className="tag">{tag}</span>
    </span>
  );
}

function statusClass(s: OrderStatus): string {
  if (s === "PAID" || s === "DELIVERED") return "ok";
  if (s === "SHIPPED") return "info";
  if (s === "PENDING") return "pending";
  if (s === "REFUNDED") return "warn";
  return "fail";
}
