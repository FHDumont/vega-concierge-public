// Admin inside the (store) route group: store header + AppNav sidebar when logged in.
import PortalShell from "@/components/PortalShell";
import FlagGuard from "@/components/FlagGuard";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <PortalShell>
      <FlagGuard flag="admin">{children}</FlagGuard>
    </PortalShell>
  );
}
