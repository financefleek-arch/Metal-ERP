import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { TreeCategory, TreeLeaf } from "../lib/types";

/**
 * The catalogue tree: category → product group → leaf. `/items/tree` returns
 * only the skeleton (groups + counts); the leaves for a node are fetched
 * from `/items/tree/leaves` the first time it's expanded, so this stays
 * cheap at 10k items. Selecting a group navigates to /items/g/:id; a leaf to
 * /items/:id.
 */
export function ItemTree({
  selectedItemId,
  selectedGroupId,
}: {
  selectedItemId: string | null;
  selectedGroupId: string | null;
}) {
  const nav = useNavigate();
  const tree = useQuery({
    queryKey: ["item-tree"],
    queryFn: () => api<TreeCategory[]>("/items/tree"),
  });
  const [openCats, setOpenCats] = useState<Set<string>>(new Set());
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set());
  const [openLoose, setOpenLoose] = useState<Set<string>>(new Set());

  if (tree.isLoading) return <div className="px-3 py-6 text-xs text-muted">Loading…</div>;
  const cats = tree.data ?? [];
  if (cats.length === 0)
    return (
      <div className="px-3 py-8 text-center text-xs text-muted">
        No categories yet. Add one, then group your items.
      </div>
    );

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, key: string) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setter(next);
  };

  return (
    <div className="text-xs">
      {cats.map((c) => {
        const catKey = c.id ?? "__none__";
        const open = openCats.has(catKey);
        const nGroups = c.groups.length;
        const nItems = c.groups.reduce((a, g) => a + g.leaf_count, 0) + c.loose_count;
        const looseKey = c.id ?? "__uncat__";
        return (
          <div key={catKey}>
            <button
              className="flex w-full items-center gap-1.5 border-b border-[#f3eee4] bg-[#efe6d4]/50 px-3 py-2.5 text-left text-[10px] font-bold uppercase tracking-wide text-[#5a4a2f] md:py-1.5"
              onClick={() => toggle(openCats, setOpenCats, catKey)}
            >
              <span className="text-[8px] text-faint">{open ? "▾" : "▸"}</span>
              {c.name}
              <span className="ml-auto font-mono text-[9px] font-normal text-muted">
                {nGroups} grp · {nItems}
              </span>
            </button>
            {open && (
              <>
                {c.groups.map((g) => {
                  const gOpen = openGroups.has(g.id);
                  return (
                    <div key={g.id}>
                      <button
                        className={`flex w-full items-center gap-1.5 border-b border-[#f3eee4] py-2.5 pl-6 pr-3 text-left md:py-1.5 ${
                          g.id === selectedGroupId
                            ? "bg-card shadow-[inset_2px_0_0_theme(colors.accent.DEFAULT)]"
                            : "hover:bg-accent-soft"
                        }`}
                      >
                        <span
                          className="-m-2 p-2 text-[8px] text-faint"
                          onClick={(e) => {
                            e.stopPropagation();
                            toggle(openGroups, setOpenGroups, g.id);
                          }}
                        >
                          {gOpen ? "▾" : "▸"}
                        </span>
                        <span className="flex-1" onClick={() => nav(`/items/g/${g.id}`)}>
                          <span
                            className={`mr-1 rounded-sm px-1 py-0.5 text-[8px] font-bold uppercase ${
                              g.item_type === "bulk"
                                ? "bg-accent-soft text-accent"
                                : "bg-[#f1e7d6] text-warn"
                            }`}
                          >
                            {g.item_type}
                          </span>
                          {g.name}
                        </span>
                        <span className="font-mono text-[9px] text-muted">{g.leaf_count}</span>
                      </button>
                      {gOpen && (
                        <LeafList
                          query={`group_id=${g.id}`}
                          pad="pl-10"
                          selectedItemId={selectedItemId}
                          useSizeLabel
                          onPick={(id) => nav(`/items/${id}`)}
                        />
                      )}
                    </div>
                  );
                })}
                {c.loose_count > 0 && (
                  <>
                    <button
                      className="flex w-full items-center gap-1.5 border-b border-[#f3eee4] py-2 pl-6 pr-3 text-left text-[9px] uppercase tracking-wide text-muted"
                      onClick={() => toggle(openLoose, setOpenLoose, looseKey)}
                    >
                      <span className="text-[8px] text-faint">
                        {openLoose.has(looseKey) ? "▾" : "▸"}
                      </span>
                      Ungrouped
                      <span className="ml-auto font-mono text-[9px]">{c.loose_count}</span>
                    </button>
                    {openLoose.has(looseKey) && (
                      <LeafList
                        query={c.id ? `category_id=${c.id}` : "uncategorised=true"}
                        pad="pl-10"
                        selectedItemId={selectedItemId}
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
function LeafList({
  query,
  pad,
  selectedItemId,
  useSizeLabel,
  onPick,
}: {
  query: string;
  pad: string;
  selectedItemId: string | null;
  useSizeLabel?: boolean;
  onPick: (id: string) => void;
}) {
  const leaves = useQuery({
    queryKey: ["item-tree-leaves", query],
    queryFn: () => api<TreeLeaf[]>(`/items/tree/leaves?${query}`),
  });

  if (leaves.isLoading)
    return <div className={`${pad} py-2 pr-3 text-[10px] text-muted`}>Loading…</div>;
  const rows = leaves.data ?? [];
  if (rows.length === 0)
    return <div className={`${pad} py-2 pr-3 text-[10px] text-faint`}>(empty)</div>;

  return (
    <>
      {rows.map((l) => (
        <button
          key={l.id}
          onClick={() => onPick(l.id)}
          className={`flex w-full items-center gap-2 border-b border-[#f3eee4] py-2.5 ${pad} pr-3 text-left md:py-1.5 ${
            l.id === selectedItemId
              ? "bg-card shadow-[inset_2px_0_0_theme(colors.accent.DEFAULT)]"
              : "hover:bg-accent-soft"
          }`}
        >
          <span>{useSizeLabel ? (l.size_label ?? l.name) : l.name}</span>
          {l.status === "unconfirmed" && (
            <span className="rounded-sm bg-[#f1e7d6] px-1 text-[8px] font-bold uppercase text-warn">
              unconf
            </span>
          )}
          {l.default_rate != null && (
            <span className="ml-auto font-mono text-[9px] text-muted">₹{l.default_rate}</span>
          )}
        </button>
      ))}
    </>
  );
}
