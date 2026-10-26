import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import type {
  GroupAssignResult,
  GroupMove,
  GroupSuggestion,
  GroupSuggestions,
  ItemCategoryRow,
} from "../../lib/types";

/** Suggestions at or above this share are accepted together by the bottom bar. */
const CONFIDENT = 0.7;

const pct = (n: number) => `${Math.round(n * 100)}%`;
const num = (n: number) => n.toLocaleString("en-IN");

interface Done {
  groups: number;
  items: number;
  /** the groups of each batch, newest last, so "Undo" can take the latest one back */
  batches: string[][];
}

/**
 * Sort the groups that have no category. Every group gets a suggested category from the names of
 * its items; nothing moves until it is accepted, and a group's items move with it.
 */
export function SortGroupsPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [done, setDone] = useState<Done>({ groups: 0, items: 0, batches: [] });
  const [later, setLater] = useState<Set<string>>(new Set());
  const [choosing, setChoosing] = useState<GroupSuggestion | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["group-sort"],
    queryFn: () => api<GroupSuggestions>("/item-groups/uncategorised"),
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["group-sort"] });
    qc.invalidateQueries({ queryKey: ["item-tree"] });
    qc.invalidateQueries({ queryKey: ["item-tree-leaves"] });
    qc.invalidateQueries({ queryKey: ["item-categories"] });
    qc.invalidateQueries({ queryKey: ["items"] });
  };

  const assign = useMutation({
    mutationFn: (moves: GroupMove[]) =>
      api<GroupAssignResult>("/item-groups/assign", { method: "POST", body: { moves } }),
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not move those groups."),
  });

  const move = (moves: GroupMove[]) => {
    setErr(null);
    assign.mutate(moves, {
      onSuccess: (r) => {
        setChoosing(null);
        setDone((d) => ({
          groups: d.groups + r.groups,
          items: d.items + r.items,
          batches: [...d.batches, moves.map((m) => m.group_id)],
        }));
        refresh();
      },
    });
  };

  const undo = () => {
    const last = done.batches[done.batches.length - 1];
    if (!last) return;
    setErr(null);
    assign.mutate(
      last.map((group_id) => ({ group_id })),
      {
        onSuccess: (r) => {
          setDone((d) => ({
            groups: Math.max(0, d.groups - r.groups),
            items: Math.max(0, d.items - r.items),
            batches: d.batches.slice(0, -1),
          }));
          refresh();
        },
      },
    );
  };

  const rows = useMemo(
    () => (list.data?.suggestions ?? []).filter((s) => !later.has(s.group_id)),
    [list.data, later],
  );
  const confident = rows.filter((s) => s.category_id && s.confidence >= CONFIDENT);
  const confidentItems = confident.reduce((n, s) => n + s.item_count, 0);
  const withSuggestion = rows.filter((s) => s.category_id).length;

  if (list.isLoading) return <div className="p-6 text-sm text-muted">Loading…</div>;
  if (list.isError)
    return <div className="p-6 text-sm text-danger">Could not load the groups. Try again.</div>;

  const total = list.data?.groups ?? 0;

  return (
    <div className="mx-auto max-w-2xl p-4 pb-32 sm:p-6 sm:pb-32">
      <div className="mb-3 flex items-center gap-1">
        <button className="btn-ghost -ml-2 border-0 bg-transparent px-3" onClick={() => nav("/items")}>
          ← Items
        </button>
      </div>
      <h1 className="font-serif text-2xl font-semibold">Sort groups</h1>
      <p className="mt-1 text-sm text-muted">
        {total === 0
          ? "Every group has a category."
          : `${total} ${total === 1 ? "group has" : "groups have"} no category (${num(
              list.data?.items ?? 0,
            )} items). ${withSuggestion} ${
              withSuggestion === 1 ? "has" : "have"
            } a suggestion, ${confident.length} with ${pct(CONFIDENT)} or more of their items agreeing.`}
      </p>

      {done.groups > 0 && (
        <div className="mt-4 rounded-xl border border-[#b6d3bf] bg-[#e3efe6] p-4 text-sm text-[#2f5a42]">
          <strong className="block text-base font-semibold text-[#1f4a31]">
            {done.groups} {done.groups === 1 ? "group" : "groups"} sorted
          </strong>
          {num(done.items)} items are now in a category.
          {done.batches.length > 0 && (
            <button
              className="mt-2 block min-h-11 text-left underline disabled:opacity-50"
              disabled={assign.isPending}
              onClick={undo}
            >
              Undo the last batch
            </button>
          )}
        </div>
      )}

      {err && (
        <p className="err mt-3" role="alert">
          {err}
        </p>
      )}

      <div className="mt-4 flex flex-col gap-3">
        {rows.map((s) => (
          <GroupCard
            key={s.group_id}
            s={s}
            busy={assign.isPending}
            onAccept={() => move([{ group_id: s.group_id, category_id: s.category_id }])}
            onMakeCategory={() =>
              move([{ group_id: s.group_id, new_category_name: s.new_category_name }])
            }
            onChoose={() => setChoosing(s)}
            onLater={() => setLater((l) => new Set(l).add(s.group_id))}
          />
        ))}
        {rows.length === 0 && total > 0 && (
          <p className="py-6 text-center text-sm text-muted">
            The rest are set aside for later. Reload to see them again.
          </p>
        )}
      </div>

      {confident.length > 0 && (
        <div
          className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-card px-4 py-3"
          style={{ paddingBottom: "max(12px, env(safe-area-inset-bottom, 0px))" }}
        >
          <div className="mx-auto flex max-w-2xl flex-col gap-1.5">
            <button
              className="btn-primary h-12 w-full"
              disabled={assign.isPending}
              onClick={() =>
                move(confident.map((s) => ({ group_id: s.group_id, category_id: s.category_id })))
              }
            >
              {assign.isPending ? "Moving…" : `Accept ${confident.length} suggestions (${pct(CONFIDENT)}+)`}
            </button>
            <span className="text-center text-xs text-muted">
              Moves {num(confidentItems)} items. Check a row first with Change.
            </span>
          </div>
        </div>
      )}

      {choosing && (
        <CategorySheet
          group={choosing}
          busy={assign.isPending}
          onClose={() => setChoosing(null)}
          onPick={(m) => move([{ group_id: choosing.group_id, ...m }])}
        />
      )}
    </div>
  );
}

