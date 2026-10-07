import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api, apiPage, getToken } from "../lib/api";
import { useDebounced } from "../lib/useDebounced";
import { useIsDesktop } from "../lib/useIsDesktop";
import { uomDisplay } from "../lib/uom";
import { AVAILABILITY, availabilityLabel, availabilityTone, tallyBadge } from "../lib/items";
import type { Availability, Item, ItemFilter, ItemListItem } from "../lib/types";

const PAGE_SIZE = 50;
import { ItemForm } from "../components/ItemForm";
import { NewItemForm } from "../components/NewItemForm";
import { ItemTree } from "../components/ItemTree";
import { PhotoBulkDialog } from "../components/PhotoBulkDialog";
import { GroupForm } from "../components/GroupForm";
import { CategoryManager } from "../components/CategoryManager";
import { useAuth } from "../lib/auth";
import { ShareLinkDialog } from "../components/catalog/ShareLinkDialog";
import { PhotoQueueDialog } from "../components/PhotoQueueDialog";
import { SheetImportDialog } from "../components/SheetImportDialog";
import { TallyDialog } from "../components/catalog/TallyDialog";
import { LabelsDialog } from "../components/catalog/LabelsDialog";
import { MakeCatalogDialog } from "../components/catalog/MakeCatalogDialog";
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

