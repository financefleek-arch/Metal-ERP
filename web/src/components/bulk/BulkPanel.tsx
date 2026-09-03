import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import { useVocab } from "../../lib/reference";
import type {
  BulkDeleteResult,
  BulkField,
  BulkUpdateResult,
  GroupOut,
  ItemCategoryRow,
  ItemListItem,
} from "../../lib/types";
import { PreviewTable, ResultSummary } from "./PreviewTable";

export type BulkMode = "fields" | "category" | "delete";

/**
 * The bulk workspace: pick values → Preview (server dry-run) → Apply.
 * Rendered in the Items right pane on desktop and as a full-screen route on
 * mobile — identical markup, the parent decides the frame.
 */
export function BulkPanel({
  mode,
  ids,
  items,
  onClose,
  onDone,
}: {
  mode: BulkMode;
  ids: string[];
  /** the selected rows we already hold, for names + type-aware skip hints */
  items: ItemListItem[];
  onClose: () => void;
  onDone: (summary: string) => void;
}) {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);

  const title =
    mode === "fields"
      ? `Edit ${ids.length} item${ids.length === 1 ? "" : "s"}`
      : mode === "category"
        ? `Move ${ids.length} item${ids.length === 1 ? "" : "s"}`
        : `Delete ${ids.length} item${ids.length === 1 ? "" : "s"}?`;

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["items"] });
    qc.invalidateQueries({ queryKey: ["item-tree"] });
    qc.invalidateQueries({ queryKey: ["item-tree-leaves"] });
    qc.invalidateQueries({ queryKey: ["item-categories"] });
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <h2 className="font-serif text-lg font-semibold">{title}</h2>
        <button className="btn-ghost h-8 px-3 text-xs" onClick={onClose}>
          Cancel
        </button>
      </div>
      {err && <p className="err">{err}</p>}
      {mode === "fields" && (
        <FieldsFlow ids={ids} items={items} setErr={setErr} onDone={onDone} after={invalidate} />
      )}
      {mode === "category" && (
        <CategoryFlow ids={ids} setErr={setErr} onDone={onDone} after={invalidate} />
      )}
      {mode === "delete" && (
        <DeleteFlow ids={ids} setErr={setErr} onDone={onDone} after={invalidate} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// shared: a two-step (preview → apply) footer + result view
// ---------------------------------------------------------------------------

type Res = BulkUpdateResult | BulkDeleteResult;

function useTwoStep<T extends Res>(
  run: (dryRun: boolean) => Promise<T>,
  { setErr, onDone, after }: { setErr: (s: string | null) => void; onDone: (s: string) => void; after: () => void },
) {
  const [preview, setPreview] = useState<T | null>(null);
  const previewM = useMutation({
    mutationFn: () => run(true),
    onSuccess: (d) => {
      setPreview(d);
      setErr(null);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Preview failed"),
  });
  const applyM = useMutation({
    mutationFn: () => run(false),
    onSuccess: (d) => {
      after();
      onDone(summarize(d));
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Apply failed"),
  });
  return { preview, setPreview, previewM, applyM };
}

function summarize(d: Res): string {
  if ("deleted" in d)
    return [
      d.deleted && `${d.deleted} deleted`,
      d.archived && `${d.archived} archived`,
      d.blocked && `${d.blocked} left (on documents)`,
      d.errors && `${d.errors} failed`,
    ]
      .filter(Boolean)
      .join(" · ") || "Nothing changed";
  return [
    `${d.changed} updated`,
    d.unchanged && `${d.unchanged} unchanged`,
    d.errors && `${d.errors} failed`,
    d.learned_rule_ids.length && `${d.learned_rule_ids.length} rule(s) learned`,
  ]
    .filter(Boolean)
    .join(" · ");
}

function StepFooter({
  preview,
  onBack,
  onPreview,
  onApply,
  previewing,
  applying,
  applyLabel,
  danger,
}: {
  preview: Res | null;
  onBack: () => void;
  onPreview: () => void;
  onApply: () => void;
  previewing: boolean;
  applying: boolean;
  applyLabel: string;
  danger?: boolean;
}) {
  return (
    <div className="sticky bottom-0 -mx-4 flex items-center gap-2 border-t border-line bg-card/95 px-4 py-3 text-[11px] text-muted backdrop-blur md:static md:mx-0 md:border-0 md:bg-transparent md:px-0 md:py-1">
      {preview ? (
        <>
          <ResultSummary rows={preview.rows} />
          <button className="btn-ghost ml-auto h-9 px-3 text-xs" onClick={onBack}>
            Back
          </button>
          <button
            className={`h-9 px-3 text-xs ${danger ? "btn-primary !bg-danger" : "btn-primary"}`}
            disabled={applying}
            onClick={onApply}
          >
            {applying ? "Applying…" : applyLabel}
          </button>
        </>
      ) : (
        <button
          className="btn-primary ml-auto h-9 px-4 text-xs"
          disabled={previewing}
          onClick={onPreview}
        >
          {previewing ? "Checking…" : "Preview change"}
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// flow 1: edit fields
// ---------------------------------------------------------------------------

type FieldSpec = {
  key: BulkField;
  label: string;
  kind: "vocab" | "number" | "select" | "textarea";
  vocab?: "uoms" | "metals" | "shapes" | "finishes";
  options?: { value: string; label: string }[];
  hint?: string;
};

const FIELD_SPECS: FieldSpec[] = [
  { key: "uom", label: "Unit (UOM)", kind: "vocab", vocab: "uoms" },
  { key: "purchase_uom", label: "Purchase unit", kind: "vocab", vocab: "uoms" },
  {
    key: "default_discount_pct",
    label: "Discount %",
    kind: "number",
    hint: "MRP items only — BULK items are skipped",
  },
  { key: "default_rate", label: "Default rate", kind: "number" },
  {
    key: "item_type",
    label: "Type",
    kind: "select",
    options: [
      { value: "bulk", label: "⚖ BULK" },
      { value: "mrp", label: "📦 MRP" },
    ],
  },
  { key: "metal", label: "Metal", kind: "vocab", vocab: "metals" },
  { key: "shape", label: "Shape", kind: "vocab", vocab: "shapes" },
  { key: "finish", label: "Finish", kind: "vocab", vocab: "finishes" },
  {
    key: "status",
    label: "Status",
    kind: "select",
    options: [
      { value: "confirmed", label: "Confirm" },
      { value: "unconfirmed", label: "Mark unconfirmed" },
    ],
  },
  { key: "notes", label: "Notes", kind: "textarea" },
];

function FieldsFlow({
  ids,
  items,
  setErr,
  onDone,
  after,
}: {
  ids: string[];
  items: ItemListItem[];
  setErr: (s: string | null) => void;
  onDone: (s: string) => void;
  after: () => void;
}) {
  const uoms = useVocab("uoms");
  const metals = useVocab("metals");
  const shapes = useVocab("shapes");
  const finishes = useVocab("finishes");
  const vocabByName = { uoms, metals, shapes, finishes };

  const [enabled, setEnabled] = useState<Set<BulkField>>(new Set());
  const [values, setValues] = useState<Record<string, string>>({});
  const [notesMode, setNotesMode] = useState<"replace" | "append">("replace");

  const nBulk = items.filter((i) => i.item_type === "bulk").length;

  const run = (dryRun: boolean) => {
    const fields: Record<string, string> = {};
    for (const k of enabled) fields[k] = values[k] ?? "";
    return api<BulkUpdateResult>(`/items/bulk?dry_run=${dryRun}`, {
      method: "PATCH",
      body: { ids, fields, fields_set: [...enabled], notes_mode: notesMode },
    });
  };
  const { preview, setPreview, previewM, applyM } = useTwoStep(run, { setErr, onDone, after });

  if (preview)
    return (
      <>
        <p className="text-xs text-muted">
          {preview.changed} of {ids.length} items change. The rest already have these values.
        </p>
        <PreviewTable rows={preview.rows} />
        {preview.learned_rule_ids.length > 0 && (
          <p className="text-[11px] text-accent-dark">
            Teaches the classifier {preview.learned_rule_ids.length} rule(s).
          </p>
        )}
        <StepFooter
          preview={preview}
          onBack={() => setPreview(null)}
          onPreview={() => previewM.mutate()}
          onApply={() => applyM.mutate()}
          previewing={previewM.isPending}
          applying={applyM.isPending}
          applyLabel={`Apply to ${preview.changed} item${preview.changed === 1 ? "" : "s"}`}
        />
      </>
    );

  return (
    <>
      <p className="text-xs text-muted">
        Turn on a field to set it for all {ids.length}. Anything left off is untouched.
      </p>
      <div className="flex flex-col divide-y divide-line">
        {FIELD_SPECS.map((spec) => {
          const on = enabled.has(spec.key);
          return (
            <div key={spec.key} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5">
              <label className="flex min-w-[8rem] items-center gap-2 text-xs font-medium">
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-[color:theme(colors.accent.DEFAULT)]"
                  checked={on}
                  onChange={(e) => {
                    const next = new Set(enabled);
                    if (e.target.checked) next.add(spec.key);
                    else next.delete(spec.key);
                    setEnabled(next);
                  }}
                />
                {spec.label}
              </label>
              <div className={`min-w-[10rem] flex-1 ${on ? "" : "pointer-events-none opacity-40"}`}>
                <FieldControl
                  spec={spec}
                  value={values[spec.key] ?? ""}
                  onChange={(v) => setValues((s) => ({ ...s, [spec.key]: v }))}
                  vocabList={spec.vocab ? (vocabByName[spec.vocab].data ?? []) : []}
                />
                {spec.key === "notes" && on && (
                  <div className="mt-1 flex gap-3 text-[11px] text-muted">
                    {(["replace", "append"] as const).map((m) => (
                      <label key={m} className="flex items-center gap-1">
                        <input
                          type="radio"
                          name="notesmode"
                          checked={notesMode === m}
                          onChange={() => setNotesMode(m)}
                        />
                        {m === "replace" ? "Replace" : "Add a line"}
                      </label>
                    ))}
                  </div>
                )}
                {spec.hint && on && (
                  <p className="mt-1 text-[11px] text-muted">{spec.hint}</p>
                )}
              </div>
            </div>
          );
        })}
      </div>
      {enabled.has("default_discount_pct") && nBulk > 0 && (
        <p className="text-[11px] text-warn">
          {nBulk} of the selected items are BULK — discount % will be skipped for those.
        </p>
      )}
      <StepFooter
        preview={null}
        onBack={() => {}}
        onPreview={() => {
          if (enabled.size === 0) {
            setErr("Turn on at least one field.");
            return;
          }
          previewM.mutate();
        }}
        onApply={() => {}}
        previewing={previewM.isPending}
        applying={false}
        applyLabel=""
      />
    </>
  );
}

function FieldControl({
  spec,
  value,
  onChange,
  vocabList,
}: {
  spec: FieldSpec;
  value: string;
  onChange: (v: string) => void;
  vocabList: string[];
}) {
  if (spec.kind === "textarea")
    return (
      <textarea
        className="field h-16 resize-y py-2"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  if (spec.kind === "number")
    return (
      <input
        className="field font-mono"
        inputMode="decimal"
        placeholder="new value"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  if (spec.kind === "select")
    return (
      <select className="field" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">— pick —</option>
        {spec.options!.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  // vocab: a select with free-text fallback
  return (
    <select className="field" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">— pick —</option>
      {vocabList.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
      {value && !vocabList.includes(value) && <option value={value}>{value}</option>}
    </select>
  );
}

// ---------------------------------------------------------------------------
// flow 2: category / group
// ---------------------------------------------------------------------------

function CategoryFlow({
  ids,
  setErr,
  onDone,
  after,
}: {
  ids: string[];
  setErr: (s: string | null) => void;
  onDone: (s: string) => void;
  after: () => void;
}) {
  const cats = useQuery({
    queryKey: ["item-categories"],
    queryFn: () => api<ItemCategoryRow[]>("/item-categories"),
  });
  const [catId, setCatId] = useState("");
  const [groupChoice, setGroupChoice] = useState<"keep" | "remove" | string>("keep");

  const groups = useQuery({
    queryKey: ["item-groups", catId],
    queryFn: () => api<GroupOut[]>(`/item-groups?category_id=${catId}`),
    enabled: !!catId,
  });

  const run = (dryRun: boolean) => {
    const fields: Record<string, string | null> = {};
    const fieldsSet: string[] = [];
    if (catId) {
      fields.category_id = catId;
      fieldsSet.push("category_id");
    }
    if (groupChoice === "remove") {
      fields.group_id = null;
      fieldsSet.push("group_id");
    } else if (groupChoice !== "keep") {
      fields.group_id = groupChoice;
      fieldsSet.push("group_id");
    }
    return api<BulkUpdateResult>(`/items/bulk?dry_run=${dryRun}`, {
      method: "PATCH",
      body: { ids, fields, fields_set: fieldsSet },
    });
  };
  const { preview, setPreview, previewM, applyM } = useTwoStep(run, { setErr, onDone, after });

  const nothingChosen = !catId && (groupChoice === "keep");

  if (preview)
    return (
      <>
        <p className="text-xs text-muted">
          {preview.changed} of {ids.length} items move.
          {preview.learned_rule_ids.length > 0 &&
            ` The classifier learns ${preview.learned_rule_ids.length} rule(s) from the unconfirmed ones.`}
        </p>
        <PreviewTable rows={preview.rows} />
        <StepFooter
          preview={preview}
          onBack={() => setPreview(null)}
          onPreview={() => previewM.mutate()}
          onApply={() => applyM.mutate()}
          previewing={previewM.isPending}
          applying={applyM.isPending}
          applyLabel={`Move ${preview.changed} item${preview.changed === 1 ? "" : "s"}`}
        />
      </>
    );

  return (
    <>
      <p className="text-xs text-muted">
        Pick a category, and optionally a group inside it. Groups pass their HSN &amp; unit down to
        items that have none.
      </p>
      <div className="flex flex-col gap-3">
        <div>
          <span className="label">Category</span>
          <select
            className="field"
            value={catId}
            onChange={(e) => {
              setCatId(e.target.value);
              setGroupChoice("keep");
            }}
          >
            <option value="">— leave as-is —</option>
            {(cats.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <span className="label">Group</span>
          <select
            className="field"
            value={groupChoice}
            disabled={!catId}
            onChange={(e) => setGroupChoice(e.target.value)}
          >
            <option value="keep">— leave as-is —</option>
            <option value="remove">Remove from group</option>
            {(groups.data ?? []).map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
          {!catId && (
            <p className="mt-1 text-[11px] text-muted">Choose a category to list its groups.</p>
          )}
        </div>
      </div>
      <StepFooter
        preview={null}
        onBack={() => {}}
        onPreview={() => {
          if (nothingChosen) {
            setErr("Pick a category or a group to move to.");
            return;
          }
          previewM.mutate();
        }}
        onApply={() => {}}
        previewing={previewM.isPending}
        applying={false}
        applyLabel=""
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// flow 3: delete
// ---------------------------------------------------------------------------

function DeleteFlow({
  ids,
  setErr,
  onDone,
  after,
}: {
  ids: string[];
  setErr: (s: string | null) => void;
  onDone: (s: string) => void;
  after: () => void;
}) {
  const run = (dryRun: boolean, onBlocked: "skip" | "archive" = "skip") =>
    api<BulkDeleteResult>(`/items/bulk-delete?dry_run=${dryRun}`, {
      method: "POST",
      body: { ids, on_blocked: onBlocked },
    });

  const [preview, setPreview] = useState<BulkDeleteResult | null>(null);
  const previewM = useMutation({
    mutationFn: () => run(true),
    onSuccess: (d) => {
      setPreview(d);
      setErr(null);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Preview failed"),
  });
  const deleteM = useMutation({
    mutationFn: () => run(false, "skip"),
    onSuccess: (d) => {
      after();
      onDone(
        [d.deleted && `${d.deleted} deleted`, d.blocked && `${d.blocked} kept (on documents)`]
          .filter(Boolean)
          .join(" · "),
      );
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Delete failed"),
  });
  const archiveM = useMutation({
    mutationFn: () => run(false, "archive"),
    onSuccess: (d) => {
      after();
      onDone(`${d.deleted} deleted · ${d.archived} archived`);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Archive failed"),
  });

  // auto-run the preview once on mount
  const ran = useRef(false);
  useEffect(() => {
    if (ran.current) return;
    ran.current = true;
    previewM.mutate();
  }, [previewM]);

  if (!preview)
    return <p className="py-6 text-xs text-muted">Checking which items can be deleted…</p>;

  const deletable = preview.deleted;
  const blocked = preview.blocked;

  return (
    <>
      <p className="text-xs text-muted">
        Items on invoices or inward bills can’t be deleted — archive those instead.
      </p>
      <PreviewTable rows={preview.rows} />
      <div className="sticky bottom-0 -mx-4 flex flex-wrap items-center gap-2 border-t border-line bg-card/95 px-4 py-3 text-[11px] text-muted backdrop-blur md:static md:mx-0 md:border-0 md:bg-transparent md:px-0 md:py-1">
        <span className="tabular-nums">
          {deletable} deletable{blocked ? ` · ${blocked} on documents` : ""}
        </span>
        <div className="ml-auto flex gap-2">
          {blocked > 0 && (
            <button
              className="btn-ghost h-9 px-3 text-xs !border-warn !text-warn"
              disabled={archiveM.isPending}
              onClick={() => archiveM.mutate()}
            >
              {archiveM.isPending ? "…" : `Archive the ${blocked}`}
            </button>
          )}
          <button
            className="btn-primary h-9 px-3 text-xs !bg-danger"
            disabled={deletable === 0 || deleteM.isPending}
            onClick={() => deleteM.mutate()}
          >
            {deleteM.isPending ? "Deleting…" : `Delete the ${deletable}`}
          </button>
        </div>
      </div>
    </>
  );
}
