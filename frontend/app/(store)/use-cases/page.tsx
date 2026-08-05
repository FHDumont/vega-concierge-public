"use client";
// Use cases no header da loja — acessível logado ou não; flag behind_the_scenes bloqueia PARTICIPANTES.
import FlagGuard from "@/components/FlagGuard";
import UseCasesPanel from "@/components/UseCasesPanel";

export default function UseCasesPage() {
  return (
    <FlagGuard flag="behind_the_scenes">
      <UseCasesPanel />
    </FlagGuard>
  );
}
