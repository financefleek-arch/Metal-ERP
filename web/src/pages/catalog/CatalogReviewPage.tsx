import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from "@tanstack/react-query";
import { api, ApiError, apiPage, type Page } from "../../lib/api";
import { useDebounced } from "../../lib/useDebounced";
import {
  itemsQuery,
  money,
  trimMargin,
  validMargin,
  type BulkChanges,
  type BulkTarget,
  type CatalogDetail,
  type CatalogItem,
  type CatalogListItem,
  type GroupSummary,
  type ItemFilter,
  type ItemPatch,
} from "../../lib/catalog";
import { Barcode } from "../../components/catalog/Barcode";
import { SupplierPicker } from "../../components/catalog/SupplierPicker";
import { CustomerCatalogDialog } from "../../components/catalog/CustomerCatalogDialog";
import { CustomerCatalogsPanel } from "../../components/catalog/CustomerCatalogsPanel";
import { EditableNumber, EditableText } from "../../components/catalog/Editable";
import { LabelsDialog } from "../../components/catalog/LabelsDialog";
import { TallyDialog } from "../../components/catalog/TallyDialog";
import { PromoteDialog } from "../../components/catalog/PromoteDialog";
import { ImageLightbox } from "../../components/catalog/ImageLightbox";
import { GroupsPanel } from "../../components/catalog/GroupsPanel";
import { PricingBar } from "../../components/catalog/PricingBar";

// Item fields a customer catalog prints; editing one makes existing versions out of date.
const CUSTOMER_FIELDS = [
  "display_name",
  "pack_qty",
  "cost_price",
  "included",
  "group_name",
  "item_margin_pct",
];

type IncludedFilter = "all" | "yes" | "no";
type MarginFilter = "any" | "item" | "bulk";
type ItemPages = InfiniteData<Page<CatalogItem[]>, string | null>;

// One column template for the header and every row, so columns line up. Below xl the row is
// checkbox + photo + name on top, with the detail fields stacked under the photo column.
const COLS =
  "xl:grid-cols-[28px_100px_minmax(0,1fr)_100px_112px_100px_84px_112px_170px_92px] xl:items-center xl:gap-x-3";
// Detail cells below xl start under the photo and span to the right edge.
const DETAIL = "col-span-2 col-start-2 xl:col-span-1 xl:col-start-auto";

