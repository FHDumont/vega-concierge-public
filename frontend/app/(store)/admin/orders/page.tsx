"use client";
import { AdminBusinessShell, OrdersView, useOrdersData } from "../business-shared";

export default function AdminOrdersPage() {
  const { orders, loadOrders, refreshAll } = useOrdersData();
  return (
    <AdminBusinessShell refreshAll={refreshAll}>
      <OrdersView orders={orders} onRefresh={loadOrders} />
    </AdminBusinessShell>
  );
}
