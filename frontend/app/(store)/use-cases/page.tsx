"use client";
// Use cases in the store header — accessible logged in or not; the behind_the_scenes flag blocks PARTICIPANTS.
import FlagGuard from "@/components/FlagGuard";
import UseCasesPanel from "@/components/UseCasesPanel";

export default function UseCasesPage() {
  return (
    <FlagGuard flag="behind_the_scenes">
      <UseCasesPanel />
    </FlagGuard>
  );
}
