import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiUpload, ApiError } from "../../lib/api";
import type {
  ImportBatch,
  ImportCommitResult,
  ImportCurrentBatch,
  ImportOutcome,
  ImportReview,
  PartyRole,
  StagedRow,
} from "../../lib/types";

const OUTCOME_LABEL: Record<ImportOutcome, string> = {
  new: "new",
  link: "match → fill blanks",
  flag: "needs decision",
  skip: "skipped",
};

const OUTCOME_CLASS: Record<ImportOutcome, string> = {
  new: "bg-accent-soft text-accent",
  link: "bg-[#e6efe8] text-ok",
  flag: "bg-[#f1e7d6] text-warn",
  skip: "bg-[#efe9df] text-muted",
};

function missingLabel(tokens: string[]): string {
  const m: Record<string, string> = { address: "address", "gstin/pan": "GSTIN/PAN", state: "state" };
  return tokens.map((t) => m[t] ?? t).join(", ");
}

export function ImportPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | ImportOutcome>("all");
  const [uploadErr, setUploadErr] = useState<string | null>(null);
  const [done, setDone] = useState<ImportCommitResult | null>(null);

  const upload = useMutation({
    mutationFn: (f: File) => {
      const form = new FormData();
      form.append("file", f);
      return apiUpload<ImportBatch>("/parties/import", form);
    },
    onSuccess: (b) => {
      setUploadErr(null);
      setBatchId(b.batch_id);
    },
    onError: (e) => setUploadErr(e instanceof ApiError ? e.message : "Upload failed"),
  });

  const review = useQuery({
    queryKey: ["party-import", batchId],
    queryFn: () => api<ImportReview>(`/parties/import/${batchId}`),
    enabled: !!batchId && !done,
  });

  const patchRow = useMutation({
    mutationFn: (args: { id: string; body: Record<string, unknown> }) =>
      api<StagedRow>(`/parties/import/${batchId}/rows/${args.id}`, {
        method: "PATCH",
        body: args.body,
      }),
    onSuccess: () => review.refetch(),
  });

  const commit = useMutation({
    mutationFn: () =>
      api<ImportCommitResult>(`/parties/import/${batchId}/commit`, { method: "POST" }),
    onSuccess: (r) => {
      setDone(r);
      qc.invalidateQueries({ queryKey: ["parties"] });
    },
  });

  const discard = useMutation({
    mutationFn: () => api<void>(`/parties/import/${batchId}`, { method: "DELETE" }),
    onSuccess: () => nav("/parties"),
  });

  // A batch staged earlier (another session, or before a reload) is kept in
  // the DB until commit — offer to resume it instead of re-uploading the XML.
  const resume = useQuery({
    queryKey: ["party-import-current"],
    queryFn: () => api<ImportCurrentBatch>("/parties/import/current"),
    enabled: !batchId && !done,
  });

  const discardCurrent = useMutation({
    mutationFn: (id: string) =>
      api<void>(`/parties/import/${id}`, { method: "DELETE" }),
    onSuccess: () => resume.refetch(),
  });

  // ---- done state ----
  if (done) {
    return (
      <div className="mx-auto max-w-2xl p-4 sm:p-8">
        <h1 className="font-serif text-2xl font-semibold">Import complete</h1>
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat n={done.created} label="Created" />
          <Stat n={done.updated} label="Updated" />
          <Stat n={done.skipped} label="Skipped" />
          <Stat n={done.still_flagged} label="Left flagged" />
        </div>
        {done.still_flagged > 0 && (
          <p className="mt-3 text-sm text-warn">
            {done.still_flagged} row{done.still_flagged === 1 ? "" : "s"} still need a decision —
            re-open the import to resolve them.
          </p>
        )}
        <button className="btn-primary mt-6" onClick={() => nav("/parties")}>
          Back to Parties
        </button>
      </div>
    );
  }

  // ---- upload state ----
  if (!batchId) {
    const staged = resume.data?.batch_id ? resume.data : null;
    return (
      <div className="mx-auto max-w-2xl p-4 sm:p-8">
        <h1 className="font-serif text-2xl font-semibold">Import parties from Tally</h1>
        {staged && (
          <div className="mt-4 rounded-xl border border-accent/40 bg-accent-soft/40 px-4 py-3 text-sm">
            <p>
              An import staged earlier is waiting for review —{" "}
              <strong>{staged.total}</strong> ledger{staged.total === 1 ? "" : "s"}.
            </p>
            <div className="mt-2 flex gap-2">
              <button
                className="btn-primary"
                onClick={() => setBatchId(staged.batch_id)}
              >
                Review it
              </button>
              <button
                className="btn-ghost"
                disabled={discardCurrent.isPending}
                onClick={() => discardCurrent.mutate(staged.batch_id!)}
              >
                {discardCurrent.isPending ? "Discarding…" : "Discard & start over"}
              </button>
            </div>
          </div>
        )}
        <p className="mt-4 max-w-prose text-sm text-muted">
          In Tally: <em>Gateway → Display More Reports → List of Accounts → Ledgers</em>, then{" "}
          <span className="font-mono text-xs">Alt+E</span> → format <strong>XML</strong> (or{" "}
          <em>Export → Masters → All Masters</em>). Drop that file here.
          {staged && " Uploading a new file replaces the staged batch above."}
        </p>
        <button
          className="mt-5 w-full rounded-xl border-2 border-dashed border-accent bg-accent-soft/50 p-8 text-sm text-accent"
          onClick={() => fileRef.current?.click()}
          disabled={upload.isPending}
        >
          {upload.isPending ? "Parsing…" : "Click to choose the Tally masters XML"}
          <span className="mt-1 block text-[11px] text-muted">.xml · UTF-16 or UTF-8</span>
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".xml,text/xml,application/xml"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) upload.mutate(f);
            e.target.value = "";
          }}
        />
        {uploadErr && <p className="err mt-2">{uploadErr}</p>}
        <button className="btn-ghost mt-5" onClick={() => nav("/parties")}>
          Cancel
        </button>
      </div>
    );
  }

  // ---- review state ----
  const rows = review.data?.rows ?? [];
  const counts = review.data?.counts ?? { new: 0, link: 0, flag: 0, skip: 0 };
  const shown = filter === "all" ? rows : rows.filter((r) => r.outcome === filter);
  const ready = counts.new + counts.link;
  const flagged = counts.flag;

  return (
    <div className="mx-auto max-w-5xl p-4 sm:p-6">
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="font-serif text-2xl font-semibold">Review the batch</h1>
        <span className="text-sm text-muted">{rows.length} ledgers parsed</span>
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        <Stat n={counts.new} label="New — will create" tone="accent" />
        <Stat n={counts.link} label="Match — fill blanks" tone="ok" />
        <Stat n={counts.flag} label="Needs attention" tone="warn" />
        <Stat n={counts.skip} label="Skipped" />
      </div>

      <div className="mb-3 flex flex-wrap gap-2">
        {(["all", "new", "link", "flag", "skip"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`rounded-full border px-3 py-1 text-[11px] ${
              filter === f
                ? "border-ink bg-ink text-ground"
                : "border-line bg-card text-muted hover:bg-ground"
            }`}
          >
            {f === "all" ? `All ${rows.length}` : `${OUTCOME_LABEL[f]} ${counts[f]}`}
          </button>
        ))}
      </div>

      <div className="card overflow-x-auto">
        <div className="min-w-[640px]">
          <div className="grid grid-cols-[1fr_130px_110px_130px_1fr] gap-3 bg-[#efe9df] px-4 py-2.5 text-[10px] uppercase tracking-wide text-muted">
            <span>Tally ledger</span>
            <span>GSTIN</span>
            <span>Role</span>
            <span>Outcome</span>
            <span>Notes</span>
          </div>
          {review.isLoading && <div className="px-4 py-6 text-sm text-muted">Loading…</div>}
          {shown.map((r) => (
            <Row key={r.id} r={r} onPatch={(body) => patchRow.mutate({ id: r.id, body })} />
          ))}
          {!review.isLoading && shown.length === 0 && (
            <div className="px-4 py-6 text-center text-sm text-muted">Nothing in this filter.</div>
          )}
        </div>
      </div>

      <div className="mt-4 flex flex-col gap-3 rounded-xl border border-line bg-ground px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="text-sm">
          <strong>{ready}</strong> ready
          {flagged > 0 && (
            <>
              {" · "}
              <strong>{flagged}</strong> need a decision first
            </>
          )}
        </div>
        <div className="flex gap-2">
          <button className="btn-ghost" onClick={() => discard.mutate()}>
            Cancel
          </button>
          <button
            className="btn-primary"
            disabled={ready === 0 || commit.isPending}
            onClick={() => commit.mutate()}
          >
            {commit.isPending ? "Importing…" : `Import ${ready} part${ready === 1 ? "y" : "ies"}`}
          </button>
        </div>
      </div>
      {commit.isError && (
        <p className="err mt-2">
          {commit.error instanceof ApiError ? commit.error.message : "Import failed"}
        </p>
      )}
    </div>
  );
}

