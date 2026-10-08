import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api, apiPage, getToken } from "../lib/api";
import { useDebounced } from "../lib/useDebounced";
import { useIsDesktop } from "../lib/useIsDesktop";
import { uomDisplay } from "../lib/uom";
import { canWrite } from "../lib/roles";
import { AVAILABILITY, availabilityLabel, availabilityTone, tallyBadge } from "../lib/items";
import { groupNode, type NodeAction, type TreeNodeSel } from "../lib/itemNodes";
import type {
  Availability,
  GroupOut,
  Item,
  ItemCategoryRow,
  ItemFilter,
  ItemListItem,
} from "../lib/types";

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

type Scope = "bulk" | "mrp" | "unconfirmed" | "no_hsn" | "price_review" | "archived";
type View = "tree" | "flat";

const FILTERS: { key: Scope; label: string }[] = [
  { key: "bulk", label: "⚖ BULK" },
  { key: "mrp", label: "📦 MRP" },
  { key: "unconfirmed", label: "Unconfirmed" },
  { key: "no_hsn", label: "No HSN" },
  { key: "price_review", label: "Price review" },
  { key: "archived", label: "Archived" },
];
/** Chips that cannot both be on: BULK or MRP, Unconfirmed or Archived. The rest combine freely. */
const EXCLUSIVE: Scope[][] = [
  ["bulk", "mrp"],
  ["unconfirmed", "archived"],
];

