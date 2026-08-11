"use client";
// Purchase history — dedicated route /account/purchases (F-NAV-1).
import { useCallback, useEffect, useState } from "react";
import { Order, getOrders, getOrder } from "@/lib/api";
import { formatMoney } from "@/lib/shop";
import { useAuth } from "@/lib/auth";
import { useChat } from "@/lib/chat-context";
import StatusPill from "@/components/StatusPill";
import OrderDetail from "@/components/OrderDetail";

const PAGE_SIZES = [10, 25, 50];

function RefreshButton({ onRefresh }: { onRefresh: () => Promise<void> | void }) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      className="ns-btn-ghost"
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

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
  } catch {
    return iso;
  }
}

function itemSummary(o: Order): string {
  const n = o.items.reduce((s, i) => s + i.qty, 0);
  return `${n} item${n === 1 ? "" : "s"}`;
}

function PurchaseHistory({
  orders,
  error,
  onReload,
}: {
  orders: Order[] | null;
  error: boolean;
  onReload: () => Promise<void> | void;
}) {
  const [detail, setDetail] = useState<Order | null>(null);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [detailError, setDetailError] = useState(false);
  const [pageSize, setPageSize] = useState(10);
  const [page, setPage] = useState(1);
  const { clearContextOrderId } = useChat();

  useEffect(() => {
    if (!detail) clearContextOrderId();
  }, [detail, clearContextOrderId]);

  async function open(id: string) {
    setLoadingId(id);
    setDetailError(false);
    try {
      setDetail(await getOrder(id));
    } catch {
      setDetailError(true);
    } finally {
      setLoadingId(null);
    }
  }

  function back() {
    setDetail(null);
    onReload();
  }

  if (detail) {
    return (
      <section className="ns-panelcard">
        <h2 className="ns-card-title">Purchase history</h2>
        <OrderDetail
          order={detail}
          onBack={back}
          backLabel="← Purchase history"
          showNotificationPreview
          showRefund
          onRefunded={(o) => setDetail(o)}
        />
      </section>
    );
  }

  return (
    <section className="ns-panelcard">
      <div className="ns-card-head">
        <h2 className="ns-card-title">Purchase history</h2>
        <RefreshButton onRefresh={onReload} />
      </div>
      {detailError && (
        <div className="ns-alert error" style={{ marginBottom: 14 }} role="alert">
          We couldn’t open that order. Please try again.
        </div>
      )}
      {error ? (
        <div className="ns-alert error">We couldn’t load your orders. Please refresh.</div>
      ) : !orders ? (
        <div className="ns-center">
          <span className="ns-spinner" aria-hidden />
        </div>
      ) : orders.length === 0 ? (
        <div className="ns-alert">You haven’t placed any orders yet.</div>
      ) : (
        (() => {
          const totalPages = Math.max(1, Math.ceil(orders.length / pageSize));
          const safePage = Math.min(page, totalPages);
          const start = (safePage - 1) * pageSize;
          const slice = orders.slice(start, start + pageSize);
          return (
            <>
              <table className="ns-orders ns-orders-click">
                <thead>
                  <tr>
                    <th>Order</th>
                    <th>Date</th>
                    <th>Items</th>
                    <th>Status</th>
                    <th className="right">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {slice.map((o) => (
                    <tr
                      key={o.id}
                      onClick={() => open(o.id)}
                      role="button"
                      tabIndex={0}
                      aria-busy={loadingId === o.id}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          open(o.id);
                        }
                      }}
                    >
                      <td className="id">{o.id}</td>
                      <td>{formatDate(o.created_at)}</td>
                      <td>{itemSummary(o)}</td>
                      <td>
                        <StatusPill status={o.status} />
                      </td>
                      <td className="right">{formatMoney(o.total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {orders.length > PAGE_SIZES[0] && (
                <div className="ns-hist-pager">
                  <label className="size">
                    Per page
                    <select
                      value={pageSize}
                      onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}
                    >
                      {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
                    </select>
                  </label>
                  <span className="range">
                    {start + 1}–{Math.min(start + pageSize, orders.length)} of {orders.length}
                  </span>
                  <div className="nav">
                    <button type="button" className="ns-btn-ghost" disabled={safePage <= 1} onClick={() => setPage(safePage - 1)}>
                      ← Prev
                    </button>
                    <span className="page">Page {safePage} of {totalPages}</span>
                    <button type="button" className="ns-btn-ghost" disabled={safePage >= totalPages} onClick={() => setPage(safePage + 1)}>
                      Next →
                    </button>
                  </div>
                </div>
              )}
            </>
          );
        })()
      )}
    </section>
  );
}

export default function PurchasesPage() {
  const { user, ready } = useAuth();
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [error, setError] = useState(false);

  const loadOrders = useCallback(() => {
    setError(false);
    return getOrders()
      .then(setOrders)
      .catch(() => setError(true));
  }, []);

  useEffect(() => {
    if (user) loadOrders();
  }, [user, loadOrders]);

  useEffect(() => {
    function onVisible() {
      if (document.visibilityState === "visible" && user) loadOrders();
    }
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [user, loadOrders]);

  if (!ready || !user) {
    return (
      <div className="ns-account-page">
        <div className="ns-center">
          <span className="ns-spinner" aria-hidden />
        </div>
      </div>
    );
  }

  return (
    <div className="ns-account-page">
      <h1>Purchase history</h1>
      <PurchaseHistory orders={orders} error={error} onReload={loadOrders} />
    </div>
  );
}