export function CatalogReviewPage() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();

  const [q, setQ] = useState("");
  const dq = useDebounced(q, 250);
  const [groupSel, setGroupSel] = useState<string>("all"); // "all" | "none" | category id
  const [incl, setIncl] = useState<IncludedFilter>("all");
  const [marginSel, setMarginSel] = useState<MarginFilter>("any");
  const [showGroups, setShowGroups] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [allMatching, setAllMatching] = useState(false);
  const [bulkGroup, setBulkGroup] = useState("");
  const [bulkMargin, setBulkMargin] = useState("");
  const [flash, setFlash] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [labelsOpen, setLabelsOpen] = useState(false);
  const [tallyOpen, setTallyOpen] = useState(false);
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [zoomRow, setZoomRow] = useState<CatalogItem | null>(null);
  const [ccOpen, setCcOpen] = useState(false);
  const [pickSupplier, setPickSupplier] = useState(false);
  const [importNote, setImportNote] = useState<string | null>(null);

  const filter: ItemFilter = useMemo(
    () => ({
      q: dq.trim() || undefined,
      no_group: groupSel === "none" || undefined,
      category_id: groupSel !== "all" && groupSel !== "none" ? groupSel : undefined,
      included: incl === "all" ? undefined : incl === "yes",
      has_item_margin: marginSel === "any" ? undefined : marginSel === "item",
    }),
    [dq, groupSel, incl, marginSel],
  );

  const catalog = useQuery({
    queryKey: ["supplier-catalog", id],
    queryFn: () => api<CatalogDetail>(`/supplier-catalogs/${id}`),
  });
  const groups = useQuery({
    queryKey: ["catalog-groups", id],
    queryFn: () => api<GroupSummary[]>(`/supplier-catalogs/${id}/groups`),
  });
  const list = useInfiniteQuery({
    queryKey: ["catalog-items", id, filter],
    queryFn: ({ pageParam }) =>
      apiPage<CatalogItem[]>(`/supplier-catalogs/${id}/items?${itemsQuery(filter, pageParam)}`),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.nextCursor,
  });

  const rows = useMemo(() => list.data?.pages.flatMap((p) => p.data) ?? [], [list.data]);
  const total = list.data?.pages[0]?.total ?? null;

  // Selection is ephemeral: drop it whenever the result set changes.
  useEffect(() => {
    setSelected(new Set());
    setAllMatching(false);
  }, [filter, id]);

  // Load the next page when the bottom sentinel scrolls into view.
  const sentinel = useRef<HTMLDivElement>(null);
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = list;
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !hasNextPage) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting) && !isFetchingNextPage) void fetchNextPage();
      },
      { rootMargin: "600px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage, rows.length]);

  // the upload page leaves a one-time note about reused products and suggestions
  useEffect(() => {
    try {
      const key = `catalog-import-note:${id}`;
      const note = sessionStorage.getItem(key);
      if (note) {
        setImportNote(note);
        sessionStorage.removeItem(key);
      }
    } catch {
      /* storage can be blocked: the note is a convenience */
    }
  }, [id]);

  const onError = (e: unknown) =>
    setErr(e instanceof ApiError ? e.message : "That did not save. Try again.");

  function refreshGroups() {
    qc.invalidateQueries({ queryKey: ["catalog-groups", id] });
    qc.invalidateQueries({ queryKey: ["item-categories"] });
  }

  const patchItem = useMutation({
    mutationFn: (v: { itemId: string; body: ItemPatch }) =>
      api<CatalogItem>(`/supplier-catalogs/${id}/items/${v.itemId}`, {
        method: "PATCH",
        body: v.body,
      }),
    onSuccess: (updated, v) => {
      setErr(null);
      qc.setQueriesData<ItemPages>({ queryKey: ["catalog-items", id] }, (old) =>
        old
          ? {
              ...old,
              pages: old.pages.map((p) => ({
                ...p,
                data: p.data.map((r) => (r.id === updated.id ? updated : r)),
              })),
            }
          : old,
      );
      if ("group_name" in v.body || "included" in v.body) refreshGroups();
      if ("item_margin_pct" in v.body)
        qc.invalidateQueries({ queryKey: ["supplier-catalog", id] });
      // any edit that changes what a customer catalog prints makes it out of date
      if (CUSTOMER_FIELDS.some((f) => f in v.body))
        qc.invalidateQueries({ queryKey: ["customer-catalogs", id] });
    },
    onError,
  });

  const bulk = useMutation({
    mutationFn: (v: { target: BulkTarget; changes: BulkChanges }) =>
      api<{ updated: number }>(`/supplier-catalogs/${id}/items/bulk`, {
        method: "PATCH",
        body: { ...v.target, changes: v.changes },
      }),
    onSuccess: (res) => {
      setErr(null);
      setFlash(`Updated ${res.updated} item${res.updated === 1 ? "" : "s"}.`);
      window.setTimeout(() => setFlash(null), 2500);
      setSelected(new Set());
      setAllMatching(false);
      setBulkGroup("");
      setBulkMargin("");
      qc.invalidateQueries({ queryKey: ["catalog-items", id] });
      qc.invalidateQueries({ queryKey: ["supplier-catalog", id] });
      qc.invalidateQueries({ queryKey: ["customer-catalogs", id] });
      refreshGroups();
    },
    onError,
  });

  function replaceRow(updated: CatalogItem) {
    qc.setQueriesData<ItemPages>({ queryKey: ["catalog-items", id] }, (old) =>
      old
        ? {
            ...old,
            pages: old.pages.map((p) => ({
              ...p,
              data: p.data.map((r) => (r.id === updated.id ? updated : r)),
            })),
          }
        : old,
    );
  }

  const linkProduct = useMutation({
    mutationFn: (v: { itemId: string; productId: string }) =>
      api<CatalogItem>(`/supplier-catalogs/${id}/items/${v.itemId}/link-product`, {
        method: "POST",
        body: { product_id: v.productId },
      }),
    onSuccess: (updated) => {
      setErr(null);
      replaceRow(updated);
      refreshGroups();
      qc.invalidateQueries({ queryKey: ["customer-catalogs", id] });
    },
    onError,
  });

  const dismissSuggestion = useMutation({
    mutationFn: (itemId: string) =>
      api<CatalogItem>(`/supplier-catalogs/${id}/items/${itemId}/suggestion`, {
        method: "DELETE",
      }),
    onSuccess: (updated) => replaceRow(updated),
    onError,
  });

  const setSupplier = useMutation({
    mutationFn: (partyId: string) =>
      api<CatalogListItem>(`/supplier-catalogs/${id}`, {
        method: "PATCH",
        body: { supplier_party_id: partyId },
      }),
    onSuccess: (c) => {
      setErr(null);
      setPickSupplier(false);
      qc.setQueryData(["supplier-catalog", id], (old: unknown) => ({ ...(old as object), ...c }));
      qc.invalidateQueries({ queryKey: ["supplier-catalogs"] });
    },
    onError,
  });

  const rename = useMutation({
    mutationFn: (title: string) =>
      api<CatalogListItem>(`/supplier-catalogs/${id}`, { method: "PATCH", body: { title } }),
    onSuccess: (c) => {
      qc.setQueryData(["supplier-catalog", id], c);
      qc.invalidateQueries({ queryKey: ["supplier-catalogs"] });
    },
    onError,
  });

  const remove = useMutation({
    mutationFn: () => api(`/supplier-catalogs/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["supplier-catalogs"] });
      nav("/catalogs");
    },
    onError: (e) => {
      setConfirmDelete(false);
      onError(e);
    },
  });

  // --- selection helpers ---
  const allLoadedSelected = rows.length > 0 && rows.every((r) => selected.has(r.id));
  const selectedCount = allMatching ? (total ?? selected.size) : selected.size;
  const target = (): BulkTarget => (allMatching ? { filter } : { ids: [...selected] });

  function toggleAll() {
    setAllMatching(false);
    setSelected(allLoadedSelected ? new Set() : new Set(rows.map((r) => r.id)));
  }
  function toggleRow(rowId: string) {
    setAllMatching(false);
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(rowId)) n.delete(rowId);
      else n.add(rowId);
      return n;
    });
  }

  if (catalog.isError) {
    return (
      <div className="max-w-xl">
        <p className="err">That catalog was not found.</p>
        <Link to="/catalogs" className="text-sm text-accent hover:underline">
          Back to catalogs
        </Link>
      </div>
    );
  }

  const cat = catalog.data;
  const groupList = groups.data ?? [];
  const noGroupCount = groupList.find((g) => g.category_id === null)?.item_count ?? 0;
  const filtered = !!(
    filter.q ||
    filter.no_group ||
    filter.category_id ||
    incl !== "all" ||
    marginSel !== "any"
  );

  return (
    <div>
      <div className="mb-1 text-sm">
        <Link to="/catalogs" className="text-accent hover:underline">
          Supplier catalogs
        </Link>
      </div>

      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0 flex-1">
          {cat ? (
            <EditableText
              label="Catalog name"
              value={cat.title}
              onSave={(t) => t && rename.mutate(t)}
              className="font-serif text-2xl font-semibold"
            />
          ) : (
            <div className="h-8 w-64 animate-pulse rounded bg-line" />
          )}
          {cat && (
            <p className="px-1.5 text-sm text-muted">
              {cat.item_count} items · {cat.page_count} pages · {cat.source_filename} ·{" "}
              {cat.supplier_name ? (
                <>
                  from{" "}
                  {cat.supplier_party_id ? (
                    <Link to={`/parties/${cat.supplier_party_id}`} className="text-accent hover:underline">
                      {cat.supplier_name}
                    </Link>
                  ) : (
                    cat.supplier_name
                  )}
                </>
              ) : (
                <button
                  type="button"
                  className="text-accent hover:underline"
                  onClick={() => setPickSupplier((v) => !v)}
                >
                  Set supplier
                </button>
              )}
            </p>
          )}
          {cat && !cat.supplier_name && pickSupplier && (
            <div className="mt-1 max-w-sm px-1.5">
              <SupplierPicker
                value={null}
                onPick={(s) => s && setSupplier.mutate(s.id)}
                disabled={setSupplier.isPending}
              />
              <p className="mt-1 text-xs text-muted">
                With a supplier, a later catalog from them reuses these codes.
              </p>
            </div>
          )}
          {importNote && (
            <p className="mt-1 px-1.5 text-sm text-ok" role="status">
              {importNote}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="btn-ghost"
            aria-expanded={showGroups}
            onClick={() => setShowGroups((v) => !v)}
          >
            Manage groups
          </button>
          <button type="button" className="btn-ghost" onClick={() => setLabelsOpen(true)}>
            Print labels
          </button>
          <button type="button" className="btn-ghost" onClick={() => setPromoteOpen(true)}>
            Add to items
          </button>
          <button type="button" className="btn-ghost" onClick={() => setTallyOpen(true)}>
            Send to Tally
          </button>
          {confirmDelete ? (
            <span className="flex items-center gap-2 text-sm">
              Delete this catalog?
              <button
                type="button"
                className="btn-primary bg-danger hover:bg-danger"
                disabled={remove.isPending}
                onClick={() => remove.mutate()}
              >
                Delete
              </button>
              <button type="button" className="btn-ghost" onClick={() => setConfirmDelete(false)}>
                Keep
              </button>
            </span>
          ) : (
            <button type="button" className="btn-ghost" onClick={() => setConfirmDelete(true)}>
              Delete
            </button>
          )}
        </div>
      </div>

      {err && (
        <p className="err mb-3" role="alert">
          {err}
        </p>
      )}
      {showGroups && <GroupsPanel catalogId={id} groups={groupList} />}
      {cat && (
        <PricingBar
          catalog={cat}
          resetting={bulk.isPending}
          onResetItemMargins={() =>
            bulk.mutate({
              target: { filter: { has_item_margin: true } },
              changes: { item_margin_pct: null },
            })
          }
        />
      )}

      <CustomerCatalogsPanel catalogId={id} onCreate={() => setCcOpen(true)} />

      {/* filters */}
      <div className="mb-3 flex flex-wrap items-end gap-3">
        <div className="min-w-[14rem] flex-1">
          <label className="label" htmlFor="cat-search">
            Search
          </label>
          <input
            id="cat-search"
            className="field"
            placeholder="Name, code or brand"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="cat-group">
            Group
          </label>
          <select
            id="cat-group"
            className="field w-56"
            value={groupSel}
            onChange={(e) => setGroupSel(e.target.value)}
          >
            <option value="all">All groups</option>
            {noGroupCount > 0 && <option value="none">No group ({noGroupCount})</option>}
            {groupList
              .filter((g) => g.category_id !== null)
              .map((g) => (
                <option key={g.category_id} value={g.category_id!}>
                  {g.name} ({g.item_count})
                </option>
              ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="cat-incl">
            Show
          </label>
          <select
            id="cat-incl"
            className="field w-40"
            value={incl}
            onChange={(e) => setIncl(e.target.value as IncludedFilter)}
          >
            <option value="all">All items</option>
            <option value="yes">Included</option>
            <option value="no">Excluded</option>
          </select>
        </div>
        <div>
          <label className="label" htmlFor="cat-margin">
            Margin
          </label>
          <select
            id="cat-margin"
            className="field w-48"
            value={marginSel}
            onChange={(e) => setMarginSel(e.target.value as MarginFilter)}
          >
            <option value="any">Any</option>
            <option value="item">Item-level margin</option>
            <option value="bulk">Bulk margin</option>
          </select>
        </div>
      </div>

      {/* bulk bar */}
      {selectedCount > 0 && (
        <div
          className="card sticky top-0 z-10 mb-3 flex flex-wrap items-center gap-3 border-accent bg-accent-soft p-3"
          role="region"
          aria-label="Bulk actions"
        >
          <span className="text-sm font-medium">
            {allMatching ? `All ${selectedCount} matching items` : `${selectedCount} selected`}
          </span>
          {!allMatching && allLoadedSelected && total !== null && total > selected.size && (
            <button
              type="button"
              className="text-sm text-accent underline"
              onClick={() => setAllMatching(true)}
            >
              Select all {total} matching
            </button>
          )}
          <span className="flex items-center gap-2">
            <label className="sr-only" htmlFor="bulk-group">
              Set group
            </label>
            <input
              id="bulk-group"
              className="field h-9 w-48"
              list="catalog-groups-list"
              placeholder="Set group…"
              value={bulkGroup}
              onChange={(e) => setBulkGroup(e.target.value)}
            />
            <button
              type="button"
              className="btn-primary h-9"
              disabled={!bulkGroup.trim() || bulk.isPending}
              onClick={() =>
                bulk.mutate({ target: target(), changes: { group_name: bulkGroup.trim() } })
              }
            >
              Apply
            </button>
          </span>
          <span className="flex items-center gap-2">
            <label className="sr-only" htmlFor="bulk-margin">
              Set item margin
            </label>
            <input
              id="bulk-margin"
              className="field h-9 w-32 text-right font-mono"
              inputMode="decimal"
              placeholder="Margin %…"
              aria-invalid={bulkMargin.trim() !== "" && !validMargin(bulkMargin)}
              value={bulkMargin}
              onChange={(e) => setBulkMargin(e.target.value)}
            />
            <button
              type="button"
              className="btn-primary h-9"
              disabled={!validMargin(bulkMargin) || bulk.isPending}
              onClick={() =>
                bulk.mutate({
                  target: target(),
                  changes: { item_margin_pct: Number(bulkMargin).toFixed(2) },
                })
              }
            >
              Set margin
            </button>
          </span>
          <button
            type="button"
            className="btn-ghost h-9"
            disabled={bulk.isPending}
            onClick={() => bulk.mutate({ target: target(), changes: { item_margin_pct: null } })}
          >
            Clear margin
          </button>
          <button
            type="button"
            className="btn-ghost h-9"
            disabled={bulk.isPending}
            onClick={() => bulk.mutate({ target: target(), changes: { included: false } })}
          >
            Exclude
          </button>
          <button
            type="button"
            className="btn-ghost h-9"
            disabled={bulk.isPending}
            onClick={() => bulk.mutate({ target: target(), changes: { included: true } })}
          >
            Include
          </button>
          <button
            type="button"
            className="btn-ghost h-9"
            disabled={bulk.isPending}
            onClick={() => bulk.mutate({ target: target(), changes: { group_name: null } })}
          >
            Clear group
          </button>
          <button type="button" className="btn-ghost h-9" onClick={() => setLabelsOpen(true)}>
            Print labels
          </button>
          <button type="button" className="btn-ghost h-9" onClick={() => setCcOpen(true)}>
            Customer catalog
          </button>
          <button type="button" className="btn-ghost h-9" onClick={() => setPromoteOpen(true)}>
            Add to items
          </button>
          <button type="button" className="btn-ghost h-9" onClick={() => setTallyOpen(true)}>
            Send to Tally
          </button>
          <button
            type="button"
            className="ml-auto text-sm text-muted underline"
            onClick={() => {
              setSelected(new Set());
              setAllMatching(false);
            }}
          >
            Clear selection
          </button>
        </div>
      )}
      {flash && (
        <p className="mb-2 text-sm text-ok" role="status">
          {flash}
        </p>
      )}

      <datalist id="catalog-groups-list">
        {groupList
          .filter((g) => g.category_id !== null)
          .map((g) => (
            <option key={g.category_id} value={g.name} />
          ))}
      </datalist>

      {/* table */}
      <div className="card overflow-hidden">
        <div
          className={`hidden border-b border-line bg-ground px-3 py-2 text-[11px] font-medium uppercase tracking-[0.06em] text-muted xl:grid ${COLS}`}
        >
          <input
            type="checkbox"
            aria-label="Select all loaded items"
            checked={allLoadedSelected}
            onChange={toggleAll}
          />
          <span>Image</span>
          <span>Item name</span>
          <span>Item code</span>
          <span>Unit</span>
          <span className="text-right">Supplier price</span>
          <span className="text-right">Item margin</span>
          <span className="text-right">New price</span>
          <span>Group</span>
          <span>Status</span>
        </div>

        {list.isLoading && <p className="p-4 text-sm text-muted">Loading items…</p>}
        {list.isError && <p className="err p-4">Could not load items. Try again.</p>}
        {!list.isLoading && rows.length === 0 && (
          <p className="p-4 text-sm text-muted">
            {filtered ? "No items match these filters." : "This catalog has no items."}
          </p>
        )}

        {rows.length > 0 && (
          <label className="flex items-center gap-3 border-b border-line bg-ground px-3 py-2.5 text-sm xl:hidden">
            <input
              type="checkbox"
              checked={allLoadedSelected}
              onChange={toggleAll}
              aria-label="Select all loaded items"
            />
            <span className="font-medium">Select all {rows.length} shown</span>
            {total !== null && total > rows.length && (
              <span className="text-xs text-muted">of {total} matching</span>
            )}
          </label>
        )}

        <ul>
          {rows.map((r) => (
            <li
              key={r.id}
              className={`grid grid-cols-[24px_102px_minmax(0,1fr)] gap-x-3 gap-y-2 border-b border-line px-3 py-3 last:border-0 ${COLS} ${
                r.included ? "" : "bg-ground/60"
              }`}
            >
              <input
                type="checkbox"
                aria-label={`Select ${r.display_name}`}
                checked={selected.has(r.id) || allMatching}
                onChange={() => toggleRow(r.id)}
              />
              {r.image_url ? (
                <button
                  type="button"
                  aria-label={`View photo of ${r.display_name}`}
                  className="h-[68px] w-[102px] shrink-0 cursor-zoom-in rounded"
                  onClick={() => setZoomRow(r)}
                >
                  <img
                    src={r.image_url}
                    alt={r.display_name}
                    loading="lazy"
                    className={`h-[68px] w-[102px] rounded border border-line object-cover ${
                      r.included ? "" : "opacity-50"
                    }`}
                  />
                </button>
              ) : (
                <div className="h-[68px] w-[102px] rounded border border-line bg-ground" />
              )}
              <div className="min-w-0">
                <EditableText
                  label={`Name of ${r.code}`}
                  value={r.display_name}
                  onSave={(v) => v && patchItem.mutate({ itemId: r.id, body: { display_name: v } })}
                  className="font-medium"
                />
                <p className="truncate px-1.5 text-[11px] text-muted">
                  supplier ref {r.supplier_code}
                  {r.brand ? ` · ${r.brand}` : ""}
                  {r.size_text ? ` · ${r.size_text}` : ""}
                  {r.carton_qty ? ` · carton of ${r.carton_qty}` : ""}
                  {r.tally_status === "synced" ? (
                    <span className="text-ok"> · in Tally</span>
                  ) : r.tally_status === "error" ? (
                    <span className="text-danger"> · Tally rejected it</span>
                  ) : r.item_id ? (
                    <span> · in your items</span>
                  ) : null}
                </p>
              </div>
              <div className={`min-w-0 px-1.5 ${DETAIL}`}>
                <span className="font-mono text-xs">
                  {r.code}
                  <span
                    className="ml-1 text-[10px] text-muted"
                    title={
                      r.code_locked
                        ? "This code has been used (label, catalog or Tally). It will not change."
                        : "Not used yet. It changes if you move the item to another group."
                    }
                  >
                    {r.code_locked ? "🔒" : "draft"}
                  </span>
                </span>
                {r.suggestion && (
                  <div className="mt-1 rounded border border-accent/40 bg-accent-soft px-1.5 py-1 text-[11px]">
                    <span>
                      Same as <b className="font-mono">{r.suggestion.code}</b>?
                    </span>
                    <span className="ml-2 inline-flex gap-2">
                      <button
                        type="button"
                        className="text-accent hover:underline"
                        disabled={linkProduct.isPending}
                        onClick={() =>
                          linkProduct.mutate({ itemId: r.id, productId: r.suggestion!.product_id })
                        }
                      >
                        Use it
                      </button>
                      <button
                        type="button"
                        className="text-muted hover:underline"
                        disabled={dismissSuggestion.isPending}
                        onClick={() => dismissSuggestion.mutate(r.id)}
                      >
                        Not the same
                      </button>
                    </span>
                  </div>
                )}
                {r.barcode && (
                  <Barcode
                    pattern={r.barcode}
                    label={`Barcode ${r.code}`}
                    className="mt-1 block h-4 w-24"
                  />
                )}
              </div>
              <div className={`flex items-center gap-1.5 ${DETAIL}`}>
                <span className="whitespace-nowrap pl-1.5 text-xs text-muted">Pack of</span>
                <EditableNumber
                  kind="int"
                  label={`Pack quantity of ${r.code}`}
                  value={String(r.pack_qty)}
                  onSave={(v) => patchItem.mutate({ itemId: r.id, body: { pack_qty: Number(v) } })}
                  className="w-12 text-center"
                />
              </div>
              <div className={`flex items-center gap-1 xl:justify-end ${DETAIL}`}>
                <span className="whitespace-nowrap pl-1.5 text-xs text-muted xl:hidden">
                  Supplier price
                </span>
                <span className="pl-1.5 text-xs text-muted xl:pl-0">₹</span>
                <EditableNumber
                  kind="money"
                  label={`Supplier price of ${r.code}`}
                  value={r.cost_price}
                  onSave={(v) => patchItem.mutate({ itemId: r.id, body: { cost_price: v } })}
                  className="w-24"
                />
              </div>
              <div className={`flex items-center gap-1.5 xl:justify-end ${DETAIL}`}>
                <span className="whitespace-nowrap pl-1.5 text-xs text-muted xl:hidden">
                  Item margin
                </span>
                <EditableNumber
                  kind="margin"
                  allowEmpty
                  placeholder="—"
                  label={`Item margin of ${r.code}`}
                  value={r.item_margin_pct !== null ? trimMargin(r.item_margin_pct) : ""}
                  onSave={(v) =>
                    patchItem.mutate({
                      itemId: r.id,
                      body: { item_margin_pct: v === "" ? null : Number(v).toFixed(2) },
                    })
                  }
                  className={`w-16 ${r.item_margin_pct !== null ? "!bg-[#f1e7d6] border-line font-medium" : ""}`}
                />
                <span className="text-xs text-muted" aria-hidden>
                  %
                </span>
              </div>
              <div className={`flex items-center gap-1.5 xl:justify-end ${DETAIL}`}>
                <span className="whitespace-nowrap pl-1.5 text-xs text-muted xl:hidden">
                  New price
                </span>
                <span
                  className={`px-1.5 text-right text-sm font-semibold tabular-nums ${
                    Number(r.sell_price) < Number(r.cost_price) ? "text-danger" : ""
                  }`}
                  title={
                    Number(r.sell_price) < Number(r.cost_price)
                      ? "Below the supplier price"
                      : undefined
                  }
                >
                  ₹{money(r.sell_price)}
                </span>
              </div>
              <div className={`min-w-0 ${DETAIL}`}>
                <EditableText
                  label={`Group of ${r.code}`}
                  value={r.category_name ?? ""}
                  placeholder="No group"
                  list="catalog-groups-list"
                  onSave={(v) =>
                    patchItem.mutate({ itemId: r.id, body: { group_name: v === "" ? null : v } })
                  }
                />
                {!r.category_name && r.suggested_group && (
                  <button
                    type="button"
                    className="px-1.5 text-[11px] text-accent underline"
                    onClick={() =>
                      patchItem.mutate({ itemId: r.id, body: { group_name: r.suggested_group } })
                    }
                  >
                    Use “{r.suggested_group}”
                  </button>
                )}
              </div>
              <button
                type="button"
                aria-pressed={r.included}
                className={`h-8 rounded-full border px-3 text-xs font-medium ${DETAIL} ${
                  r.included
                    ? "border-line bg-[#e6efe8] text-ok"
                    : "border-line bg-ground text-muted"
                }`}
                onClick={() => patchItem.mutate({ itemId: r.id, body: { included: !r.included } })}
              >
                {r.included ? "Included" : "Excluded"}
              </button>
            </li>
          ))}
        </ul>
        <div ref={sentinel} aria-hidden className="h-1" />
        {isFetchingNextPage && <p className="p-3 text-center text-sm text-muted">Loading more…</p>}
      </div>

      {ccOpen && cat && (
        <CustomerCatalogDialog
          catalogId={id}
          defaultTitle={cat.title}
          selection={selectedCount > 0 ? { count: selectedCount, target: target() } : null}
          filtered={filtered ? { count: total ?? 0, filter } : null}
          includedTotal={groupList.reduce((n, g) => n + g.included_count, 0)}
          onClose={() => {
            setCcOpen(false);
            qc.invalidateQueries({ queryKey: ["catalog-items", id] }); // codes may now be locked
          }}
        />
      )}
      {zoomRow?.image_url && (
        <ImageLightbox
          src={zoomRow.image_url}
          title={zoomRow.display_name}
          caption={`${zoomRow.code} · supplier ref ${zoomRow.supplier_code}`}
          onClose={() => setZoomRow(null)}
        />
      )}
      {promoteOpen && cat && (
        <PromoteDialog
          catalogId={id}
          selection={selectedCount > 0 ? { count: selectedCount, target: target() } : null}
          filtered={filtered ? { count: total ?? 0, filter } : null}
          includedTotal={groupList.reduce((n, g) => n + g.included_count, 0)}
          onClose={() => {
            setPromoteOpen(false);
            qc.invalidateQueries({ queryKey: ["catalog-items", id] });
          }}
        />
      )}
      {tallyOpen && cat && (
        <TallyDialog
          catalogId={id}
          selection={selectedCount > 0 ? { count: selectedCount, target: target() } : null}
          filtered={filtered ? { count: total ?? 0, filter } : null}
          includedTotal={groupList.reduce((n, g) => n + g.included_count, 0)}
          onClose={() => {
            setTallyOpen(false);
            qc.invalidateQueries({ queryKey: ["catalog-items", id] });
          }}
        />
      )}

      {labelsOpen && cat && (
        <LabelsDialog
          catalogId={id}
          fileSlug={cat.title.replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "").toLowerCase() || "catalog"}
          selection={selectedCount > 0 ? { count: selectedCount, target: target() } : null}
          filtered={filtered ? { count: total ?? 0, filter } : null}
          includedTotal={groupList.reduce((n, g) => n + g.included_count, 0)}
          onClose={() => {
            setLabelsOpen(false);
            qc.invalidateQueries({ queryKey: ["catalog-items", id] }); // codes may now be locked
          }}
        />
      )}

      <p className="mt-2 text-xs text-muted">
        {total !== null
          ? `Showing ${rows.length} of ${total}${filtered ? " matching" : ""} items.`
          : ""}
      </p>
    </div>
  );
}
