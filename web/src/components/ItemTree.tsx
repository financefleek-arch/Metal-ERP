import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { useDebounced } from "../lib/useDebounced";
import { availabilityLabel, availabilityTone, tallyBadge } from "../lib/items";
import {
  categoryNode,
  groupNode,
  looseNode,
  type NodeAction,
  type TreeNodeSel,
} from "../lib/itemNodes";
import type { ItemListItem, TreeCategory, TreeLeaf } from "../lib/types";
import { NodeMenu, NodeMenuEntry } from "./NodeMenu";

const OPEN_KEY = "items.tree.open.v2";

/** `loose` lists the "Ungrouped" rows the user has CLOSED: they start open, to save a click */
type OpenState = { cats: string[]; groups: string[]; loose: string[] };

function loadOpen(): OpenState {
  try {
    const raw = window.sessionStorage.getItem(OPEN_KEY);
    if (raw) {
      const p = JSON.parse(raw) as Partial<OpenState>;
      return { cats: p.cats ?? [], groups: p.groups ?? [], loose: p.loose ?? [] };
    }
  } catch {
    /* storage can be blocked; the tree just starts closed */
  }
  return { cats: [], groups: [], loose: [] };
}

/** A checkbox that can show "some of the children are ticked". */
function Check({
  checked,
  partial,
  label,
  onChange,
  lockedBy,
}: {
  checked: boolean;
  partial?: boolean;
  label: string;
  onChange: () => void;
  /** set when a parent row is ticked: this one shows as ticked and cannot be changed alone */
  lockedBy?: string;
}) {
  const ref = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = !!partial && !checked;
  }, [partial, checked]);
  return (
    <input
      ref={ref}
      type="checkbox"
      aria-label={label}
      checked={checked || !!lockedBy}
      disabled={!!lockedBy}
      title={lockedBy ? `Included because “${lockedBy}” is ticked. Untick that to choose differently.` : undefined}
      onChange={onChange}
      onClick={(e) => e.stopPropagation()}
      className={`h-4 w-4 shrink-0 accent-[color:theme(colors.accent.DEFAULT)] md:h-3.5 md:w-3.5 ${
        lockedBy ? "opacity-60" : ""
      }`}
    />
  );
}

/**
 * The catalogue tree: category → product group → leaf. `/items/tree` returns only the skeleton
 * (groups + counts); the leaves for a node are fetched from `/items/tree/leaves` the first time
 * it is expanded, so this stays cheap at 10k items.
 *
 * Selecting: a leaf checkbox ticks that one item; a category / group / "Ungrouped" checkbox selects
 * the WHOLE node as a filter (no tick limit); tick as many categories and groups as you like and
 * they act together. The ⋯ menu runs an action on one whole node. Drag a size onto a group or category (or a group onto a category) to move it.
 */