function buildQuery(f: ItemFilter, cursor?: string | null) {
  const p = new URLSearchParams();
  if (f.q?.trim()) p.set("q", f.q.trim());
  if (f.type) p.set("type", f.type);
  if (f.status) p.set("status", f.status);
  if (f.no_hsn) p.set("no_hsn", "true");
  if (f.price_review) p.set("price_review", "true");
  for (const a of f.availability ?? []) p.append("availability", a);
  if (f.no_photo) p.set("no_photo", "true");
  if (f.in_tally != null) p.set("in_tally", String(f.in_tally));
  if (f.tally_price_due) p.set("tally_price_due", "true");
  if (f.catalog_id) p.set("catalog_id", f.catalog_id);
  if (f.supplier_id) p.set("supplier_id", f.supplier_id);
  // Server caps a search result and doesn't page it; page only the browse list.
  if (!f.q?.trim()) {
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
  const [params, setParams] = useSearchParams();
  const catalogId = params.get("catalog");

  const [view, setView] = useState<View>(
    catalogId || params.get("avail") ? "flat" : "tree",
  );
  const [avail, setAvail] = useState<Availability[]>(
    () => (params.get("avail")?.split(",").filter(Boolean) ?? []) as Availability[],
  );
  const [noPhoto, setNoPhoto] = useState(false);
  const [notInTally, setNotInTally] = useState(false);
  const [tallyDue, setTallyDue] = useState(false);
  const [allMatching, setAllMatching] = useState(false);
  const [photosOpen, setPhotosOpen] = useState(false);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [labelsOpen, setLabelsOpen] = useState(false);
  const [tallyOpen, setTallyOpen] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [queueOpen, setQueueOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [exportErr, setExportErr] = useState<string | null>(null);
  const { me } = useAuth();
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 250);
  const [scope, setScope] = useState<Scope>("");
  const isDesktop = useIsDesktop();

  // the one description of "which items" shared by the list, the counts and every bulk action
  const itemFilter: ItemFilter = useMemo(
    () => ({
      q: dq || undefined,
      type: scope === "bulk" || scope === "mrp" ? scope : undefined,
      status: scope === "unconfirmed" || scope === "archived" ? scope : undefined,
      no_hsn: scope === "no_hsn" || undefined,
      price_review: scope === "price_review" || undefined,
      availability: avail.length ? avail : undefined,
      no_photo: noPhoto || undefined,
      in_tally: notInTally ? false : undefined,
      tally_price_due: tallyDue || undefined,
      catalog_id: catalogId ?? undefined,
    }),
    [dq, scope, avail, noPhoto, notInTally, tallyDue, catalogId],
  );
  const filterKey = JSON.stringify(itemFilter);

  // --- bulk selection (flat view only) ---
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkMode, setBulkMode] = useState<BulkMode | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  const inDetail = isNew || isCats || isBulk || !!groupId || !!selectedId;
  const showDetailPane = isDesktop || inDetail;
  const showRailPane = isDesktop || !inDetail;

  const list = useInfiniteQuery({
    queryKey: ["items", filterKey],
    refetchOnWindowFocus: false, // every loaded page would refetch
    queryFn: ({ pageParam }) =>
      apiPage<ItemListItem[]>(`/items?${buildQuery(itemFilter, pageParam)}`),
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
    setAllMatching(false);
    setBulkMode(null);
  }, [filterKey, view]);

  // chip counts (availability, no photo, not in Tally), scoped to the price list when one is open
  const counts = useQuery({
    queryKey: ["item-counts", catalogId],
    enabled: view === "flat",
    queryFn: async () => {
      const base: ItemFilter = catalogId ? { catalog_id: catalogId } : {};
      const n = (f: ItemFilter) =>
        api<{ count: number }>("/items/count", { method: "POST", body: { ...base, ...f } }).then(
          (r) => r.count,
        );
      const [a, b, c, d, np, nt, td] = await Promise.all([
        n({ availability: ["in_stock"] }),
        n({ availability: ["expected"] }),
        n({ availability: ["out_of_stock"] }),
        n({ availability: ["discontinued"] }),
        n({ no_photo: true }),
        n({ in_tally: false }),
        n({ tally_price_due: true }),
      ]);
      return {
        in_stock: a,
        expected: b,
        out_of_stock: c,
        discontinued: d,
        no_photo: np,
        not_in_tally: nt,
        tally_due: td,
      };
    },
  });
  // how many items the current filter matches, for "select all N matching"
  const matching = useQuery({
    queryKey: ["item-matching", filterKey],
    enabled: view === "flat" && selected.size > 0,
    queryFn: () =>
      api<{ count: number }>("/items/count", { method: "POST", body: itemFilter }).then(
        (r) => r.count,
      ),
  });

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

  // Export what is ticked, else everything the current filters show, as an .xlsx download.
  async function exportSheet() {
    setExportErr(null);
    try {
      const t = getToken();
      const body =
        selected.size > 0 && !allMatching ? { ids: bulkIds } : { filter: view === "flat" ? itemFilter : {} };
      const res = await fetch("/api/item-sheet/export", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const d = (await res.json().catch(() => ({}))) as { detail?: unknown };
        throw new Error(typeof d.detail === "string" ? d.detail : "Could not export.");
      }
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = "items.xlsx";
      a.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
    } catch (e) {
      setExportErr(e instanceof Error ? e.message : "Could not export.");
    }
  }
  const selectedCount = allMatching ? (matching.data ?? selected.size) : selected.size;
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
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
            <span className="text-sm font-semibold">Items</span>
            <div className="flex flex-wrap gap-1.5 [&_a]:whitespace-nowrap [&_button]:whitespace-nowrap">
              <ImportMenu
                onSheet={() => setSheetOpen(true)}
                onExport={() => void exportSheet()}
              />
              <button
                className="btn-ghost h-7 px-2.5 text-xs"
                title="Add photos to many items at once"
                onClick={() => setPhotosOpen(true)}
              >
                Photos
              </button>
              <button
                className="btn-ghost h-7 px-2.5 text-xs"
                title="Go through the items that have no photo, one at a time"
                onClick={() => setQueueOpen(true)}
              >
                Photo queue
              </button>
              {me?.ext_supplier_catalog && (
                <Link to="/items/catalogs" className="btn-ghost h-7 px-2.5 text-xs">
                  Catalogs
                </Link>
              )}
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
              <div className="flex flex-wrap gap-1.5 md:gap-1" aria-label="Availability and photo filters">
                {AVAILABILITY.map((a) => {
                  const on = avail.includes(a.value);
                  const n = counts.data?.[a.value];
                  return (
                    <button
                      key={a.value}
                      aria-pressed={on}
                      onClick={() =>
                        setAvail((cur) =>
                          cur.includes(a.value) ? cur.filter((x) => x !== a.value) : [...cur, a.value],
                        )
                      }
                      className={`rounded-full border px-3 py-1 text-xs md:px-2.5 md:py-0.5 md:text-[10px] ${
                        on ? "border-accent bg-accent text-ground" : "border-line bg-card text-muted hover:bg-ground"
                      }`}
                    >
                      {a.label}
                      {n != null && <span className="ml-1 tabular-nums opacity-70">{n}</span>}
                    </button>
                  );
                })}
                <button
                  aria-pressed={noPhoto}
                  onClick={() => setNoPhoto((v) => !v)}
                  className={`rounded-full border px-3 py-1 text-xs md:px-2.5 md:py-0.5 md:text-[10px] ${
                    noPhoto ? "border-warn bg-warn text-ground" : "border-line bg-card text-muted hover:bg-ground"
                  }`}
                >
                  No photo
                  {counts.data && <span className="ml-1 tabular-nums opacity-70">{counts.data.no_photo}</span>}
                </button>
                <button
                  aria-pressed={notInTally}
                  onClick={() => setNotInTally((v) => !v)}
                  className={`rounded-full border px-3 py-1 text-xs md:px-2.5 md:py-0.5 md:text-[10px] ${
                    notInTally ? "border-ink bg-ink text-ground" : "border-line bg-card text-muted hover:bg-ground"
                  }`}
                >
                  Not in Tally
                  {counts.data && <span className="ml-1 tabular-nums opacity-70">{counts.data.not_in_tally}</span>}
                </button>
                <button
                  aria-pressed={tallyDue}
                  onClick={() => setTallyDue((v) => !v)}
                  title="In Tally, but its price there is not your current price"
                  className={`rounded-full border px-3 py-1 text-xs md:px-2.5 md:py-0.5 md:text-[10px] ${
                    tallyDue ? "border-warn bg-warn text-ground" : "border-line bg-card text-muted hover:bg-ground"
                  }`}
                >
                  Tally price old
                  {counts.data && <span className="ml-1 tabular-nums opacity-70">{counts.data.tally_due}</span>}
                </button>
                {catalogId && (
                  <button
                    className="rounded-full border border-accent bg-accent-soft px-3 py-1 text-xs text-accent md:px-2.5 md:py-0.5 md:text-[10px]"
                    title="Showing only the items from this price list. Click to show all."
                    onClick={() => {
                      const next = new URLSearchParams(params);
                      next.delete("catalog");
                      setParams(next, { replace: true });
                    }}
                  >
                    From a price list ✕
                  </button>
                )}
              </div>
            </>
          )}
        </div>

        {/* selection bar sits above the rows (or sticky-bottom on mobile) */}
        {view === "flat" && selected.size > 0 && (
          <div className="md:static md:order-none">
            <SelectionBar
              count={selected.size}
              matching={matching.data ?? null}
              allMatching={allMatching}
              onEditFields={() => openBulk("fields")}
              onRename={() => openBulk("rename")}
              onSetAvailability={() => openBulk("availability")}
              onMoveCategory={() => openBulk("category")}
              onDelete={() => openBulk("delete")}
              onShareLink={me?.ext_supplier_catalog ? () => setShareOpen(true) : undefined}
              onSendToTally={me?.ext_supplier_catalog ? () => setTallyOpen(true) : undefined}
              onPrintLabels={me?.ext_supplier_catalog ? () => setLabelsOpen(true) : undefined}
              onMakeCatalog={me?.ext_supplier_catalog ? () => setCatalogOpen(true) : undefined}
              onSelectAllMatching={() => {
                setSelected(new Set(rows.map((r) => r.id)));
                setAllMatching(true);
              }}
              onClear={() => {
                setSelected(new Set());
                setAllMatching(false);
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
                    {it.thumb_url && (
                      <img
                        src={it.thumb_url}
                        alt=""
                        loading="lazy"
                        className="h-9 w-9 shrink-0 rounded border border-line object-cover"
                      />
                    )}
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
                        {it.availability !== "in_stock" && (
                          <span
                            className={`shrink-0 rounded-sm px-1 py-0.5 text-[8px] font-bold uppercase ${availabilityTone(it.availability)}`}
                          >
                            {availabilityLabel(it.availability)}
                          </span>
                        )}
                        {(() => {
                          const tb = tallyBadge(it.tally_state);
                          return tb && it.tally_state !== "synced" ? (
                            <span
                              title={tb.title}
                              className={`shrink-0 rounded-sm px-1 py-0.5 text-[8px] font-bold uppercase ${tb.tone}`}
                            >
                              {tb.label}
                            </span>
                          ) : null;
                        })()}
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
                          ` · ₹${it.default_rate}${it.uom ? `/${uomDisplay(it.uom)}` : ""}`}
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
              filter={allMatching ? itemFilter : undefined}
              total={allMatching ? selectedCount : undefined}
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
      {photosOpen && <PhotoBulkDialog onClose={() => setPhotosOpen(false)} />}
      {shareOpen && (
        <ShareLinkDialog
          selection={allMatching ? { filter: itemFilter } : { ids: bulkIds }}
          count={selectedCount}
          onClose={() => setShareOpen(false)}
        />
      )}
      {queueOpen && <PhotoQueueDialog filter={itemFilter} onClose={() => setQueueOpen(false)} />}
      {sheetOpen && <SheetImportDialog onClose={() => setSheetOpen(false)} />}
      {exportErr && (
        <p className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded-md bg-danger px-3 py-2 text-xs text-ground" role="alert">
          {exportErr}
        </p>
      )}
      {tallyOpen && (
        <TallyDialog
          selection={allMatching ? { filter: itemFilter } : { ids: bulkIds }}
          count={selectedCount}
          onClose={() => {
            setTallyOpen(false);
            list.refetch();
          }}
        />
      )}
      {labelsOpen && (
        <LabelsDialog
          selection={allMatching ? { filter: itemFilter } : { ids: bulkIds }}
          count={selectedCount}
          onClose={() => {
            setLabelsOpen(false);
            list.refetch(); // items may have been given codes
          }}
        />
      )}
      {catalogOpen && (
        <MakeCatalogDialog
          selection={allMatching ? { filter: itemFilter } : { ids: bulkIds }}
          count={selectedCount}
          onClose={() => setCatalogOpen(false)}
        />
      )}
    </div>
  );
}

/** "Import items": the places items come from, in one menu. */
function ImportMenu({ onSheet, onExport }: { onSheet: () => void; onExport: () => void }) {
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const go = (to: string) => {
    setOpen(false);
    nav(to);
  };
  return (
    <div className="relative">
      <button
        className="btn-ghost h-7 px-2.5 text-xs"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
      >
        Import ▾
      </button>
      {open && (
        <div
          role="menu"
          className="absolute left-0 z-20 mt-1 w-max min-w-[220px] max-w-[min(20rem,calc(100vw-2rem))] overflow-hidden rounded-lg border border-line bg-card text-xs shadow-xl"
        >
          {[
            ["From a supplier price list", "/documents"],
            ["From a supplier bill", "/documents"],
            ["From Tally (stock items XML)", "/items/import"],
            ["Add one by hand", "/items/new"],
          ].map(([label, to]) => (
            <button
              key={to}
              role="menuitem"
              className="block w-full whitespace-nowrap px-3 py-2 text-left hover:bg-ground"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => go(to)}
            >
              {label}
            </button>
          ))}
          <div className="border-t border-line" />
          <button
            role="menuitem"
            className="block w-full whitespace-nowrap px-3 py-2 text-left hover:bg-ground"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              setOpen(false);
              onSheet();
            }}
          >
            Update from Excel…
          </button>
          <button
            role="menuitem"
            className="block w-full whitespace-nowrap px-3 py-2 text-left hover:bg-ground"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              setOpen(false);
              onExport();
            }}
          >
            Export to Excel
          </button>
        </div>
      )}
    </div>
  );
}
