"use client";
// ADMIN — business overview (/admin). Orders/products have their own routes.
// Legacy ?v=orders|products links redirect once (compat).
import { Suspense } from "react";
import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AdminBusinessShell, OverviewView, useOverviewData } from "./business-shared";

export default function AdminPage() {
  return (
    <Suspense fallback={<div className="ns-adm-empty">Loading…</div>}>
      <AdminPageEntry />
    </Suspense>
  );
}

function AdminPageEntry() {
  const router = useRouter();
  const v = useSearchParams()?.get("v");

  useEffect(() => {
    if (v === "orders") router.replace("/admin/orders");
    else if (v === "products") router.replace("/admin/products");
  }, [v, router]);

  if (v === "orders" || v === "products") {
    return <div className="ns-adm-empty">Loading…</div>;
  }
  return <AdminOverview />;
}

function AdminOverview() {
  const { summary, loadSummary, refreshAll } = useOverviewData();
  return (
    <AdminBusinessShell refreshAll={refreshAll}>
      <OverviewView summary={summary} onRefresh={loadSummary} />
    </AdminBusinessShell>
  );
}
