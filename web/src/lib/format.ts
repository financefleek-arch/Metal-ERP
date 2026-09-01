/** Small display helpers shared across pages. */

/** "34d ago" / "3mo ago" / "never" from an ISO timestamp or null. */
export function lastSeenLabel(iso: string | null): string {
  if (!iso) return "never billed";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "never billed";
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days <= 0) return "billed today";
  if (days === 1) return "billed yesterday";
  if (days < 45) return `last billed ${days}d ago`;
  const months = Math.round(days / 30);
  if (months < 24) return `last billed ${months}mo ago`;
  return `last billed ${Math.round(months / 12)}y ago`;
}

const MISSING_LABEL: Record<string, string> = {
  address: "address",
  address_line1: "address line 1",
  address_city: "city",
  address_state: "state",
};

export function missingLabel(tokens: string[]): string {
  return tokens.map((t) => MISSING_LABEL[t] ?? t).join(", ");
}
