"use client";
// App providers. Theme = palette + light/dark scheme (default Splunk/light), driven
// by CSS variables across the ENTIRE app (shop, Behind the Scenes, Admin) — custom design, no
// @splunk/styled-components (F-015 removed the SSR registry). See lib/theme-context, ADR-013.
// Session (AuthProvider, F-008) hoisted to the top in F-020: the layer bar and Admin/Config
// (owner-only) also need to know who's logged in. The `ShopProvider` (cart) stays only
// in the Shop (route group `(store)`). useAuth always resolves this global provider.
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
