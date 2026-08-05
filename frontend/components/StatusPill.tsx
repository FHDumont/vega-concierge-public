// Status do pedido — design custom (ADR-012/013): ponto colorido por severidade +
// rótulo textual (o estado nunca depende só da cor — acessibilidade). Reusa a escala
// pura `orderStatusSeverity` (lib/severity, sem dependência de Splunk) mapeada para
// classes de paleta (globals.css › .ns-status). Substitui o SeverityTag (Splunk) na Loja.
import { OrderStatus } from "@/lib/api";
import { orderStatusSeverity, Severity } from "@/lib/severity";

const SEVERITY_CLASS: Record<Severity, string> = {
  normal: "ok",
  info: "info",
  notice: "pending",
  warning: "pending",
  alert: "fail",
  critical: "fail",
  unknown: "",
};

export default function StatusPill({ status }: { status: OrderStatus }) {
  return <span className={`ns-status ${SEVERITY_CLASS[orderStatusSeverity(status)]}`}>{status}</span>;
}
