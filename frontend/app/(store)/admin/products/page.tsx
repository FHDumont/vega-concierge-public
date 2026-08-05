"use client";
import { AdminBusinessShell, ProductsView, useProductsData } from "../business-shared";

export default function AdminProductsPage() {
  const { products, loadProducts, refreshAll } = useProductsData();
  return (
    <AdminBusinessShell refreshAll={refreshAll}>
      <ProductsView products={products} onRefresh={loadProducts} />
    </AdminBusinessShell>
  );
}
