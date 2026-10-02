import { useMemo } from "react";
import { barcodeRuns } from "../../lib/catalog";

const QUIET = 10; // modules of white space each side, as the Code 128 spec asks

/** A Code 128 barcode drawn from the server's bar pattern, so the screen shows exactly what
 *  the label prints. Stretches to its box; give it a width and a height. */
export function Barcode({
  pattern,
  label,
  className = "",
}: {
  pattern: string;
  label: string;
  className?: string;
}) {
  const runs = useMemo(() => barcodeRuns(pattern), [pattern]);
  return (
    <svg
      viewBox={`0 0 ${pattern.length + 2 * QUIET} 10`}
      preserveAspectRatio="none"
      role="img"
      aria-label={label}
      className={`text-ink ${className}`}
      fill="currentColor"
    >
      {runs.map(([start, len]) => (
        <rect key={start} x={QUIET + start} y={0} width={len} height={10} />
      ))}
    </svg>
  );
}