function GroupCard({
  s,
  busy,
  onAccept,
  onMakeCategory,
  onChoose,
  onLater,
}: {
  s: GroupSuggestion;
  busy: boolean;
  onAccept: () => void;
  onMakeCategory: () => void;
  onChoose: () => void;
  onLater: () => void;
}) {
  const mixed = s.breakdown.length
    ? `Mixed. ${s.breakdown.map((b) => `${pct(b.share)} ${b.category_name}`).join(", ")}.`
    : null;
  return (
    <div className="card flex flex-col gap-2 p-3.5">
      <div className="flex items-baseline justify-between gap-3">
        <strong className="min-w-0 truncate text-[15px] font-semibold">{s.name}</strong>
        <span className="shrink-0 font-mono text-xs text-muted">{num(s.item_count)} items</span>
      </div>

      {s.category_id ? (
        <>
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="text-sm font-semibold text-accent">→ {s.category_name}</span>
            <span className={`text-xs ${s.confidence >= CONFIDENT ? "text-muted" : "text-warn"}`}>
              {pct(s.confidence)} agree
            </span>
          </div>
          <div className="flex gap-2">
            <button className="btn-primary flex-1" disabled={busy} onClick={onAccept}>
              Accept
            </button>
            <button className="btn-ghost w-24" disabled={busy} onClick={onChoose}>
              Change
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="text-[13px] text-warn">
            {mixed ?? "No category matches."}
            {s.new_category_name ? " This looks like a brand name." : ""}
          </p>
          <div className="flex gap-2">
            {s.new_category_name ? (
              <button
                className="btn flex-1 border border-accent bg-accent-soft text-accent hover:bg-accent-soft/70"
                disabled={busy}
                onClick={onMakeCategory}
              >
                Make “{s.new_category_name}” a category
              </button>
            ) : (
              <button className="btn-ghost flex-1" disabled={busy} onClick={onChoose}>
                Choose a category
              </button>
            )}
            {s.new_category_name ? (
              <button className="btn-ghost w-24" disabled={busy} onClick={onChoose}>
                Choose
              </button>
            ) : (
              <button className="btn w-24 text-muted hover:bg-ground" disabled={busy} onClick={onLater}>
                Later
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

type Pick = { category_id: string } | { new_category_name: string };

/** Bottom sheet: pick an existing category, or type a name to make a new one. */
function CategorySheet({
  group,
  busy,
  onClose,
  onPick,
}: {
  group: GroupSuggestion;
  busy: boolean;
  onClose: () => void;
  onPick: (p: Pick) => void;
}) {
  const [q, setQ] = useState(group.new_category_name ?? "");
  const [picked, setPicked] = useState<string | null>(null);
  const cats = useQuery({
    queryKey: ["item-categories"],
    queryFn: () => api<ItemCategoryRow[]>("/item-categories"),
  });

  const text = q.trim();
  const low = text.toLowerCase();
  const all = cats.data ?? [];
  const shown = all
    .filter((c) => !low || c.name.toLowerCase().includes(low))
    .sort((a, b) => a.name.localeCompare(b.name));
  const exact = all.find((c) => c.name.toLowerCase() === low);
  const canCreate = text.length > 0 && !exact;
  // the Create row is the pick while nothing else is chosen
  const createPicked = canCreate && picked === null;
  const chosen = picked ?? (exact ? exact.id : null);
  const target = createPicked ? text : (all.find((c) => c.id === chosen)?.name ?? null);

  return (
    <div className="fixed inset-0 z-40 flex items-end bg-ink/45" onClick={onClose}>
      <div
        className="flex max-h-[85vh] w-full flex-col rounded-t-2xl bg-card"
        role="dialog"
        aria-label={`Put ${group.name} in a category`}
        onClick={(e) => e.stopPropagation()}
      >
        <span className="mx-auto mt-2.5 h-1 w-10 rounded-full bg-line" />
        <div className="flex items-baseline justify-between px-5 pb-2 pt-2.5">
          <span className="font-serif text-xl font-semibold">Put {group.name} in…</span>
          <span className="text-xs text-muted">{num(group.item_count)} items</span>
        </div>
        <div className="px-4 pb-2">
          <input
            className="field"
            placeholder="Find or create a category"
            aria-label="Find or create a category"
            value={q}
            maxLength={60}
            onChange={(e) => {
              setQ(e.target.value);
              setPicked(null);
            }}
          />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-2">
          {canCreate && (
            <button
              className={`mb-1 flex min-h-14 w-full items-center gap-3 rounded-lg border px-3 text-left ${
                createPicked ? "border-accent bg-accent-soft" : "border-line"
              }`}
              onClick={() => setPicked(null)}
            >
              <span className="grid h-7 w-7 place-items-center rounded-full bg-accent text-lg leading-none text-white">
                +
              </span>
              <span className="text-[15px] font-semibold">Create category “{text}”</span>
            </button>
          )}
          {cats.isLoading && <p className="py-3 text-sm text-muted">Loading…</p>}
          {shown.map((c) => (
            <button
              key={c.id}
              className={`flex min-h-[52px] w-full items-center border-b border-[#f0eadf] px-2 text-left text-[15px] ${
                chosen === c.id ? "bg-accent-soft" : ""
              }`}
              onClick={() => setPicked(c.id)}
            >
              <span className="flex-1">{c.name}</span>
              <span className="font-mono text-xs text-muted">{c.group_count} grp</span>
            </button>
          ))}
        </div>
        <div className="border-t border-line px-4 pb-5 pt-3">
          <button
            className="btn-primary h-12 w-full"
            disabled={busy || !target}
            onClick={() =>
              onPick(createPicked ? { new_category_name: text } : { category_id: chosen as string })
            }
          >
            {target
              ? `${createPicked ? "Create" : "Move to"} “${target}”${
                  createPicked ? " and move" : ""
                } ${num(group.item_count)} items`
              : "Choose a category"}
          </button>
        </div>
      </div>
    </div>
  );
}
