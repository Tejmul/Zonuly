/** A company's grade. Monochrome, so the distinction is carried by fill and rule:
 *  a target is solid, a maybe is outlined, a reject is struck through. */
export function TierPill({ tier }: { tier: string | null }) {
  if (!tier) return null;
  const cls =
    tier === "tier1" ? "chip chip-solid"
    : tier === "reject" ? "chip chip-strike"
    : "chip";
  const label =
    tier === "tier1" ? "T1" : tier === "tier2" ? "T2"
    : tier === "prospect" ? "PRO" : tier === "unknown" ? "—" : "REJ";
  return <span className={cls} title={tier}>{label}</span>;
}
