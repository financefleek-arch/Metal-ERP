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
import { SupplierPicker } from "../../components/catalog/SupplierPicker";
import { EditableNumber, EditableText } from "../../components/catalog/Editable";
import { MakeCatalogDialog } from "../../components/catalog/MakeCatalogDialog";
import type { PdfTarget } from "../../lib/catalog";
import { ImageLightbox } from "../../components/catalog/ImageLightbox";
import { ReplacePhoto } from "../../components/catalog/ReplacePhoto";
import { CatalogSteps } from "../../components/catalog/CatalogSteps";

/** What the table shows. One choice instead of a chip and a dropdown for each question. */
type View = "all" | "group" | "match" | "photo" | "price" | "new" | "margin" | "excluded";
type Look = Pick<ItemFilter, "suggested_group" | "no_suggestion">;
type Pop = "group" | "margin" | "more" | null;
type ItemPages = InfiniteData<Page<CatalogItem[]>, string | null>;

const VIEW_LABEL: Record<View, string> = {
  all: "All items",
  group: "Needs a group",
  match: "Possible matches",
  photo: "Photo to check",
  price: "Price changed",
  new: "Not in your items",
  margin: "Has its own margin",
  excluded: "Excluded",
};

// One column template for the header and every row, so columns line up. Below xl the row is
// checkbox + photo + name on top, with the detail fields stacked under the photo column.
const COLS =
  "xl:grid-cols-[28px_100px_minmax(0,1fr)_84px_70px_112px_96px_104px_170px_118px] xl:items-center xl:gap-x-3";
// Detail cells below xl start under the photo and span to the right edge.
const DETAIL = "col-span-2 col-start-2 xl:col-span-1 xl:col-start-auto";

function Status({ r, onInclude }: { r: CatalogItem; onInclude: () => void }) {
  const base = "inline-flex h-7 items-center whitespace-nowrap rounded-full px-3 text-xs font-medium";
  if (!r.included)
    return (
      <button
        type="button"
        className={`${base} border border-line bg-ground text-muted line-through hover:bg-card`}
        title="Excluded from adding. Click to include it again."
        onClick={onInclude}
      >
        Excluded
      </button>
    );
  if (r.item_id) return <span className={`${base} bg-[#e6efe8] text-ok`}>In your items</span>;
  if (r.suggestion) return <span className={`${base} bg-accent-soft text-accent`}>Possible match</span>;
  if (!r.category_name) return <span className={`${base} bg-[#f6edd6] text-warn`}>Needs a group</span>;
  return <span className={`${base} border border-line bg-ground text-muted`}>Ready</span>;
}

