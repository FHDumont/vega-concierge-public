// Selo de tier do usuário (F-008) — design custom dirigido por paletas (ADR-012/013).
// O REALCE cresce com o tier (Standard discreto → Platinum preenchido com a cor de
// acento), comunicando hierarquia por mais que a cor (rótulo textual sempre presente —
// acessibilidade). Estilizado por variáveis de paleta (globals.css › .ns-tier). Migrado
// para fora do @splunk/themes na F-010.
import { Tier } from "@/lib/api";

const LABEL: Record<Tier, string> = {
  STANDARD: "Standard",
  GOLD: "Gold",
  PLATINUM: "Platinum",
};

const CLASS: Record<Tier, string> = {
  STANDARD: "standard",
  GOLD: "gold",
  PLATINUM: "platinum",
};

export default function TierBadge({ tier }: { tier: Tier }) {
  return (
    <span className={`ns-tier ${CLASS[tier]}`} title={`${LABEL[tier]} member`}>
      {LABEL[tier]}
    </span>
  );
}
