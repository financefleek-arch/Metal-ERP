import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api, apiPage } from "../lib/api";
import { useDebounced } from "../lib/useDebounced";
import { useIsDesktop } from "../lib/useIsDesktop";
import type { Item, ItemListItem } from "../lib/types";

const PAGE_SIZE = 50;
import { ItemForm } from "../components/ItemForm";
import { NewItemForm } from "../components/NewItemForm";
import { ItemTree } from "../components/ItemTree";
import { GroupForm } from "../components/GroupForm";
import { CategoryManager } from "../components/CategoryManager";
import { SelectionBar } from "../components/bulk/SelectionBar";
import { BulkPanel, type BulkMode } from "../components/bulk/BulkPanel";

type Scope = "" | "bulk" | "mrp" | "unconfirmed" | "no_hsn" | "price_review" | "archived";
type View = "tree" | "flat";

const FILTERS: { key: Scope; label: string }[] = [
  { key: "", label: "All" },
  { key: "bulk", label: "⚖ BULK" },
  { key: "mrp", label: "📦 MRP" },
  { key: "unconfirmed", label: "Unconfirmed" },
  { key: "no_hsn", label: "No HSN" },
  { key: "price_review", label: "Price review" },
  { key: "archived", label: "Archived" },
];

function buildQuery(q: string, scope: Scope, cursor?: string | null) {
  const p = new URLSearchParams();
  if (q.trim()) p.set("q", q.trim());
  if (scope === "bulk" || scope === "mrp") p.set("type", scope);
  if (scope === "unconfirmed") p.set("status", "unconfirmed");
  if (scope === "archived") p.set("status", "archived");
  if (scope === "no_hsn") p.set("no_hsn", "true");
  if (scope === "price_review") p.set("price_review", "true");
  // Server caps a search result and doesn't page it; page only the browse list.
  if (!q.trim()) {
    p.set("limit", String(PAGE_SIZE));
    if (cursor) p.set("cursor", cursor);
  }
  return p.toString();
}

/** Bottom-of-list sentinel: auto-loads the next page when scrolled into view. */
function LoadMore({
  hasMore,
  loading,
  onLoad,
}: {
  hasMore: boolean;
  loading: boolean;
  onLoad: () => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || !hasMore) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && !loading) onLoad();
      },
      { rootMargin: "200px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [hasMore, loading, onLoad]);

  if (!hasMore) return null;
  return (
    <div ref={ref} className="px-3 py-3 text-center text-[11px] text-muted">
      {loading ? "Loading…" : "Scroll for more"}
    </div>
  );
}

