// Maps domain states (order, stock) to the Splunk Design System's SEVERITY
// scale (CONVENCOES › Design System / ADR-009). Color + a label/icon
// (never color alone) communicate the state — see SeverityTag.
import { OrderStatus } from "./api";
import { StockState } from "./shop";

// Subset of the scale (Emergency→Info) used by the Shop.
export type Severity = "normal" | "info" | "notice" | "warning" | "alert" | "critical" | "unknown";

export function orderStatusSeverity(status: OrderStatus): Severity {
  switch (status) {
    case "PAID":
      return "normal";
    case "SHIPPED":
      return "info";
    case "DELIVERED":
      return "normal";
    case "PENDING":
      return "notice";
    case "REFUNDED":
      return "info";
    case "FAILED":
      return "critical";
    default:
      return "unknown";
  }
}

export function stockSeverity(state: StockState): Severity {
  if (state === "out") return "alert";
  if (state === "low") return "warning";
  return "normal";
}
