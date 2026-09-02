import type { BulkOutcome, BulkResult } from "../../lib/types";

const TONE: Record<BulkResult, string> = {
  changed: "text-ok",
  deleted: "text-danger",
  archived: "text-warn",
  skipped: "text-muted",
  blocked: "text-warn",
  error: "text-danger",
};

const VERB: Record<BulkResult, string> = {
  changed: "will change",
  deleted: "delete",
  archived: "archive",
  skipped: "unchanged",
  blocked: "can’t delete",
  error: "error",
};

/** The shared "here's exactly what happens" list — same for every bulk op,
 *  same shape for the dry-run preview and the applied result. */
export function PreviewTable({ rows }: { rows: BulkOutcome[] }) {
  if (rows.length === 0)
    return <p className="px-1 py-4 text-xs text-muted">Nothing to show.</p>;
  return (
    <div className="max-h-[46vh] overflow-y-auto rounded-lg border border-line md:max-h-none">
      <table className="w-full border-collapse text-xs">
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-b border-line last:border-0">
              <td className="px-3 py-2 align-top font-medium">{r.name}</td>
              <td className={`px-3 py-2 align-top ${TONE[r.result]}`}>
                <span className="font-semibold">{VERB[r.result]}</span>
                {r.detail ? <span className="text-muted"> · {r.detail}</span> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ResultSummary({
  rows,
}: {
  rows: { result: BulkResult }[];
}) {
  const n = (k: BulkResult) => rows.filter((r) => r.result === k).length;
  const parts: string[] = [];
  for (const k of ["changed", "deleted", "archived", "skipped", "blocked", "error"] as BulkResult[]) {
    if (n(k)) parts.push(`${n(k)} ${VERB[k]}`);
  }
  return <span className="tabular-nums">{parts.join(" · ") || "—"}</span>;
}
