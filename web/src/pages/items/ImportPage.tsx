import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiUpload, ApiError } from "../../lib/api";
import type {
  ItemImportBatch,
  ItemImportCommitResult,
  ItemImportCurrentBatch,
  ItemImportOutcome,
  ItemImportReview,
  ItemType,
  StagedItemRow,
} from "../../lib/types";

const OUTCOME_LABEL: Record<ItemImportOutcome, string> = {
  new: "new",
  link: "match → fill blanks",
  skip: "GUID seen — skip",
  flag: "needs decision",
};

const OUTCOME_CLASS: Record<ItemImportOutcome, string> = {
  new: "bg-accent-soft text-accent",
  link: "bg-[#e6efe8] text-ok",
  skip: "bg-[#efe9df] text-muted",
  flag: "bg-[#f1e7d6] text-warn",
};

export function ItemsImportPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | ItemImportOutcome>("all");
  const [uploadErr, setUploadErr] = useState<string | null>(null);
  const [batchMeta, setBatchMeta] = useState<ItemImportBatch | null>(null);
  const [done, setDone] = useState<ItemImportCommitResult | null>(null);
  const [seedAllHsn, setSeedAllHsn] = useState(true);

  const upload = useMutation({
    mutationFn: (f: File) => {
      const form = new FormData();
      form.append("file", f);
      const qs = seedAllHsn ? "?seed_all_hsn=true" : "";
      return apiUpload<ItemImportBatch>(`/items/import${qs}`, form);
    },
    onSuccess: (b) => {
      setUploadErr(null);
      setBatchMeta(b);
      setBatchId(b.batch_id);
    },
    onError: (e) => setUploadErr(e instanceof ApiError ? e.message : "Upload failed"),
  });

  const review = useQuery({
    queryKey: ["item-import", batchId],
    queryFn: () => api<ItemImportReview>(`/items/import/${batchId}`),
    enabled: !!batchId && !done,
  });

  const patchRow = useMutation({
    mutationFn: (args: { id: string; body: Record<string, unknown> }) =>
      api<StagedItemRow>(`/items/import/${batchId}/rows/${args.id}`, {
        method: "PATCH",
        body: args.body,
      }),
    onSuccess: () => review.refetch(),
  });

  const commit = useMutation({
    mutationFn: () =>
      api<ItemImportCommitResult>(`/items/import/${batchId}/commit`, { method: "POST" }),
    onSuccess: (r) => {
      setDone(r);
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["item-tree"] });
    },
  });

  const discard = useMutation({
    mutationFn: () => api<void>(`/items/import/${batchId}`, { method: "DELETE" }),
    onSuccess: () => nav("/items"),
  });

  // A batch staged earlier (another session, or before a reload) is kept in
  // the DB until commit — offer to resume it instead of re-uploading the XML.
  const resume = useQuery({
    queryKey: ["item-import-current"],
    queryFn: () => api<ItemImportCurrentBatch>("/items/import/current"),
    enabled: !batchId && !done,
  });

  const discardCurrent = useMutation({
    mutationFn: (id: string) =>
      api<void>(`/items/import/${id}`, { method: "DELETE" }),
    onSuccess: () => resume.refetch(),
  });

  if (done) {
    return (
      <div className="mx-auto max-w-2xl p-4 sm:p-8">
        <h1 className="font-serif text-2xl font-semibold">Import complete</h1>
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Stat n={done.created} label="Created" />
          <Stat n={done.updated} label="Updated" />
          <Stat n={done.skipped} label="Skipped" />
          <Stat n={done.still_flagged} label="Left flagged" />
          <Stat n={done.hsn_seeded} label="HSN seeded" />
          <Stat n={done.groups_created} label="Groups built" />
        </div>
        {done.still_flagged > 0 && (
          <p className="mt-3 text-sm text-warn">
            {done.still_flagged} row{done.still_flagged === 1 ? "" : "s"} still need a decision —
            re-open the import to resolve them.
          </p>
        )}
        <button className="btn-primary mt-6" onClick={() => nav("/items")}>
          Back to Items
        </button>
      </div>
    );
  }

  if (!batchId) {
    const staged = resume.data?.batch_id ? resume.data : null;
    return (
      <div className="mx-auto max-w-2xl p-4 sm:p-8">
        <h1 className="font-serif text-2xl font-semibold">Import items from Tally</h1>
        {staged && (
          <div className="mt-4 rounded-xl border border-accent/40 bg-accent-soft/40 px-4 py-3 text-sm">
            <p>
              An import staged earlier is waiting for review —{" "}
              <strong>{staged.total}</strong> stock item{staged.total === 1 ? "" : "s"}.
            </p>
            <div className="mt-2 flex gap-2">
              <button className="btn-primary" onClick={() => setBatchId(staged.batch_id)}>
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
          In TallyPrime: <span className="font-mono text-xs">Alt+G</span> →{" "}
          <em>Chart of Accounts → Stock Items</em>, then{" "}
          <span className="font-mono text-xs">Ctrl+E</span> → File Format:{" "}
          <strong>XML (Data Interchange)</strong>. A Stock Summary report export
          will not work — it has no HSN, units or GUID. Drop the masters file here.
          {staged && " Uploading a new file replaces the staged batch above."}
        </p>
        <button
          className="mt-5 w-full rounded-xl border-2 border-dashed border-accent bg-accent-soft/50 p-8 text-sm text-accent"
          onClick={() => fileRef.current?.click()}
          disabled={upload.isPending}
        >
          {upload.isPending ? "Parsing…" : "Click to choose the Tally stock-items XML"}
          <span className="mt-1 block text-[11px] text-muted">
            .xml · UTF-16 or UTF-8 · zero-history dummies auto-skipped
          </span>
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
        <label className="mt-3 flex items-start gap-2 text-xs text-muted">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={seedAllHsn}
            onChange={(e) => setSeedAllHsn(e.target.checked)}
          />
          <span>
            Add every HSN code in the file to the reference list on import.
            Leave this on for a first import — none of the shop&apos;s HSN codes
            are in the list yet, so otherwise every item needs a manual decision.
          </span>
        </label>
        {uploadErr && <p className="err mt-2">{uploadErr}</p>}
        <button className="btn-ghost mt-5" onClick={() => nav("/items")}>
          Cancel
        </button>
      </div>
    );
  }

  const rows = review.data?.rows ?? [];
  const counts = review.data?.counts ?? { new: 0, link: 0, skip: 0, flag: 0 };
  const shown = filter === "all" ? rows : rows.filter((r) => r.outcome === filter);
  const ready = counts.new + counts.link;
  const flagged = counts.flag;

  return (
    <div className="mx-auto max-w-5xl p-4 sm:p-6">
      <div className="mb-2 flex items-baseline justify-between">
        <h1 className="font-serif text-2xl font-semibold">Review the batch</h1>
        <span className="text-sm text-muted">
          {rows.length} stock items · {batchMeta?.dummies_skipped ?? 0} dummies skipped
        </span>
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        <Stat n={counts.new} label="New — will create" tone="accent" />
        <Stat n={counts.link} label="Match — fill blanks" tone="ok" />
        <Stat n={counts.skip} label="GUID seen — skip" />
        <Stat n={counts.flag} label="Needs attention" tone="warn" />
      </div>

      <div className="mb-3 flex flex-wrap gap-2">
        {(["all", "new", "link", "skip", "flag"] as const).map((f) => (
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
        <div className="min-w-[620px]">
          <div className="grid grid-cols-[1fr_120px_110px_120px_1fr] gap-3 bg-[#efe9df] px-4 py-2.5 text-[10px] uppercase tracking-wide text-muted">
            <span>Stock item</span>
            <span>Unit · HSN</span>
            <span>Type</span>
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
            {commit.isPending ? "Importing…" : `Import ${ready} item${ready === 1 ? "" : "s"}`}
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
    <div className={`min-w-[110px] rounded-lg border ${ring} bg-card px-3 py-2`}>
      <div className="font-serif text-lg font-semibold">{n}</div>
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
    </div>
  );
}

function Row({ r, onPatch }: { r: StagedItemRow; onPatch: (body: Record<string, unknown>) => void }) {
  const badHsn = r.flags.find((f) => f.code === "bad_hsn");
  const nearMatch = r.flags.find((f) => f.code === "name_near_match");
  const otherFlags = r.flags.filter(
    (f) => f.code !== "bad_hsn" && f.code !== "name_near_match",
  );
  const parsedBits = [r.parsed.metal, r.parsed.shape, r.parsed.grade, r.parsed.size_text, r.parsed.sku]
    .filter(Boolean)
    .join(" · ");

  return (
    <div
      className={`grid grid-cols-[1fr_120px_110px_120px_1fr] items-start gap-3 border-t border-[#f3eee4] px-4 py-3 text-xs ${
        r.outcome === "flag" ? "bg-[#fdf8ef]" : ""
      }`}
    >
      <div>
        <div className="font-medium">{r.edited_name ?? r.stock_name}</div>
        <div className="text-[10px] text-muted">{r.parent_group ?? "—"}</div>
        {parsedBits && <div className="text-[10px] text-ink-soft/70">{parsedBits}</div>}
      </div>
      <span className="font-mono text-[10px] text-muted">
        {r.base_units ?? "—"} · {r.hsn ?? "—"}
      </span>
      <select
        className="rounded border border-line bg-card px-1.5 py-1 text-[11px]"
        value={r.item_type}
        onChange={(e) => onPatch({ type_override: e.target.value as ItemType })}
      >
        <option value="bulk">BULK</option>
        <option value="mrp">MRP</option>
      </select>
      <span
        className={`inline-block w-fit rounded px-1.5 py-0.5 text-[9px] font-bold uppercase ${OUTCOME_CLASS[r.outcome]}`}
      >
        {OUTCOME_LABEL[r.outcome]}
      </span>
      <div className="text-[11px]">
        {otherFlags.map((f) => (
          <div key={f.code} className="text-warn">
            {f.message}
          </div>
        ))}
        {badHsn && (
          <div>
            <span className="text-warn">{badHsn.message}</span> ·{" "}
            <button
              className="text-accent hover:underline"
              onClick={() => onPatch({ seed_hsn: true, decision: "create" })}
            >
              seed it
            </button>{" "}
            /{" "}
            <button
              className="text-accent hover:underline"
              onClick={() => onPatch({ seed_hsn: false, decision: "create" })}
            >
              import without HSN
            </button>
          </div>
        )}
        {nearMatch && r.outcome === "flag" && (
          <div>
            {nearMatch.message} to <strong>{r.match_item_name}</strong> ·{" "}
            <button
              className="text-accent hover:underline"
              onClick={() => onPatch({ decision: "link" })}
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
            {r.match_item_name} · fills unit / HSN / rate · sets tally_guid
          </span>
        )}
        {r.outcome === "skip" && (
          <span className="text-muted">already imported · nothing blank to fill</span>
        )}
        {r.standard_rate != null && (
          <span className="ml-2 font-mono text-muted">₹{r.standard_rate}</span>
        )}
        {r.outcome !== "skip" && (
          <button
            className="ml-2 text-[10px] text-muted hover:text-danger"
            onClick={() => onPatch({ decision: "skip" })}
          >
            skip
          </button>
        )}
      </div>
    </div>
  );
}
