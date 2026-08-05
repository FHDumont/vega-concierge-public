// Admin dentro do route group (store): header da loja + sidebar AppNav quando logado.
import PortalShell from "@/components/PortalShell";
import FlagGuard from "@/components/FlagGuard";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <PortalShell>
      <FlagGuard flag="admin">{children}</FlagGuard>
    </PortalShell>
  );
}
