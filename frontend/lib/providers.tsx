"use client";
// Providers da app. Tema = paleta + esquema light/dark (default Splunk/light), dirigido
// por variáveis CSS em TODA a app (loja, Behind the Scenes, Admin) — design custom, sem
// @splunk/styled-components (F-015 removeu o registry SSR). Ver lib/theme-context, ADR-013.
// Sessão (AuthProvider, F-008) elevada ao topo na F-020: a barra de camada e o Admin/Config
// (owner-only) também precisam saber quem está logado. O `ShopProvider` (carrinho) segue só
// na Loja (route group `(store)`). useAuth resolve sempre este provider global.
import { useEffect } from "react";
import { PaletteProvider } from "./theme-context";
import { AuthProvider } from "./auth";
import { FlagsProvider } from "./flags";
import { initShopperSessionConfig } from "./api";

export default function Providers({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    initShopperSessionConfig();
  }, []);

  return (
    <PaletteProvider>
      <AuthProvider>
        <FlagsProvider>{children}</FlagsProvider>
      </AuthProvider>
    </PaletteProvider>
  );
}
