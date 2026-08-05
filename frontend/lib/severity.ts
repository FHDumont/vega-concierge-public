// Mapeia estados do domínio (pedido, estoque) para a escala de SEVERIDADE do Splunk
// Design System (CONVENCOES › Design System / ADR-009). A cor + um rótulo/ícone
// (nunca só cor) comunicam o estado — ver SeverityTag.
import { OrderStatus } from "./api";
import { StockState } from "./shop";

// Subconjunto da escala (Emergency→Info) usado pela Loja.
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
