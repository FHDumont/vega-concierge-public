import { vegaVersionLabel } from "@/lib/version";

/** Store footer — includes the baked version when available (F-DEPLOY-PROD-1). */
export default function StoreFooter() {
  return (
    <footer className="ns-footer">
      Vega · Your AI shopping concierge · {vegaVersionLabel()}
    </footer>
  );
}
