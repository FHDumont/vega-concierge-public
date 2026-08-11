"use client";
// Detail of an order (items · total · customer · lifecycle timeline) —
// component shared between Admin (F-014) and the user's order history (F-019).
import { Order, OrderStatus } from "@/lib/api";
import { formatMoney } from "@/lib/shop";
import StatusPill from "@/components/StatusPill";
import NotificationPreview from "@/components/NotificationPreview";
import ReturnRefund from "@/components/ReturnRefund";

const NOTIFY_STATUSES: OrderStatus[] = ["PAID", "SHIPPED", "DELIVERED"];

export function fmtDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
}
export function fmtDateTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

const PIN_COLOR: Record<OrderStatus, string> = {
  PENDING: "var(--sev-notice)",
  PAID: "var(--sev-normal)",
  SHIPPED: "var(--sev-info)",
  DELIVERED: "var(--sev-normal)",
  REFUNDED: "var(--sev-info)",
  FAILED: "var(--sev-critical)",
};

export default function OrderDetail({
  order,
  onBack,
  backLabel = "← Orders",
  showNotificationPreview = false,
  showRefund = false,
  onRefunded,
}: {
  order: Order;
  onBack: () => void;
  backLabel?: string;
  showNotificationPreview?: boolean;
  showRefund?: boolean;
  onRefunded?: (o: Order) => void;
}) {
  const history = order.history ?? [{ status: order.status, at: order.created_at }];
  return (
    <div className="ns-adm-card ns-adm-detail">
      <div className="head">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <button type="button" className="ns-adm-back" onClick={onBack}>{backLabel}</button>
          <span className="id">{order.id}</span>
        </div>
        <StatusPill status={order.status} />
      </div>

      {showNotificationPreview && NOTIFY_STATUSES.includes(order.status) && (
        <NotificationPreview orderId={order.id} />
      )}

      {showRefund && (order.status === "DELIVERED" || order.status === "REFUNDED") && (
        <ReturnRefund order={order} onRefunded={onRefunded} />
      )}

      <div className="grid">
        <div>
          <p className="ns-adm-sub">Items</p>
          {order.items.map((it) => (
            <div className="ns-adm-itemrow" key={it.sku}>
              <span>{it.name} <span className="q">× {it.qty}</span></span>
              <span>{formatMoney(it.price * it.qty)}</span>
            </div>
          ))}
          <div className="ns-adm-total">
            <span>Total</span>
            <span>{formatMoney(order.total)}</span>
          </div>
        </div>

        <div>
          <p className="ns-adm-sub">Customer</p>
          <div className="ns-adm-cust">
            <div className="nm">{order.customer.name}</div>
            <div className="mut">{order.customer.email}</div>
            <div className="mut">{order.customer.address}</div>
            <div className="mut" style={{ marginTop: 6 }}>Placed {fmtDateTime(order.created_at)}</div>
          </div>

          <p className="ns-adm-sub" style={{ marginTop: 18 }}>Lifecycle</p>
          <ul className="ns-adm-timeline">
            {history.map((h, i) => (
              <li className="ns-adm-tl" key={`${h.status}-${i}`}>
                <span className="col">
                  <span className="pin" style={{ background: PIN_COLOR[h.status] }} />
                  <span className="line" />
                </span>
                <span className="body">
                  <span className="st">{h.status}</span>
                  <span className="at"> · {fmtDateTime(h.at)}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
