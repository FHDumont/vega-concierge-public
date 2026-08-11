// User tier badge (F-008) — custom design driven by palettes (ADR-012/013).
// The EMPHASIS grows with the tier (subtle Standard → Platinum filled with the accent
// color), conveying hierarchy through more than color alone (text label always present —
// accessibility). Styled via palette variables (globals.css › .ns-tier). Migrated
// out of @splunk/themes in F-010.
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
