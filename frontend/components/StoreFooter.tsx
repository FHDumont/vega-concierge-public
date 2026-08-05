import { vegaVersionLabel } from "@/lib/version";

/** Rodapé da loja — inclui versão baked quando disponível (F-DEPLOY-PROD-1). */
export default function StoreFooter() {
  return (
    <footer className="ns-footer">
      Vega · Your AI shopping concierge · {vegaVersionLabel()}
    </footer>
  );
}