export function ItemTree({
  selectedItemId,
  selectedGroupId,
  writable,
  catalogModule,
  selectedIds,
  onToggleLeaf,
  nodeKeys,
  onPickNode,
  onNodeAction,
}: {
  selectedItemId: string | null;
  selectedGroupId: string | null;
  /** may change items (owner / accountant); a viewer sees no checkboxes or menus */
  writable: boolean;
  catalogModule: boolean;
  selectedIds: Set<string>;
  onToggleLeaf: (id: string) => void;
  /** keys of the ticked categories / groups; several can be ticked together */
  nodeKeys: Set<string>;
  onPickNode: (node: TreeNodeSel) => void;
  onNodeAction: (node: TreeNodeSel, action: NodeAction) => void;
}) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const tree = useQuery({
    queryKey: ["item-tree"],
    queryFn: () => api<TreeCategory[]>("/items/tree"),
  });
  const initial = useMemo(loadOpen, []);
  const [openCats, setOpenCats] = useState<Set<string>>(new Set(initial.cats));
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set(initial.groups));
  const [closedLoose, setClosedLoose] = useState<Set<string>>(new Set(initial.loose));
  const [search, setSearch] = useState("");
  const dq = useDebounced(search.trim(), 250);
  const [note, setNote] = useState<string | null>(null);
  const drag = useRef<{ kind: "leaf" | "group"; id: string } | null>(null);
  const [dropKey, setDropKey] = useState<string | null>(null);

  useEffect(() => {
    try {
      window.sessionStorage.setItem(
        OPEN_KEY,
        JSON.stringify({ cats: [...openCats], groups: [...openGroups], loose: [...closedLoose] }),
      );
    } catch {
      /* ignore */
    }
  }, [openCats, openGroups, closedLoose]);

  const hits = useQuery({
    queryKey: ["items", "tree-search", dq],
    enabled: dq.length >= 2,
    queryFn: () => api<ItemListItem[]>(`/items?q=${encodeURIComponent(dq)}`),
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["item-tree"] });
    qc.invalidateQueries({ queryKey: ["item-tree-leaves"] });
    qc.invalidateQueries({ queryKey: ["items"] });
    qc.invalidateQueries({ queryKey: ["item-categories"] });
  };
  const move = useMutation({
    mutationFn: (a: { path: string; body: Record<string, unknown> }) =>
      api(a.path, { method: "PATCH", body: a.body }),
    onSuccess: () => {
      setNote(null);
      refresh();
    },
    onError: (e) => setNote(e instanceof ApiError ? e.message : "Could not move that."),
  });

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, key: string) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setter(next);
  };

  const q = search.trim().toLowerCase();
  const cats = useMemo(() => {
    const all = tree.data ?? [];
    if (!q) return all;
    return all
      .map((c) => {
        if (c.name.toLowerCase().includes(q)) return c;
        const groups = c.groups.filter((g) => g.name.toLowerCase().includes(q));
        return groups.length ? { ...c, groups } : null;
      })
      .filter((c): c is TreeCategory => c !== null);
  }, [tree.data, q]);

  if (tree.isLoading) return <div className="px-3 py-6 text-xs text-muted">Loading…</div>;
  if ((tree.data ?? []).length === 0)
    return (
      <div className="px-3 py-8 text-center text-xs text-muted">
        No categories yet. Add one, then group your items.
      </div>
    );

  const dropOn = (key: string, accepts: (d: NonNullable<typeof drag.current>) => boolean) =>
    writable
      ? {
          onDragOver: (e: React.DragEvent) => {
            if (drag.current && accepts(drag.current)) {
              e.preventDefault();
              if (dropKey !== key) setDropKey(key);
            }
          },
          onDragLeave: () => setDropKey((k) => (k === key ? null : k)),
        }
      : {};

  const leafPartial = (query: string) =>
    selectedIds.size > 0 &&
    qc
      .getQueriesData<TreeLeaf[]>({ queryKey: ["item-tree-leaves", query] })
      .some(([, leaves]) => leaves?.some((l) => selectedIds.has(l.id)));

  const dropCls = (key: string) => (dropKey === key ? " outline outline-2 -outline-offset-2 outline-accent" : "");

  return (
    <div className="text-xs">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-line bg-ground px-3 py-2">
        <input
          className="field h-7 min-w-0 flex-1 text-xs"
          placeholder="find a category, group or item…"
          aria-label="Search the tree"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <button
          className="rounded-full border border-line bg-card px-2.5 py-1 text-[10px] text-muted hover:bg-ground"
          onClick={() => setOpenCats(new Set((tree.data ?? []).map((c) => c.id ?? "__none__")))}
        >
          Open all
        </button>
        <button
          className="rounded-full border border-line bg-card px-2.5 py-1 text-[10px] text-muted hover:bg-ground"
          onClick={() => {
            setOpenCats(new Set());
            setOpenGroups(new Set());
            setClosedLoose(new Set());
          }}
        >
          Close all
        </button>
      </div>
      {note && (
        <div className="border-b border-line bg-[#f4dcd8] px-3 py-2 text-[11px] text-danger" role="alert">
          {note}
        </div>
      )}
      {q && dq.length >= 2 && (hits.data?.length ?? 0) > 0 && (
        <div className="border-b border-line">
          <div className="bg-[#efe6d4]/50 px-3 py-1.5 text-[10px] font-bold uppercase tracking-wide text-[#5a4a2f]">
            Items matching “{dq}”
          </div>
          {(hits.data ?? []).slice(0, 8).map((it) => (
            <button
              key={it.id}
              onClick={() => nav(`/items/${it.id}`)}
              className="flex w-full items-center gap-2 border-b border-[#f3eee4] px-3 py-2.5 text-left hover:bg-accent-soft md:py-1.5"
            >
              <span className="truncate">{it.name}</span>
              {it.default_rate != null && (
                <span className="ml-auto font-mono text-[9px] text-muted">₹{it.default_rate}</span>
              )}
            </button>
          ))}
          {(hits.data?.length ?? 0) > 8 && (
            <div className="px-3 py-1.5 text-[10px] text-muted">
              {hits.data!.length - 8} more — switch to Flat to see them all.
            </div>
          )}
        </div>
      )}
      {cats.length === 0 && !(hits.data?.length) && (
        <div className="px-3 py-8 text-center text-xs text-muted">Nothing in the tree matches “{search}”.</div>
      )}

      {cats.map((c) => {
        const catKey = c.id ?? "__none__";
        const open = openCats.has(catKey) || !!q;
        const nGroups = c.groups.length;
        const nItems = c.groups.reduce((a, g) => a + g.leaf_count, 0) + c.loose_count;
        const looseKey = c.id ?? "__uncat__";
        const cNode = categoryNode(c.id, c.name, nItems);
        const lNode = looseNode(c.id, c.name, c.loose_count);
        const catDropKey = `drop:cat:${catKey}`;
        const catPicked = nodeKeys.has(cNode.key);
        return (
          <div key={catKey}>
            <div
              className={`flex items-center border-b border-[#f3eee4] bg-[#efe6d4]/50 text-[10px] font-bold uppercase tracking-wide text-[#5a4a2f]${dropCls(catDropKey)}`}
              {...dropOn(catDropKey, () => true)}
              onDrop={(e) => {
                e.preventDefault();
                setDropKey(null);
                const d = drag.current;
                drag.current = null;
                if (!d) return;
                if (d.kind === "group")
                  move.mutate({ path: `/item-groups/${d.id}`, body: { category_id: c.id } });
                else
                  move.mutate({
                    path: `/items/${d.id}`,
                    body: { group_id: null, category_id: c.id },
                  });
              }}
            >
              {writable && (
                <span className="pl-3">
                  <Check
                    label={`Select all of ${c.name}`}
                    checked={nodeKeys.has(cNode.key)}
                    onChange={() => onPickNode(cNode)}
                  />
                </span>
              )}
              <button
                className="flex min-w-0 flex-1 items-center gap-1.5 px-3 py-2.5 text-left uppercase md:py-1.5"
                aria-expanded={open}
                onClick={() => toggle(openCats, setOpenCats, catKey)}
              >
                <span className="text-[8px] text-faint">{open ? "▾" : "▸"}</span>
                <span className="truncate">{c.name}</span>
                <span className="ml-auto shrink-0 font-mono text-[9px] font-normal text-muted">
                  {nGroups} grp · {nItems}
                </span>
              </button>
              {writable && (
                <span className="pr-2 normal-case tracking-normal">
                  <NodeMenu
                    title={c.name}
                    count={nItems}
                    catalogModule={catalogModule}
                    onAction={(a) => onNodeAction(cNode, a)}
                  >
                    <NodeMenuEntry label="Manage categories…" onClick={() => nav("/items/categories")} />
                  </NodeMenu>
                </span>
              )}
            </div>
            {open && (
              <>
                {c.groups.map((g) => {
                  const gOpen = openGroups.has(g.id);
                  const gNode = groupNode(g.id, g.name, g.leaf_count);
                  const gQuery = `group_id=${g.id}`;
                  const gDropKey = `drop:grp:${g.id}`;
                  return (
                    <div key={g.id}>
                      <div
                        draggable={writable}
                        onDragStart={() => {
                          drag.current = { kind: "group", id: g.id };
                        }}
                        onDragEnd={() => {
                          drag.current = null;
                          setDropKey(null);
                        }}
                        {...dropOn(gDropKey, (d) => d.kind === "leaf")}
                        onDrop={(e) => {
                          e.preventDefault();
                          setDropKey(null);
                          const d = drag.current;
                          drag.current = null;
                          if (d?.kind === "leaf")
                            move.mutate({
                              path: `/items/${d.id}`,
                              body: { group_id: g.id, category_id: c.id },
                            });
                        }}
                        className={`flex items-center border-b border-[#f3eee4] pl-3 ${
                          g.id === selectedGroupId
                            ? "bg-card shadow-[inset_2px_0_0_theme(colors.accent.DEFAULT)]"
                            : "hover:bg-accent-soft"
                        }${dropCls(gDropKey)}`}
                      >
                        <button
                          className="-ml-1 grid h-9 w-7 shrink-0 place-items-center text-[8px] text-faint md:h-7"
                          aria-label={gOpen ? `Close ${g.name}` : `Open ${g.name}`}
                          aria-expanded={gOpen}
                          onClick={() => toggle(openGroups, setOpenGroups, g.id)}
                        >
                          {gOpen ? "▾" : "▸"}
                        </button>
                        {writable && (
                          <span className="mr-1.5">
                            <Check
                              label={`Select all of ${g.name}`}
                              checked={nodeKeys.has(gNode.key)}
                              lockedBy={catPicked ? c.name : undefined}
                              partial={leafPartial(gQuery)}
                              onChange={() => onPickNode(gNode)}
                            />
                          </span>
                        )}
                        <button
                          className="flex min-w-0 flex-1 items-center py-2.5 text-left md:py-1.5"
                          onClick={() => nav(`/items/g/${g.id}`)}
                        >
                          <span
                            className={`mr-1 shrink-0 rounded-sm px-1 py-0.5 text-[8px] font-bold uppercase ${
                              g.item_type === "bulk"
                                ? "bg-accent-soft text-accent"
                                : "bg-[#f1e7d6] text-warn"
                            }`}
                          >
                            {g.item_type}
                          </span>
                          <span className="truncate">{g.name}</span>
                        </button>
                        <span className="px-1.5 font-mono text-[9px] text-muted">{g.leaf_count}</span>
                        {writable && (
                          <span className="pr-2">
                            <NodeMenu
                              title={g.name}
                              count={g.leaf_count}
                              catalogModule={catalogModule}
                              onAction={(a) => onNodeAction(gNode, a)}
                            >
                              <NodeMenuEntry label="Open group page" onClick={() => nav(`/items/g/${g.id}`)} />
                            </NodeMenu>
                          </span>
                        )}
                      </div>
                      {gOpen && (
                        <LeafList
                          query={gQuery}
                          total={g.leaf_count}
                          lockedBy={
                            catPicked ? c.name : nodeKeys.has(gNode.key) ? g.name : undefined
                          }
                          pad="pl-10"
                          selectedItemId={selectedItemId}
                          useSizeLabel
                          writable={writable}
                          selectedIds={selectedIds}
                          onToggleLeaf={onToggleLeaf}
                          onDragLeaf={(id) => {
                            drag.current = { kind: "leaf", id };
                          }}
                          onDragEnd={() => {
                            drag.current = null;
                            setDropKey(null);
                          }}
                          onPick={(id) => nav(`/items/${id}`)}
                        />
                      )}
                    </div>
                  );
                })}
                {c.loose_count > 0 && (
                  <>
                    {c.groups.length > 0 && (
                    <div
                      className={`flex items-center border-b border-[#f3eee4] pl-6 text-[9px] uppercase tracking-wide text-muted${dropCls(`drop:loose:${looseKey}`)}`}
                      {...dropOn(`drop:loose:${looseKey}`, (d) => d.kind === "leaf")}
                      onDrop={(e) => {
                        e.preventDefault();
                        setDropKey(null);
                        const d = drag.current;
                        drag.current = null;
                        if (d?.kind === "leaf")
                          move.mutate({
                            path: `/items/${d.id}`,
                            body: { group_id: null, category_id: c.id },
                          });
                      }}
                    >
                      {writable && (
                        <span className="mr-1.5">
                          <Check
                            label={`Select all ungrouped in ${c.name}`}
                            checked={nodeKeys.has(lNode.key)}
                            lockedBy={catPicked ? c.name : undefined}
                            partial={leafPartial(
                              c.id ? `category_id=${c.id}` : "uncategorised=true",
                            )}
                            onChange={() => onPickNode(lNode)}
                          />
                        </span>
                      )}
                      <button
                        className="flex min-w-0 flex-1 items-center gap-1.5 py-2 text-left"
                        aria-expanded={!closedLoose.has(looseKey)}
                        onClick={() => toggle(closedLoose, setClosedLoose, looseKey)}
                      >
                        <span className="text-[8px] text-faint">
                          {closedLoose.has(looseKey) ? "▸" : "▾"}
                        </span>
                        Ungrouped
                        <span className="ml-auto pr-1.5 font-mono text-[9px]">{c.loose_count}</span>
                      </button>
                      {writable && (
                        <span className="pr-2 normal-case tracking-normal">
                          <NodeMenu
                            title={`${c.name} · ungrouped`}
                            count={c.loose_count}
                            catalogModule={catalogModule}
                            onAction={(a) => onNodeAction(lNode, a)}
                          />
                        </span>
                      )}
                    </div>
                    )}
                    {(c.groups.length === 0 || !closedLoose.has(looseKey)) && (
                      <LeafList
                        query={c.id ? `category_id=${c.id}` : "uncategorised=true"}
                        total={c.loose_count}
                        lockedBy={
                          catPicked ? c.name : nodeKeys.has(lNode.key) ? `${c.name} · ungrouped` : undefined
                        }
                        pad="pl-10"
                        selectedItemId={selectedItemId}
                        writable={writable}
                        selectedIds={selectedIds}
                        onToggleLeaf={onToggleLeaf}
                        onDragLeaf={(id) => {
                          drag.current = { kind: "leaf", id };
                        }}
                        onDragEnd={() => {
                          drag.current = null;
                          setDropKey(null);
                        }}
                        onPick={(id) => nav(`/items/${id}`)}
                      />
                    )}
                  </>
                )}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Leaves for one expanded node — fetched on first open, then cached. */
const LEAF_PAGE = 200;

function LeafList({
  query,
  total,
  lockedBy,
  pad,
  selectedItemId,
  useSizeLabel,
  writable,
  selectedIds,
  onToggleLeaf,
  onDragLeaf,
  onDragEnd,
  onPick,
}: {
  query: string;
  /** how many items the node holds, so a long one can offer "show more" */
  total: number;
  /** a parent category / group is ticked, so every leaf here is part of the selection */
  lockedBy?: string;
  pad: string;
  selectedItemId: string | null;
  useSizeLabel?: boolean;
  writable: boolean;
  selectedIds: Set<string>;
  onToggleLeaf: (id: string) => void;
  onDragLeaf: (id: string) => void;
  onDragEnd: () => void;
  onPick: (id: string) => void;
}) {
  const [limit, setLimit] = useState(LEAF_PAGE);
  const leaves = useQuery({
    queryKey: ["item-tree-leaves", query, limit],
    queryFn: () => api<TreeLeaf[]>(`/items/tree/leaves?${query}&limit=${limit}`),
    placeholderData: keepPreviousData,
  });

  if (leaves.isLoading)
    return <div className={`${pad} py-2 pr-3 text-[10px] text-muted`}>Loading…</div>;
  const rows = leaves.data ?? [];
  if (rows.length === 0)
    return <div className={`${pad} py-2 pr-3 text-[10px] text-faint`}>(empty)</div>;

  return (
    <>
      {rows.map((l) => {
        const tb = l.tally_state ? tallyBadge(l.tally_state) : null;
        const on = selectedIds.has(l.id) || !!lockedBy;
        return (
          <div
            key={l.id}
            draggable={writable}
            onDragStart={() => onDragLeaf(l.id)}
            onDragEnd={onDragEnd}
            className={`flex items-center border-b border-[#f3eee4] pr-3 ${
              on
                ? "bg-accent-soft"
                : l.id === selectedItemId
                  ? "bg-card shadow-[inset_2px_0_0_theme(colors.accent.DEFAULT)]"
                  : "hover:bg-accent-soft"
            }`}
          >
            {writable ? (
              <span className="pl-6">
                <Check
                  label={`Select ${l.name}`}
                  checked={on}
                  lockedBy={lockedBy}
                  onChange={() => onToggleLeaf(l.id)}
                />
              </span>
            ) : (
              <span className={pad.replace("pl-10", "pl-6")} />
            )}
            <button
              onClick={() => onPick(l.id)}
              className="flex min-w-0 flex-1 items-center gap-2 py-2.5 pl-2 text-left md:py-1.5"
            >
              {l.thumb_url && (
                <img
                  src={l.thumb_url}
                  alt=""
                  loading="lazy"
                  className="h-6 w-6 shrink-0 rounded-sm border border-line object-cover"
                />
              )}
              <span className="truncate">{useSizeLabel ? (l.size_label ?? l.name) : l.name}</span>
              {l.status === "unconfirmed" && (
                <span className="shrink-0 rounded-sm bg-[#f1e7d6] px-1 text-[8px] font-bold uppercase text-warn">
                  unconf
                </span>
              )}
              {l.availability && l.availability !== "in_stock" && (
                <span
                  className={`shrink-0 rounded-sm px-1 text-[8px] font-bold uppercase ${availabilityTone(l.availability)}`}
                >
                  {availabilityLabel(l.availability)}
                </span>
              )}
              {tb && l.tally_state !== "synced" && (
                <span
                  title={tb.title}
                  className={`shrink-0 rounded-sm px-1 text-[8px] font-bold uppercase ${tb.tone}`}
                >
                  {tb.label}
                </span>
              )}
              {l.default_rate != null && (
                <span className="ml-auto shrink-0 font-mono text-[9px] text-muted">₹{l.default_rate}</span>
              )}
            </button>
          </div>
        );
      })}
      {rows.length >= limit && total > rows.length && (
        <button
          className="block w-full border-b border-[#f3eee4] py-2.5 pl-10 pr-3 text-left text-[11px] text-accent hover:bg-accent-soft md:py-1.5"
          onClick={() => setLimit((n) => Math.min(n + LEAF_PAGE, 2000))}
        >
          Show {Math.min(LEAF_PAGE, total - rows.length)} more · {total - rows.length} not shown
          {limit >= 2000 ? " (open the Flat view to see them all)" : ""}
        </button>
      )}
    </>
  );
}