export function CatalogReviewPage() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();

  const [q, setQ] = useState("");
  const dq = useDebounced(q, 250);
  const [groupSel, setGroupSel] = useState<string>("all"); // "all" | "none" | category id
  const [view, setView] = useState<View>("all");
  const [look, setLook] = useState<Look | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [allMatching, setAllMatching] = useState(false);
  const [bulkGroup, setBulkGroup] = useState("");
  const [bulkMargin, setBulkMargin] = useState("");
  const [pop, setPop] = useState<Pop>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [pdfOpen, setPdfOpen] = useState(false);
  const [zoomRow, setZoomRow] = useState<CatalogItem | null>(null);
  const [pickSupplier, setPickSupplier] = useState(false);
  const [importNote, setImportNote] = useState<string | null>(null);

  // how many of this price list's products are in Items now: the catalog can only be made from those
  const inItems = useQuery({
    queryKey: ["catalog-in-items", id],
    queryFn: () => api<{ count: number }>("/items/count", { method: "POST", body: { catalog_id: id } }),
  });
  const itemCount = inItems.data?.count ?? 0;

  // included rows that are not in your items yet
  const notAddedQ = useQuery({
    queryKey: ["catalog-queues", id],
    queryFn: () =>
      apiPage<unknown[]>(
        `/supplier-catalogs/${id}/items?${itemsQuery({ not_added: true, included: true }, null).replace(/limit=\d+/, "limit=1")}`,
      ).then((p) => p.total ?? 0),
  });

  const filter: ItemFilter = useMemo(
    () => ({
      q: dq.trim() || undefined,
      no_group: groupSel === "none" || view === "group" || look !== null || undefined,
      category_id: groupSel !== "all" && groupSel !== "none" ? groupSel : undefined,
      included: view === "excluded" ? false : undefined,
      has_item_margin: view === "margin" ? true : undefined,
      has_suggestion: view === "match" || undefined,
      not_added: view === "new" || undefined,
      price_changed: view === "price" || undefined,
      photo_check: view === "photo" || undefined,
      suggested_group: look?.suggested_group,
      no_suggestion: look?.no_suggestion,
    }),
    [dq, groupSel, view, look],
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
    refetchOnWindowFocus: false,
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
    setPop(null);
  }, [filter, id]);

  // popovers and the ⋯ menu close on an outside click or Escape
  useEffect(() => {
    if (!pop && !menuOpen) return;
    const away = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest("[data-pop-root]")) {
        setPop(null);
        setMenuOpen(false);
      }
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setPop(null);
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [pop, menuOpen]);

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
    qc.invalidateQueries({ queryKey: ["catalog-group-suggestions", id] });
    qc.invalidateQueries({ queryKey: ["item-categories"] });
  }

  // One edit at a time (so your own quick edits never clash), each carrying the version of the
  // row it was made on: if someone else changed the row meanwhile, the save is refused.
  const patchItem = useMutation({
    scope: { id: `catalog-edit-${id}` },
    mutationFn: (v: { itemId: string; body: ItemPatch }) => {
      let seen: string | null = null;
      for (const [, data] of qc.getQueriesData<ItemPages>({ queryKey: ["catalog-items", id] })) {
        const hit = data?.pages.flatMap((p) => p.data).find((r) => r.id === v.itemId);
        if (hit) {
          seen = hit.updated_at;
          break;
        }
      }
      return api<CatalogItem>(`/supplier-catalogs/${id}/items/${v.itemId}`, {
        method: "PATCH",
        body: seen ? { ...v.body, expected_updated_at: seen } : v.body,
      });
    },
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
      if ("group_name" in v.body || "included" in v.body) {
        refreshGroups();
        qc.invalidateQueries({ queryKey: ["catalog-queues", id] });
      }
      if ("item_margin_pct" in v.body) qc.invalidateQueries({ queryKey: ["supplier-catalog", id] });
    },
    onError: (e) => {
      onError(e);
      if (e instanceof ApiError && e.status === 409) {
        qc.invalidateQueries({ queryKey: ["catalog-items", id] }); // show what the other person did
      }
    },
  });

  // accept / dismiss suggested matches: on the chosen rows, or on every row that has one
  const matchesBulk = useMutation({
    mutationFn: (v: { action: "accept" | "dismiss"; all?: boolean }) =>
      api<{ done: number; skipped: number }>(`/supplier-catalogs/${id}/matches/${v.action}`, {
        method: "POST",
        body: v.all ? { filter: { has_suggestion: true } } : target(),
      }).then((r) => ({ ...r, action: v.action })),
    onSuccess: (r) => {
      setErr(null);
      setFlash(
        r.action === "accept"
          ? `${r.done} matched${r.skipped ? `, ${r.skipped} could not be (code already in use)` : ""}.`
          : `${r.done} suggested matches dismissed.`,
      );
      qc.invalidateQueries({ queryKey: ["catalog-items", id] });
      qc.invalidateQueries({ queryKey: ["supplier-catalog", id] });
      refreshGroups();
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
      setPop(null);
      qc.invalidateQueries({ queryKey: ["catalog-items", id] });
      qc.invalidateQueries({ queryKey: ["catalog-queues", id] });
      qc.invalidateQueries({ queryKey: ["supplier-catalog", id] });
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
      qc.invalidateQueries({ queryKey: ["supplier-catalog", id] });
      qc.invalidateQueries({ queryKey: ["catalog-items", id] });
    },
    onError,
  });

  const dismissSuggestion = useMutation({
    mutationFn: (itemId: string) =>
      api<CatalogItem>(`/supplier-catalogs/${id}/items/${itemId}/suggestion`, {
        method: "DELETE",
      }),
    onSuccess: (updated) => {
      replaceRow(updated);
      qc.invalidateQueries({ queryKey: ["supplier-catalog", id] });
      qc.invalidateQueries({ queryKey: ["catalog-items", id] });
    },
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
      nav("/documents");
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
  function chooseView(v: View) {
    setView(v);
    setLook(null);
  }

  if (catalog.isError) {
    return (
      <div className="max-w-xl">
        <p className="err">That catalog was not found.</p>
        <Link to="/documents" className="text-sm text-accent hover:underline">
          Back to catalogs
        </Link>
      </div>
    );
  }

  const cat = catalog.data;
  const groupList = groups.data ?? [];
  const noGroupCount = groupList.find((g) => g.category_id === null)?.item_count ?? 0;
  const excludedCount = groupList.reduce((n, g) => n + (g.item_count - g.included_count), 0);
  const counts: Partial<Record<View, number>> = {
    group: noGroupCount,
    match: cat?.suggestion_count ?? 0,
    photo: cat?.photo_check_count ?? 0,
    price: cat?.price_change_count ?? 0,
    new: notAddedQ.data ?? 0,
    margin: cat?.item_margin_count ?? 0,
    excluded: excludedCount,
  };
  const filtered = !!(filter.q || filter.no_group || filter.category_id || view !== "all");
  const activeLabel = look
    ? look.no_suggestion
      ? "No group, no suggestion"
      : `Suggested: ${look.suggested_group}`
    : view !== "all"
      ? VIEW_LABEL[view]
      : null;

  const menuItem =
    "block w-full rounded px-3 py-2 text-left text-sm hover:bg-ground";

  return (
    <div>
      <div className="mb-1 text-sm">
        <Link to="/documents" className="text-accent hover:underline">
          Supplier catalogs
        </Link>
      </div>

      <div className="mb-4 flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          {cat ? (
            <EditableText
              label="Catalog name"
              value={cat.title}
              onSave={(t) => t && rename.mutate(t)}
              className="page-title"
            />
          ) : (
            <div className="h-8 w-64 animate-pulse rounded bg-line" />
          )}
          {cat && (
            <p className="px-1.5 text-sm text-muted">
              {cat.item_count} items · {cat.page_count} pages ·{" "}
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
                With a supplier, a later catalog from them reuses these codes and margin.
              </p>
            </div>
          )}
          {importNote && (
            <p className="mt-1 px-1.5 text-sm text-ok" role="status">
              {importNote}
            </p>
          )}
        </div>

        {confirmDelete ? (
          <span className="flex items-center gap-2 text-sm">
            Delete this price list?
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
          <div className="relative flex items-center gap-2" data-pop-root>
            {cat?.status === "ready" && (
              <button type="button" className="btn-primary h-9 px-3" onClick={() => setPdfOpen(true)}>
                {selectedCount > 0 ? `Customer PDF (${selectedCount})` : "Customer PDF"}
              </button>
            )}
            <button
              type="button"
              className="btn-ghost"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              aria-label="More actions"
              onClick={() => setMenuOpen((v) => !v)}
            >
              ⋯
            </button>
            {menuOpen && (
              <div
                role="menu"
                className="card absolute right-0 top-11 z-30 w-64 bg-card p-1 shadow-lg"
              >
                {cat?.supplier_party_id ? (
                  <Link role="menuitem" className={menuItem} to={`/parties/${cat.supplier_party_id}`}>
                    Supplier defaults
                  </Link>
                ) : (
                  <button
                    role="menuitem"
                    type="button"
                    className={menuItem}
                    onClick={() => {
                      setMenuOpen(false);
                      setPickSupplier(true);
                    }}
                  >
                    Set supplier
                  </button>
                )}
                <Link role="menuitem" className={menuItem} to="/items/categories">
                  Rename or merge groups in Items
                </Link>
                <hr className="my-1 border-line" />
                <button
                  role="menuitem"
                  type="button"
                  className={`${menuItem} text-danger`}
                  onClick={() => {
                    setMenuOpen(false);
                    setConfirmDelete(true);
                  }}
                >
                  Delete this price list
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {err && (
        <p className="err mb-3" role="alert">
          {err}
        </p>
      )}

      {cat && (
        <CatalogSteps
          catalog={cat}
          groups={groupList}
          noGroup={noGroupCount}
          notAdded={notAddedQ.data}
          inItems={itemCount}
          selection={selectedCount > 0 ? { count: selectedCount, target: target() } : null}
          resetting={bulk.isPending}
          onResetItemMargins={() =>
            bulk.mutate({
              target: { filter: { has_item_margin: true } },
              changes: { item_margin_pct: null },
            })
          }
          onLook={(f) => {
            setView("group");
            setLook(f);
          }}
        />
      )}

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
        <div className="max-md:w-full">
          <label className="label" htmlFor="cat-group">
            Group
          </label>
          <select
            id="cat-group"
            className="field md:w-52"
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
        <div className="max-md:w-full">
          <label className="label" htmlFor="cat-show">
            Show
          </label>
          <select
            id="cat-show"
            className="field md:w-60"
            value={view}
            onChange={(e) => chooseView(e.target.value as View)}
          >
            {(Object.keys(VIEW_LABEL) as View[]).map((v) => {
              const n = counts[v];
              return (
                <option key={v} value={v} disabled={v !== "all" && v !== view && !n}>
                  {VIEW_LABEL[v]}
                  {v === "all" ? ` (${cat?.item_count ?? ""})` : ` (${n ?? 0})`}
                </option>
              );
            })}
          </select>
        </div>
        {activeLabel && (
          <span className="mb-1 inline-flex items-center gap-1 rounded-full bg-accent-soft py-1 pl-3 pr-1.5 text-sm font-medium text-accent">
            {activeLabel}
            <button
              type="button"
              aria-label="Clear filter"
              className="px-1.5 text-base leading-none"
              onClick={() => chooseView("all")}
            >
              ×
            </button>
          </span>
        )}
      </div>

      {view === "match" && (cat?.suggestion_count ?? 0) > 0 && (
        <div className="card mb-3 flex flex-wrap items-center gap-3 p-3 text-sm">
          <span>
            {cat?.suggestion_count} rows look like products you already have. A match takes the
            existing product&apos;s code, group and name.
          </span>
          <button
            type="button"
            className="btn-ghost"
            disabled={matchesBulk.isPending}
            onClick={() => matchesBulk.mutate({ action: "accept", all: true })}
          >
            Accept all {cat?.suggestion_count}
          </button>
          <button
            type="button"
            className="btn-ghost"
            disabled={matchesBulk.isPending}
            onClick={() => matchesBulk.mutate({ action: "dismiss", all: true })}
          >
            Not the same for all
          </button>
        </div>
      )}

      {/* bulk bar */}
      {selectedCount > 0 && (
        <div
          className="card sticky top-2 md:top-[7.5rem] z-10 mb-3 flex flex-wrap items-center gap-2 border-accent bg-accent-soft p-2.5"
          role="region"
          aria-label="Bulk actions"
          data-pop-root
        >
          <span className="mr-1 text-sm font-medium">
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

          <span className="relative">
            <button
              type="button"
              className="btn-ghost"
              aria-expanded={pop === "group"}
              onClick={() => setPop((p) => (p === "group" ? null : "group"))}
            >
              Set group ▾
            </button>
            {pop === "group" && (
              <div className="card absolute left-0 top-11 z-30 flex items-center gap-2 bg-card p-2.5 shadow-lg">
                <label className="sr-only" htmlFor="bulk-group">
                  Group name
                </label>
                <input
                  id="bulk-group"
                  className="field w-48"
                  list="catalog-groups-list"
                  placeholder="Group name"
                  value={bulkGroup}
                  autoFocus
                  onChange={(e) => setBulkGroup(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && bulkGroup.trim() && !bulk.isPending)
                      bulk.mutate({ target: target(), changes: { group_name: bulkGroup.trim() } });
                  }}
                />
                <button
                  type="button"
                  className="btn-primary"
                  disabled={!bulkGroup.trim() || bulk.isPending}
                  onClick={() => bulk.mutate({ target: target(), changes: { group_name: bulkGroup.trim() } })}
                >
                  Apply
                </button>
              </div>
            )}
          </span>

          <span className="relative">
            <button
              type="button"
              className="btn-ghost"
              aria-expanded={pop === "margin"}
              onClick={() => setPop((p) => (p === "margin" ? null : "margin"))}
            >
              Set margin ▾
            </button>
            {pop === "margin" && (
              <div className="card absolute left-0 top-11 z-30 flex items-center gap-2 bg-card p-2.5 shadow-lg">
                <label className="sr-only" htmlFor="bulk-margin">
                  Item margin percent
                </label>
                <input
                  id="bulk-margin"
                  className="field w-28 text-right font-mono"
                  inputMode="decimal"
                  placeholder="Margin %"
                  aria-invalid={bulkMargin.trim() !== "" && !validMargin(bulkMargin)}
                  value={bulkMargin}
                  autoFocus
                  onChange={(e) => setBulkMargin(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && validMargin(bulkMargin) && !bulk.isPending)
                      bulk.mutate({
                        target: target(),
                        changes: { item_margin_pct: Number(bulkMargin).toFixed(2) },
                      });
                  }}
                />
                <button
                  type="button"
                  className="btn-primary"
                  disabled={!validMargin(bulkMargin) || bulk.isPending}
                  onClick={() =>
                    bulk.mutate({
                      target: target(),
                      changes: { item_margin_pct: Number(bulkMargin).toFixed(2) },
                    })
                  }
                >
                  Apply
                </button>
              </div>
            )}
          </span>

          <button
            type="button"
            className="btn-ghost"
            disabled={bulk.isPending}
            onClick={() =>
              bulk.mutate({ target: target(), changes: { included: view === "excluded" } })
            }
          >
            {view === "excluded" ? "Include" : "Exclude"}
          </button>

          <span className="relative">
            <button
              type="button"
              className="btn-ghost"
              aria-haspopup="menu"
              aria-expanded={pop === "more"}
              onClick={() => setPop((p) => (p === "more" ? null : "more"))}
            >
              More ▾
            </button>
            {pop === "more" && (
              <div role="menu" className="card absolute left-0 top-11 z-30 w-52 bg-card p-1 shadow-lg">
                {(
                  [
                    ["Include", () => bulk.mutate({ target: target(), changes: { included: true } })],
                    ["Clear group", () => bulk.mutate({ target: target(), changes: { group_name: null } })],
                    ["Clear own margin", () => bulk.mutate({ target: target(), changes: { item_margin_pct: null } })],
                    ["Accept matches", () => matchesBulk.mutate({ action: "accept" })],
                    ["Not the same", () => matchesBulk.mutate({ action: "dismiss" })],
                  ] as const
                ).map(([label, run]) => (
                  <button
                    key={label}
                    role="menuitem"
                    type="button"
                    className={menuItem}
                    onClick={() => {
                      setPop(null);
                      run();
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
          </span>

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
          <span className="text-right">Own margin</span>
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
          {rows.map((r) => {
            const flagged = !!r.image_flag;
            return (
              <li
                key={r.id}
                className={`group grid grid-cols-[24px_102px_minmax(0,1fr)] gap-x-3 gap-y-2 border-b border-line px-3 py-3 last:border-0 ${COLS} ${
                  r.included ? "" : "bg-ground/60"
                }`}
              >
                <input
                  type="checkbox"
                  aria-label={`Select ${r.display_name}`}
                  checked={selected.has(r.id) || allMatching}
                  onChange={() => toggleRow(r.id)}
                />
                <div>
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
                  {flagged && (
                    <p className="mt-0.5 w-[102px] text-center text-[11px] text-warn">
                      {{ small: "Small picture", odd_shape: "Odd shape", blank: "Looks blank", unreadable: "Cannot read" }[
                        r.image_flag as string
                      ] ?? "Check photo"}{" "}
                      <button
                        type="button"
                        className="underline"
                        onClick={() => patchItem.mutate({ itemId: r.id, body: { photo_ok: true } })}
                      >
                        It is fine
                      </button>
                    </p>
                  )}
                  {/* a photo that looks right only offers a replacement on hover or focus */}
                  <div
                    className={
                      r.image_url && !flagged
                        ? "opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100"
                        : ""
                    }
                  >
                    <ReplacePhoto
                      catalogId={id}
                      rowId={r.id}
                      hasPhoto={!!r.image_url}
                      onDone={() => {
                        qc.invalidateQueries({ queryKey: ["catalog-items", id] });
                        qc.invalidateQueries({ queryKey: ["items"] });
                      }}
                    />
                  </div>
                </div>
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
                    {r.price_change === "up" || r.price_change === "down" ? (
                      <span className={r.price_change === "up" ? "text-danger" : "text-ok"}>
                        {` · price ${r.price_change === "up" ? "up" : "down"} from ₹${r.previous_cost ?? "?"}`}
                      </span>
                    ) : r.price_change === "new" && catalog.data?.supplier_party_id ? (
                      <span> · new</span>
                    ) : null}
                    {r.last_paid ? ` · last bought at ₹${r.last_paid}` : ""}
                    {r.tally_status === "synced" ? (
                      <span className="text-ok"> · in Tally</span>
                    ) : r.tally_status === "error" ? (
                      <span className="text-danger"> · Tally rejected it</span>
                    ) : null}
                  </p>
                  {r.suggestion && (
                    <div className="mx-1.5 mt-1.5 max-w-md rounded border border-accent/40 bg-accent-soft px-2 py-1.5 text-[11px] leading-snug">
                      <p>
                        Same as <b className="font-mono">{r.suggestion.code}</b>{" "}
                        <span className="text-muted">
                          {r.suggestion.name}
                          {r.suggestion.supplier_name ? ` · ${r.suggestion.supplier_name}` : ""}
                        </span>
                        ?
                      </p>
                      <span className="mt-1 inline-flex gap-3">
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
                </div>
                <div className={`min-w-0 px-1.5 ${DETAIL}`}>
                  <span className="font-mono text-xs">{r.code}</span>
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
                    Own margin
                  </span>
                  <EditableNumber
                    kind="margin"
                    allowEmpty
                    placeholder="—"
                    label={`Own margin of ${r.code}`}
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
                <div className={DETAIL}>
                  <Status
                    r={r}
                    onInclude={() => patchItem.mutate({ itemId: r.id, body: { included: true } })}
                  />
                </div>
              </li>
            );
          })}
        </ul>
        <div ref={sentinel} aria-hidden className="h-1" />
        {isFetchingNextPage && <p className="p-3 text-center text-sm text-muted">Loading more…</p>}
      </div>

      {zoomRow?.image_url && (
        <ImageLightbox
          src={zoomRow.image_url}
          title={zoomRow.display_name}
          caption={`${zoomRow.code} · supplier ref ${zoomRow.supplier_code}`}
          onClose={() => setZoomRow(null)}
        />
      )}
      {pdfOpen && cat && (
        <MakeCatalogDialog
          priceList={{
            catalogId: cat.id,
            target: selectedCount > 0 ? (target() as PdfTarget) : { all_included: true },
          }}
          count={selectedCount > 0 ? selectedCount : (total ?? 0)}
          defaultTitle={cat.title}
          onClose={() => setPdfOpen(false)}
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