export function ItemsPage() {
  const nav = useNavigate();
  const { id, groupId } = useParams();
  const { pathname } = useLocation();
  const isNew = pathname === "/items/new";
  const isCats = pathname === "/items/categories";
  const isBulk = pathname === "/items/bulk";
  const selectedId = isNew || isBulk || groupId ? null : (id ?? null);

  const [view, setView] = useState<View>("tree");
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 250);
  const [scope, setScope] = useState<Scope>("");
  const isDesktop = useIsDesktop();

  // --- bulk selection (flat view only) ---
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkMode, setBulkMode] = useState<BulkMode | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  const inDetail = isNew || isCats || isBulk || !!groupId || !!selectedId;
  const showDetailPane = isDesktop || inDetail;
  const showRailPane = isDesktop || !inDetail;

  const list = useInfiniteQuery({
    queryKey: ["items", dq, scope],
    queryFn: ({ pageParam }) =>
      apiPage<ItemListItem[]>(`/items?${buildQuery(dq, scope, pageParam)}`),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.nextCursor,
    enabled: view === "flat",
  });

  const detail = useQuery({
    queryKey: ["item", selectedId],
    queryFn: () => api<Item>(`/items/${selectedId}`),
    enabled: !!selectedId,
  });

  useEffect(() => {
    if (selectedId && detail.isError) nav("/items", { replace: true });
  }, [selectedId, detail.isError, nav]);

  // selection is ephemeral — drop it whenever the result set or view changes
  useEffect(() => {
    setSelected(new Set());
    setBulkMode(null);
  }, [dq, scope, view]);

  const rows = useMemo(
    () => list.data?.pages.flatMap((p) => p.data) ?? [],
    [list.data],
  );
  const selectedRows = useMemo(
    () => rows.filter((r) => selected.has(r.id)),
    [rows, selected],
  );
  const allLoadedSelected = rows.length > 0 && rows.every((r) => selected.has(r.id));

  function toggleRow(rid: string, e?: React.MouseEvent) {
    setFlash(null);
    const next = new Set(selected);
    if (e?.shiftKey && lastClicked != null) {
      const a = rows.findIndex((r) => r.id === lastClicked);
      const b = rows.findIndex((r) => r.id === rid);
      if (a !== -1 && b !== -1) {
        const [lo, hi] = a < b ? [a, b] : [b, a];
        for (let i = lo; i <= hi; i++) next.add(rows[i].id);
      }
    } else if (next.has(rid)) next.delete(rid);
    else next.add(rid);
    setLastClicked(rid);
    setSelected(next);
  }
  const [lastClicked, setLastClicked] = useState<string | null>(null);

  function openBulk(mode: BulkMode) {
    setBulkMode(mode);
    if (!isDesktop) nav("/items/bulk");
  }
  function closeBulk() {
    setBulkMode(null);
    if (isBulk) nav("/items");
  }
  function bulkDone(summary: string) {
    setBulkMode(null);
    setSelected(new Set());
    setFlash(summary);
    list.refetch();
    if (isBulk) nav("/items");
  }

  const bulkIds = useMemo(() => [...selected], [selected]);
  const showBulkInDetail = bulkMode != null && (isDesktop || isBulk);

  return (
    <div className="mx-auto flex min-h-[calc(100dvh-6.5rem)] max-w-5xl flex-col rounded-xl border border-line bg-card md:h-full md:min-h-0 md:flex-row md:overflow-hidden">
      {/* rail */}
      <div
        className={`${
          showRailPane ? "flex" : "hidden"
        } w-full shrink-0 flex-col border-b border-line bg-ground md:flex md:w-[320px] md:border-b-0 md:border-r`}
      >
        <div className="flex flex-col gap-2 border-b border-line p-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold">Items</span>
            <div className="flex gap-1.5">
              <button
                className="btn-ghost h-7 px-2.5 text-xs"
                title="Bulk import from a Tally stock-items XML"
                onClick={() => nav("/items/import")}
              >
                ⇧ Tally
              </button>
              <button className="btn-primary h-7 px-3 text-xs" onClick={() => nav("/items/new")}>
                + New
              </button>
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5 md:gap-1">
            {(["tree", "flat"] as const).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={`rounded-full border px-3 py-1 text-xs capitalize md:px-2.5 md:py-0.5 md:text-[10px] ${
                  view === v
                    ? "border-ink bg-ink text-ground"
                    : "border-line bg-card text-muted hover:bg-ground"
                }`}
              >
                {v}
              </button>
            ))}
            <button
              onClick={() => nav("/items/categories")}
              className="rounded-full border border-line bg-card px-3 py-1 text-xs text-muted hover:bg-ground md:px-2.5 md:py-0.5 md:text-[10px]"
            >
              Categories…
            </button>
          </div>
          {view === "flat" && (
            <>
              <input
                className="field h-8 text-xs"
                placeholder="search name, grade, size, HSN…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
              <div className="flex flex-wrap gap-1.5 md:gap-1">
                {FILTERS.map((f) => (
                  <button
                    key={f.key}
                    onClick={() => setScope(f.key)}
                    className={`rounded-full border px-3 py-1 text-xs md:px-2.5 md:py-0.5 md:text-[10px] ${
                      scope === f.key
                        ? "border-ink bg-ink text-ground"
                        : "border-line bg-card text-muted hover:bg-ground"
                    }`}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>

        {/* selection bar sits above the rows (or sticky-bottom on mobile) */}
        {view === "flat" && selected.size > 0 && (
          <div className="md:static md:order-none">
            <SelectionBar
              count={selected.size}
              totalAvailable={rows.length}
              onEditFields={() => openBulk("fields")}
              onMoveCategory={() => openBulk("category")}
              onDelete={() => openBulk("delete")}
              onSelectAll={() => setSelected(new Set(rows.map((r) => r.id)))}
              onClear={() => {
                setSelected(new Set());
                setBulkMode(null);
              }}
            />
          </div>
        )}

        <div className="flex-1 overflow-y-auto">
          {flash && view === "flat" && (
            <div className="border-b border-line bg-accent-soft px-3 py-2 text-[11px] font-medium text-accent-dark">
              ✓ {flash}
            </div>
          )}
          {isNew && (
            <div className="border-b border-[#f3eee4] bg-card px-3 py-2 shadow-[inset_2px_0_0_theme(colors.accent.DEFAULT)]">
              <div className="text-[11px] font-medium text-accent">New item — unsaved</div>
              <div className="text-[10px] text-muted">fill name to save</div>
            </div>
          )}

          {view === "tree" ? (
            <ItemTree selectedItemId={selectedId} selectedGroupId={groupId ?? null} />
          ) : (
            <>
              {list.isLoading && <div className="px-3 py-6 text-xs text-muted">Loading…</div>}
              {!list.isLoading && rows.length > 0 && (
                <label className="flex items-center gap-2 border-b border-line px-3 py-2 text-[11px] text-muted">
                  <input
                    type="checkbox"
                    className="h-4 w-4 accent-[color:theme(colors.accent.DEFAULT)]"
                    checked={allLoadedSelected}
                    onChange={(e) =>
                      setSelected(e.target.checked ? new Set(rows.map((r) => r.id)) : new Set())
                    }
                  />
                  Select all {rows.length} shown
                </label>
              )}
              {!list.isLoading && rows.length === 0 && !isNew && (
                <div className="px-3 py-8 text-center text-xs text-muted">
                  {dq || scope ? "No matches." : "No items yet."}
                </div>
              )}
              {rows.map((it) => {
                const isSel = selected.has(it.id);
                return (
                  <div
                    key={it.id}
                    className={`flex items-start gap-2 border-b border-[#f3eee4] px-3 py-3 md:py-2 ${
                      isSel
                        ? "bg-accent-soft"
                        : it.id === selectedId
                          ? "bg-card shadow-[inset_2px_0_0_theme(colors.accent.DEFAULT)]"
                          : "hover:bg-accent-soft"
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="mt-0.5 h-4 w-4 shrink-0 accent-[color:theme(colors.accent.DEFAULT)] md:opacity-60 md:hover:opacity-100"
                      checked={isSel}
                      onClick={(e) => toggleRow(it.id, e)}
                      onChange={() => {}}
                    />
                    <button
                      onClick={() => nav(`/items/${it.id}`)}
                      className="min-w-0 flex-1 text-left"
                    >
                      <div className="flex items-center gap-1.5 text-xs">
                        <span
                          className={`rounded-sm px-1 py-0.5 text-[8px] font-bold uppercase ${
                            it.item_type === "bulk"
                              ? "bg-accent-soft text-accent"
                              : "bg-[#f1e7d6] text-warn"
                          }`}
                        >
                          {it.item_type}
                        </span>
                        <span className="truncate font-medium">{it.name}</span>
                        {it.status === "unconfirmed" && (
                          <span className="ml-auto shrink-0 rounded-sm bg-[#f1e7d6] px-1 py-0.5 text-[8px] font-bold uppercase text-warn">
                            unconfirmed
                          </span>
                        )}
                        {it.status === "confirmed" && !it.hsn_code && (
                          <span className="ml-auto shrink-0 rounded-sm bg-[#efe9df] px-1 py-0.5 text-[8px] font-bold uppercase text-muted">
                            no HSN
                          </span>
                        )}
                      </div>
                      <div className="mt-0.5 text-[10px] text-muted">
                        {[it.shape, it.grade && `${it.metal ?? ""} ${it.grade}`.trim()]
                          .filter(Boolean)
                          .join(" · ")}
                        {it.default_rate != null &&
                          ` · ₹${it.default_rate}${it.uom ? `/${it.uom}` : ""}`}
                        {` · billed ${it.times_billed}×`}
                      </div>
                    </button>
                  </div>
                );
              })}
              {view === "flat" && !dq && (
                <LoadMore
                  hasMore={!!list.hasNextPage}
                  loading={list.isFetchingNextPage}
                  onLoad={() => list.fetchNextPage()}
                />
              )}
              {dq && rows.length >= PAGE_SIZE && (
                <div className="px-3 py-3 text-center text-[10px] text-muted">
                  Showing the closest {PAGE_SIZE} — add another word to narrow.
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* detail */}
      <div className={`${showDetailPane ? "flex" : "hidden"} min-w-0 flex-1 flex-col md:flex`}>
        {!isDesktop && inDetail && (
          <button
            className="flex items-center gap-2 border-b border-line px-4 py-3 text-sm font-medium text-accent md:hidden"
            onClick={() => (isBulk ? closeBulk() : nav("/items"))}
          >
            ← Items
          </button>
        )}
        <div className="flex-1 overflow-y-auto p-4 md:p-5">
          {showBulkInDetail && bulkMode ? (
            <BulkPanel
              mode={bulkMode}
              ids={bulkIds}
              items={selectedRows}
              onClose={closeBulk}
              onDone={bulkDone}
            />
          ) : isCats ? (
            <CategoryManager onClose={() => nav("/items")} />
          ) : groupId ? (
            <GroupForm key={groupId} groupId={groupId} />
          ) : isNew ? (
            <NewItemForm onCreated={(it) => nav(`/items/${it.id}`)} onCancel={() => nav("/items")} />
          ) : selected.size > 0 && isDesktop ? (
            <div className="grid h-full place-items-center px-6 text-center text-sm text-muted">
              {selected.size} item{selected.size === 1 ? "" : "s"} selected — choose an action in the
              bar on the left.
            </div>
          ) : !selectedId ? (
            <div className="grid h-full place-items-center text-sm text-muted">
              Select an item or group, or add a new one.
            </div>
          ) : detail.isLoading ? (
            <div className="grid h-full place-items-center text-sm text-muted">Loading…</div>
          ) : detail.data ? (
            <ItemForm
              key={detail.data.id}
              item={detail.data}
              onChanged={() => detail.refetch()}
              onDeleted={() => nav("/items")}
            />
          ) : (
            <div className="grid h-full place-items-center text-sm text-muted">Item not found.</div>
          )}
        </div>
      </div>
    </div>
  );
}
