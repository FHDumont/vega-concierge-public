// Order status — custom design (ADR-012/013): colored dot by severity +
// text label (state never depends on color alone — accessibility). Reuses the pure
// `orderStatusSeverity` scale (lib/severity, no Splunk dependency) mapped to
// palette classes (globals.css › .ns-status). Replaces the SeverityTag (Splunk) in the Store.
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