function Stat({ n, label, tone }: { n: number; label: string; tone?: "accent" | "ok" | "warn" }) {
  const ring =
    tone === "accent"
      ? "border-accent/40"
      : tone === "ok"
        ? "border-ok/40"
        : tone === "warn"
          ? "border-warn/40"
          : "border-line";
  return (
    <div className={`min-w-[120px] rounded-lg border ${ring} bg-card px-3 py-2`}>
      <div className="font-serif text-lg font-semibold">{n}</div>
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
    </div>
  );
}

function Row({ r, onPatch }: { r: StagedRow; onPatch: (body: Record<string, unknown>) => void }) {
  const nearMatch = r.flags.find((f) => f.code === "name_near_match");
  const blocking = r.flags.filter((f) => f.code !== "name_near_match");
  return (
    <div
      className={`grid grid-cols-[1fr_130px_110px_130px_1fr] items-center gap-3 border-t border-[#f3eee4] px-4 py-3 text-xs ${
        r.outcome === "flag" ? "bg-[#fdf8ef]" : ""
      }`}
    >
      <div>
        <div className="font-medium">{r.edited_name ?? r.ledger_name}</div>
        <div className="text-[10px] text-muted">{r.parent_group ?? "—"}</div>
      </div>
      <span className="font-mono text-[10px] text-muted">{r.gstin ?? "—"}</span>
      <select
        className="rounded border border-line bg-card px-1.5 py-1 text-[11px]"
        value={r.role}
        onChange={(e) => onPatch({ role_override: e.target.value as PartyRole })}
      >
        <option value="customer">customer</option>
        <option value="supplier">supplier</option>
        <option value="both">both</option>
      </select>
      <span
        className={`inline-block w-fit rounded px-1.5 py-0.5 text-[9px] font-bold uppercase ${OUTCOME_CLASS[r.outcome]}`}
      >
        {OUTCOME_LABEL[r.outcome]}
      </span>
      <div className="text-[11px]">
        {blocking.map((f) => (
          <div key={f.code} className="text-warn">
            {f.message}
          </div>
        ))}
        {nearMatch && r.outcome === "flag" && (
          <div>
            {nearMatch.message} to <strong>{r.match_party_name}</strong> ·{" "}
            <button
              className="text-accent hover:underline"
              onClick={() => onPatch({ decision: "link", link_party_id: r.match_party_id })}
            >
              link
            </button>{" "}
            /{" "}
            <button
              className="text-accent hover:underline"
              onClick={() => onPatch({ decision: "create" })}
            >
              create new
            </button>
          </div>
        )}
        {r.outcome === "link" && !nearMatch && (
          <span className="text-muted">
            {r.match_method === "exact_gstin" ? "GSTIN" : "PAN"} = {r.match_party_name} · fills blanks
          </span>
        )}
        {r.missing.length > 0 && r.outcome !== "flag" && (
          <span className="text-muted">missing {missingLabel(r.missing)}</span>
        )}
        {r.outcome !== "skip" ? (
          <button
            className="ml-2 text-[10px] text-muted hover:text-danger"
            onClick={() => onPatch({ decision: "skip" })}
          >
            skip
          </button>
        ) : (
          <button
            className="text-[10px] text-accent hover:underline"
            onClick={() => onPatch({ decision: "pending" })}
          >
            include
          </button>
        )}
      </div>
    </div>
  );
}