const NO_CATEGORY = "__none__";
const NO_GROUP = "__loose__";

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
  if (f.group_id) p.set("group_id", f.group_id);
  if (f.category_id) p.set("category_id", f.category_id);
  if (f.uncategorised) p.set("uncategorised", "true");
  if (f.ungrouped) p.set("ungrouped", "true");
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
  const supplierId = params.get("supplier");

  const [view, setView] = useState<View>(
    catalogId || supplierId || params.get("avail") ? "flat" : "tree",
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
  const writable = canWrite(me?.role);
  const catalogModule = !!me?.ext_supplier_catalog;
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 250);
  const [scopes, setScopes] = useState<Set<Scope>>(new Set());
  const [catSel, setCatSel] = useState("");
  const [grpSel, setGrpSel] = useState("");
  const isDesktop = useIsDesktop();

  function toggleScope(k: Scope) {
    const next = new Set(scopes);
    if (next.has(k)) next.delete(k);
    else {
      for (const grp of EXCLUSIVE) if (grp.includes(k)) for (const o of grp) next.delete(o);
      next.add(k);
    }
    setScopes(next);
  }

  // the one description of "which items" shared by the list, the counts and every bulk action
  const itemFilter: ItemFilter = useMemo(
    () => ({
      q: dq || undefined,
      type: scopes.has("bulk") ? "bulk" : scopes.has("mrp") ? "mrp" : undefined,
      status: scopes.has("archived") ? "archived" : scopes.has("unconfirmed") ? "unconfirmed" : undefined,
      no_hsn: scopes.has("no_hsn") || undefined,
      price_review: scopes.has("price_review") || undefined,
      availability: avail.length ? avail : undefined,
      no_photo: noPhoto || undefined,
      in_tally: notInTally ? false : undefined,
      tally_price_due: tallyDue || undefined,
      catalog_id: catalogId ?? undefined,
      supplier_id: supplierId ?? undefined,
      group_id: grpSel && grpSel !== NO_GROUP ? grpSel : undefined,
      category_id: catSel && catSel !== NO_CATEGORY ? catSel : undefined,
      uncategorised: catSel === NO_CATEGORY || undefined,
      ungrouped: grpSel === NO_GROUP || undefined,
    }),
    [dq, scopes, avail, noPhoto, notInTally, tallyDue, catalogId, supplierId, catSel, grpSel],
  );
  const filterKey = JSON.stringify(itemFilter);

  // --- bulk selection: ticked rows (flat or tree), or one whole tree node ---
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [node, setNode] = useState<TreeNodeSel | null>(null);
  /** a node picked from a menu is only for that one action; a ticked node stays until cleared */
  const transientNode = useRef(false);
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

  // a new set of filters means a new list: drop what was ticked in the old one
  const viewRef = useRef(view);
  viewRef.current = view;
  useEffect(() => {
    if (viewRef.current !== "flat") return;
    setSelected(new Set());
    setAllMatching(false);
    setBulkMode(null);
  }, [filterKey]);
  // ticks and a picked tree node carry across Tree and Flat; only the "all matching" mode cannot
  useEffect(() => {
    setAllMatching(false);
  }, [view]);

  const categories = useQuery({
    queryKey: ["item-categories"],
    queryFn: () => api<ItemCategoryRow[]>("/item-categories"),
    enabled: view === "flat",
  });
  const groups = useQuery({
    queryKey: ["item-groups", catSel && catSel !== NO_CATEGORY ? catSel : ""],
    queryFn: () =>
      api<GroupOut[]>(
        `/item-groups${catSel && catSel !== NO_CATEGORY ? `?category_id=${catSel}` : ""}`,
      ),
    enabled: view === "flat",
  });

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

  const rows = useMemo(
    () => list.data?.pages.flatMap((p) => p.data) ?? [],
    [list.data],
  );
  const hasSelection = node != null || selected.size > 0;

  // how many items the current filter (or the picked tree node) matches
  const countFilter = node ? node.filter : itemFilter;
  const matching = useQuery({
    queryKey: ["item-matching", JSON.stringify(countFilter)],
    enabled: (view === "flat" && rows.length > 0) || node != null,
    queryFn: () =>
      api<{ count: number }>("/items/count", { method: "POST", body: countFilter }).then(
        (r) => r.count,
      ),
  });

  const selectedRows = useMemo(
    () => rows.filter((r) => selected.has(r.id)),
    [rows, selected],
  );
  const allLoadedSelected = rows.length > 0 && rows.every((r) => selected.has(r.id));

  function toggleRow(rid: string, e?: React.MouseEvent) {
    setFlash(null);
    setNode(null);
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

  /** a leaf ticked in the tree: ticks one item, and drops any whole-node selection */
  function toggleLeaf(rid: string) {
    setFlash(null);
    setNode(null);
    setAllMatching(false);
    const next = new Set(selected);
    if (next.has(rid)) next.delete(rid);
    else next.add(rid);
    setSelected(next);
  }
  function pickNode(n: TreeNodeSel | null) {
    setFlash(null);
    setSelected(new Set());
    setAllMatching(false);
    transientNode.current = false;
    setNode(n);
  }
  function clearSelection() {
    setSelected(new Set());
    setAllMatching(false);
    setBulkMode(null);
    setNode(null);
    transientNode.current = false;
  }
  /** a menu action on a node is over: forget the node it picked */
  function endNodeAction() {
    if (transientNode.current) {
      transientNode.current = false;
      setNode(null);
    }
  }

  function openBulk(mode: BulkMode) {
    setBulkMode(mode);
    if (!isDesktop) nav("/items/bulk");
  }
  function closeBulk() {
    setBulkMode(null);
    endNodeAction();
    if (isBulk) nav("/items");
  }
  function bulkDone(summary: string) {
    setBulkMode(null);
    setSelected(new Set());
    setAllMatching(false);
    setNode(null);
    transientNode.current = false;
    setFlash(summary);
    list.refetch();
    if (isBulk) nav("/items");
  }

  const bulkIds = useMemo(() => [...selected], [selected]);

  /** what the dialogs and bulk panel act on */
  const effFilter: ItemFilter | undefined = node ? node.filter : allMatching ? itemFilter : undefined;
  const selection = effFilter ? { filter: effFilter } : { ids: bulkIds };
  const selectedCount = node
    ? (matching.data ?? node.count)
    : allMatching
      ? (matching.data ?? selected.size)
      : selected.size;

  /** a menu on a tree row (or the group page): select that node for one action and start it */
  function nodeAction(n: TreeNodeSel, action: NodeAction) {
    setFlash(null);
    setSelected(new Set());
    setAllMatching(false);
    transientNode.current = true;
    setNode(n);
    switch (action) {
      case "catalog":
        return setCatalogOpen(true);
      case "share":
        return setShareOpen(true);
      case "labels":
        return setLabelsOpen(true);
      case "tally":
        return setTallyOpen(true);
      case "availability":
        return openBulk("availability");
      case "price":
        return openBulk("price");
      case "fields":
        return openBulk("fields");
      case "move":
        return openBulk("category");
      case "rename":
        return openBulk("rename");
      case "archive":
        return openBulk("archive");
      case "export":
        void exportSheet({ filter: n.filter });
        transientNode.current = false;
        return setNode(null);
    }
  }

  // Export what is ticked, else everything the current filters show, as an .xlsx download.
  async function exportSheet(override?: { filter: ItemFilter }) {
    setExportErr(null);
    try {
      const t = getToken();
      const body = override
        ? override
        : node
          ? { filter: node.filter }
          : selected.size > 0 && !allMatching
            ? { ids: bulkIds }
            : { filter: view === "flat" ? itemFilter : {} };
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
  const showBulkInDetail = bulkMode != null && (isDesktop || isBulk);
  const exportLabel = hasSelection
    ? `Export ${selectedCount}`
    : view === "flat" && matching.data != null
      ? `Export ${matching.data}`
      : "Export all";
  const exportTitle = hasSelection
    ? "Download the selected items as an Excel sheet"
    : view === "flat"
      ? "Download everything the filters below show as an Excel sheet"
      : "Download every item as an Excel sheet";

  const onClosedDialog = () => endNodeAction();

  return (
    <div className="mx-auto flex min-h-[calc(100dvh-6.5rem)] max-w-7xl flex-col rounded-xl border border-line bg-card md:h-full md:min-h-0 md:flex-row md:overflow-hidden">
      {/* rail */}
      <div
        className={`${
          showRailPane ? "flex" : "hidden"
        } w-full shrink-0 flex-col border-b border-line bg-ground md:flex md:w-[340px] md:border-b-0 md:border-r`}
      >
        <div className="flex flex-col gap-2 border-b border-line p-3">
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
            <span className="text-sm font-semibold">Items</span>
            <div className="flex flex-wrap items-center gap-1.5 [&_a]:whitespace-nowrap [&_button]:whitespace-nowrap">
              {writable && (
                <HeaderMenu
                  label="Import"
                  entries={[
                    { label: "From a supplier price list", onClick: () => nav("/documents") },
                    { label: "From a supplier bill", onClick: () => nav("/documents") },
                    { label: "From Tally (stock items XML)", onClick: () => nav("/items/import") },
                    { label: "Add one by hand", onClick: () => nav("/items/new") },
                    "sep",
                    { label: "Update from Excel…", onClick: () => setSheetOpen(true) },
                  ]}
                />
              )}
              <button
                className="btn-ghost h-7 px-2.5 text-xs"
                title={exportTitle}
                onClick={() => void exportSheet()}
              >
                {exportLabel}
              </button>
              {writable && (
                <HeaderMenu
                  label="Photos"
                  entries={[
                    { label: "Add photos to many items", onClick: () => setPhotosOpen(true) },
                    { label: "Photo queue (items with no photo)", onClick: () => setQueueOpen(true) },
                  ]}
                />
              )}
              {catalogModule && (
                <Link to="/items/catalogs" className="btn-ghost h-7 px-2.5 text-xs">
                  Catalogs
                </Link>
              )}
              {writable && (
                <button className="btn-primary h-7 px-3 text-xs" onClick={() => nav("/items/new")}>
                  + New
                </button>
              )}
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
              <div className="flex gap-1.5">
                <select
                  className="field h-8 min-w-0 flex-1 text-xs"
                  aria-label="Category"
                  value={catSel}
                  onChange={(e) => {
                    setCatSel(e.target.value);
                    setGrpSel("");
                  }}
                >
                  <option value="">All categories</option>
                  <option value={NO_CATEGORY}>Uncategorised</option>
                  {(categories.data ?? [])
                    .filter((c) => c.item_count > 0 || c.id === catSel)
                    .map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
                <select
                  className="field h-8 min-w-0 flex-1 text-xs"
                  aria-label="Group"
                  value={grpSel}
                  onChange={(e) => setGrpSel(e.target.value)}
                >
                  <option value="">All groups</option>
                  <option value={NO_GROUP}>Ungrouped</option>
                  {(groups.data ?? [])
                    .filter((g) => g.item_count > 0 || g.id === grpSel)
                    .map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex flex-wrap gap-1.5 md:gap-1">
                <button
                  onClick={() => setScopes(new Set())}
                  aria-pressed={scopes.size === 0}
                  className={`rounded-full border px-3 py-1 text-xs md:px-2.5 md:py-0.5 md:text-[10px] ${
                    scopes.size === 0
                      ? "border-ink bg-ink text-ground"
                      : "border-line bg-card text-muted hover:bg-ground"
                  }`}
                >
                  All
                </button>
                {FILTERS.map((f) => (
                  <button
                    key={f.key}
                    aria-pressed={scopes.has(f.key)}
                    onClick={() => toggleScope(f.key)}
                    className={`rounded-full border px-3 py-1 text-xs md:px-2.5 md:py-0.5 md:text-[10px] ${
                      scopes.has(f.key)
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
                  <LinkChip
                    label="From a price list"
                    title="Showing only the items from this price list. Click to show all."
                    onClear={() => {
                      const next = new URLSearchParams(params);
                      next.delete("catalog");
                      setParams(next, { replace: true });
                    }}
                  />
                )}
                {supplierId && (
                  <LinkChip
                    label="From one supplier"
                    title="Showing only the items that came from this supplier. Click to show all."
                    onClear={() => {
                      const next = new URLSearchParams(params);
                      next.delete("supplier");
                      setParams(next, { replace: true });
                    }}
                  />
                )}
              </div>
            </>
          )}
        </div>

        {/* selection bar sits above the rows (or sticky-bottom on mobile) */}
        {writable && hasSelection && (
          <div className="md:static md:order-none">
            <SelectionBar
              count={selectedCount}
              label={node?.label ?? null}
              matching={view === "flat" ? (matching.data ?? null) : null}
              allMatching={allMatching}
              showRestore={scopes.has("archived")}
              onEditFields={() => openBulk("fields")}
              onRename={() => openBulk("rename")}
              onSetAvailability={() => openBulk("availability")}
              onMoveCategory={() => openBulk("category")}
              onChangePrice={() => openBulk("price")}
              onConfirm={() => openBulk("confirm")}
              onArchive={() => openBulk("archive")}
              onRestore={() => openBulk("restore")}
              onDelete={() => openBulk("delete")}
              onShareLink={catalogModule ? () => setShareOpen(true) : undefined}
              onSendToTally={catalogModule ? () => setTallyOpen(true) : undefined}
              onPrintLabels={catalogModule ? () => setLabelsOpen(true) : undefined}
              onMakeCatalog={catalogModule ? () => setCatalogOpen(true) : undefined}
              onSelectAllMatching={() => {
                setSelected(new Set(rows.map((r) => r.id)));
                setAllMatching(true);
              }}
              onClear={clearSelection}
            />
          </div>
        )}

        <div className="flex-1 overflow-y-auto">
          {flash && (
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
            <ItemTree
              selectedItemId={selectedId}
              selectedGroupId={groupId ?? null}
              writable={writable}
              catalogModule={catalogModule}
              selectedIds={selected}
              onToggleLeaf={toggleLeaf}
              nodeKey={node && !transientNode.current ? node.key : null}
              onPickNode={pickNode}
              onNodeAction={nodeAction}
            />
          ) : (
            <>
              {list.isLoading && <div className="px-3 py-6 text-xs text-muted">Loading…</div>}
              {!list.isLoading && rows.length > 0 && writable && (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-3 py-2 text-[11px] text-muted">
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      className="h-4 w-4 accent-[color:theme(colors.accent.DEFAULT)]"
                      checked={allLoadedSelected}
                      onChange={(e) => {
                        setNode(null);
                        setSelected(e.target.checked ? new Set(rows.map((r) => r.id)) : new Set());
                        if (!e.target.checked) setAllMatching(false);
                      }}
                    />
                    Select all {rows.length} shown
                  </label>
                  {matching.data != null && matching.data > rows.length && !allMatching && (
                    <button
                      className="text-accent underline underline-offset-2"
                      onClick={() => {
                        setNode(null);
                        setSelected(new Set(rows.map((r) => r.id)));
                        setAllMatching(true);
                      }}
                    >
                      Select all {matching.data} matching
                    </button>
                  )}
                </div>
              )}
              {!list.isLoading && rows.length === 0 && !isNew && (
                <div className="px-3 py-8 text-center text-xs text-muted">
                  {dq || scopes.size || catSel || grpSel ? "No matches." : "No items yet."}
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
                    {writable && (
                      <input
                        type="checkbox"
                        className="mt-0.5 h-4 w-4 shrink-0 accent-[color:theme(colors.accent.DEFAULT)] md:opacity-60 md:hover:opacity-100"
                        aria-label={`Select ${it.name}`}
                        checked={isSel}
                        onClick={(e) => toggleRow(it.id, e)}
                        onChange={() => {}}
                      />
                    )}
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
                        {it.status === "archived" && (
                          <span className="ml-auto shrink-0 rounded-sm bg-[#efe9df] px-1 py-0.5 text-[8px] font-bold uppercase text-muted">
                            archived
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
              filter={effFilter}
              total={effFilter ? selectedCount : undefined}
              items={selectedRows}
              onClose={closeBulk}
              onDone={bulkDone}
            />
          ) : isCats ? (
            <CategoryManager onClose={() => nav("/items")} />
          ) : groupId ? (
            <GroupForm
              key={groupId}
              groupId={groupId}
              writable={writable}
              catalogModule={catalogModule}
              onAction={(g, a) => nodeAction(groupNode(g.id, g.name, g.item_count), a)}
            />
          ) : isNew ? (
            <NewItemForm onCreated={(it) => nav(`/items/${it.id}`)} onCancel={() => nav("/items")} />
          ) : hasSelection && isDesktop && !selectedId ? (
            <div className="grid h-full place-items-center px-6 text-center text-sm text-muted">
              {node
                ? `${node.label} — ${selectedCount} item${selectedCount === 1 ? "" : "s"} selected`
                : `${selectedCount} item${selectedCount === 1 ? "" : "s"} selected`}
              . Choose an action in the bar on the left.
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
              writable={writable}
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
          selection={selection}
          count={selectedCount}
          onClose={() => {
            setShareOpen(false);
            onClosedDialog();
          }}
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
          selection={selection}
          count={selectedCount}
          onClose={() => {
            setTallyOpen(false);
            onClosedDialog();
            list.refetch();
          }}
        />
      )}
      {labelsOpen && (
        <LabelsDialog
          selection={selection}
          count={selectedCount}
          onClose={() => {
            setLabelsOpen(false);
            onClosedDialog();
            list.refetch(); // items may have been given codes
          }}
        />
      )}
      {catalogOpen && (
        <MakeCatalogDialog
          selection={selection}
          count={selectedCount}
          onClose={() => {
            setCatalogOpen(false);
            onClosedDialog();
          }}
        />
      )}
    </div>
  );
}

/** A removable chip for a filter that arrives from another page (a price list, a supplier). */
function LinkChip({ label, title, onClear }: { label: string; title: string; onClear: () => void }) {
  return (
    <button
      className="rounded-full border border-accent bg-accent-soft px-3 py-1 text-xs text-accent md:px-2.5 md:py-0.5 md:text-[10px]"
      title={title}
      onClick={onClear}
    >
      {label} ✕
    </button>
  );
}

type MenuEntry = { label: string; onClick: () => void } | "sep";

/** A small header dropdown: "Import ▾", "Photos ▾". */
function HeaderMenu({ label, entries }: { label: string; entries: MenuEntry[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        className="btn-ghost h-7 px-2.5 text-xs"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
      >
        {label} ▾
      </button>
      {open && (
        <div
          role="menu"
          className="absolute left-0 z-20 mt-1 w-max min-w-[220px] max-w-[min(20rem,calc(100vw-2rem))] overflow-hidden rounded-lg border border-line bg-card text-xs shadow-xl"
        >
          {entries.map((e, i) =>
            e === "sep" ? (
              <div key={i} className="border-t border-line" />
            ) : (
              <button
                key={e.label}
                role="menuitem"
                className="block w-full whitespace-nowrap px-3 py-2 text-left hover:bg-ground"
                onMouseDown={(ev) => ev.preventDefault()}
                onClick={() => {
                  setOpen(false);
                  e.onClick();
                }}
              >
                {e.label}
              </button>
            ),
          )}
        </div>
      )}
    </div>
  );
}
