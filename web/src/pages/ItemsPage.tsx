import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useIsDesktop } from "../lib/useIsDesktop";
import type { Item, ItemListItem } from "../lib/types";
import { ItemForm } from "../components/ItemForm";
import { NewItemForm } from "../components/NewItemForm";
import { ItemTree } from "../components/ItemTree";
import { GroupForm } from "../components/GroupForm";
import { CategoryManager } from "../components/CategoryManager";

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

function buildQuery(q: string, scope: Scope) {
  const p = new URLSearchParams();
  if (q.trim()) p.set("q", q.trim());
  if (scope === "bulk" || scope === "mrp") p.set("type", scope);
  if (scope === "unconfirmed") p.set("status", "unconfirmed");
  if (scope === "archived") p.set("status", "archived");
  if (scope === "no_hsn") p.set("no_hsn", "true");
  if (scope === "price_review") p.set("price_review", "true");
  return p.toString();
}

export function ItemsPage() {
  const nav = useNavigate();
  const { id, groupId } = useParams();
  const { pathname } = useLocation();
  const isNew = pathname === "/items/new";
  const isCats = pathname === "/items/categories";
  const selectedId = isNew || groupId ? null : (id ?? null);

  const [view, setView] = useState<View>("tree");
  const [q, setQ] = useState("");
  const [scope, setScope] = useState<Scope>("");
  const isDesktop = useIsDesktop();
  // On mobile, show one pane at a time based on the route.
  const inDetail = isNew || isCats || !!groupId || !!selectedId;
  const showDetailPane = isDesktop || inDetail;
  const showRailPane = isDesktop || !inDetail;

  const list = useQuery({
    queryKey: ["items", q, scope],
    queryFn: () => api<ItemListItem[]>(`/items?${buildQuery(q, scope)}`),
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

        <div className="flex-1 overflow-y-auto">
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
              {!list.isLoading && list.data?.length === 0 && !isNew && (
                <div className="px-3 py-8 text-center text-xs text-muted">
                  {q || scope ? "No matches." : "No items yet."}
                </div>
              )}
              {list.data?.map((it) => (
                <button
                  key={it.id}
                  onClick={() => nav(`/items/${it.id}`)}
                  className={`block w-full border-b border-[#f3eee4] px-3 py-3 text-left md:py-2 ${
                    it.id === selectedId
                      ? "bg-card shadow-[inset_2px_0_0_theme(colors.accent.DEFAULT)]"
                      : "hover:bg-accent-soft"
                  }`}
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
                    <span className="font-medium">{it.name}</span>
                    {it.status === "unconfirmed" && (
                      <span className="ml-auto rounded-sm bg-[#f1e7d6] px-1 py-0.5 text-[8px] font-bold uppercase text-warn">
                        unconfirmed
                      </span>
                    )}
                    {it.status === "confirmed" && !it.hsn_code && (
                      <span className="ml-auto rounded-sm bg-[#efe9df] px-1 py-0.5 text-[8px] font-bold uppercase text-muted">
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
              ))}
            </>
          )}
        </div>
      </div>

      {/* detail */}
      <div className={`${showDetailPane ? "flex" : "hidden"} min-w-0 flex-1 flex-col md:flex`}>
        {!isDesktop && inDetail && (
          <button
            className="flex items-center gap-2 border-b border-line px-4 py-3 text-sm font-medium text-accent md:hidden"
            onClick={() => nav("/items")}
          >
            ← Items
          </button>
        )}
        <div className="flex-1 overflow-y-auto p-4 md:p-5">
        {isCats ? (
          <CategoryManager onClose={() => nav("/items")} />
        ) : groupId ? (
          <GroupForm key={groupId} groupId={groupId} />
        ) : isNew ? (
          <NewItemForm onCreated={(it) => nav(`/items/${it.id}`)} onCancel={() => nav("/items")} />
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
