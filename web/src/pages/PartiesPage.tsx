import { initials } from "../lib/initials";
import { HeaderMenu } from "../components/HeaderMenu";
import { PageHeader } from "../components/PageHeader";
import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api, apiPage } from "../lib/api";
import { useDebounced } from "../lib/useDebounced";
import { NAV_ACTIVE, useListNav } from "../lib/useListNav";
import { lastSeenLabel, missingLabel } from "../lib/format";
import { useIsDesktop } from "../lib/useIsDesktop";
import type { Party, PartyListItem, PartyRole, PartyStatus } from "../lib/types";

const PAGE_SIZE = 50;
import { PartyForm } from "../components/PartyForm";
import { NewPartyForm } from "../components/NewPartyForm";
import { PartyAccountTab } from "../components/PartyAccountTab";
import { PartyCatalogsTab } from "../components/catalog/PartyCatalogsTab";
import { OffersConsent } from "../components/OffersConsent";
import { useAuth } from "../lib/auth";

type Scope = "" | PartyRole | "incomplete" | "archived";
type Offers = "" | "agreed" | "not_asked" | "opted_out";

const FILTERS: { key: Scope; label: string }[] = [
  { key: "", label: "All" },
  { key: "customer", label: "Customers" },
  { key: "supplier", label: "Suppliers" },
  { key: "both", label: "Both" },
  { key: "incomplete", label: "Incomplete" },
  { key: "archived", label: "Archived" },
];

function roleTag(role: PartyRole) {
  return role === "customer" ? "cust" : role === "supplier" ? "supp" : "both";
}

const OFFERS: { key: Exclude<Offers, "">; label: string }[] = [
  { key: "agreed", label: "Agreed" },
  { key: "not_asked", label: "Not asked" },
  { key: "opted_out", label: "Said no" },
];

function buildQuery(q: string, scope: Scope, offers: Offers, cursor?: string | null) {
  const p = new URLSearchParams();
  if (offers) p.set("offers", offers);
  if (q.trim()) p.set("q", q.trim());
  if (scope === "customer" || scope === "supplier" || scope === "both") p.set("role", scope);
  if (scope === "incomplete") p.set("completeness", "incomplete");
  if (scope === "archived") p.set("status", "archived" satisfies PartyStatus);
  // The server pages only the plain browse list — not a search, and not the
  // `incomplete` filter (a post-query Python filter).
  if (!q.trim() && scope !== "incomplete") {
    p.set("limit", String(PAGE_SIZE));
    if (cursor) p.set("cursor", cursor);
  }
  return p.toString();
}

/** Bottom-of-list sentinel: auto-loads the next page when scrolled into view. */
function LoadMore({ loading, onLoad }: { loading: boolean; onLoad: () => void }) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && !loading) onLoad();
      },
      { rootMargin: "200px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [loading, onLoad]);
  return (
    <div ref={ref} className="px-3 py-3 text-center text-[11px] text-muted">
      {loading ? "Loading…" : "Scroll for more"}
    </div>
  );
}

export function PartiesPage() {
  const nav = useNavigate();
  const { id } = useParams();
  const { pathname, search } = useLocation();
  // The /parties/new route has no :id param, so read it from the path.
  const isNew = pathname === "/parties/new";
  const selectedId = isNew ? null : (id ?? null);

  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 250);
  const [scope, setScope] = useState<Scope>("");
  // the composer's "not asked" link arrives as /parties?offers=not_asked
  const [offers, setOffers] = useState<Offers>(() => {
    const o = new URLSearchParams(search).get("offers");
    return o === "agreed" || o === "not_asked" || o === "opted_out" ? o : "";
  });
  const [detailTab, setDetailTab] = useState<"details" | "account" | "catalogs">("details");
  const { me } = useAuth();
  const isDesktop = useIsDesktop();
  // On mobile we show one pane at a time, driven by the route.
  const showDetailPane = isDesktop || isNew || !!selectedId;
  const showRailPane = isDesktop || (!isNew && !selectedId);

  const paged = !dq && scope !== "incomplete";

  const list = useInfiniteQuery({
    queryKey: ["parties", dq, scope, offers],
    refetchOnWindowFocus: false,
    queryFn: ({ pageParam }) =>
      apiPage<PartyListItem[]>(`/parties?${buildQuery(dq, scope, offers, pageParam)}`),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.nextCursor,
  });
  const rows = useMemo(
    () => list.data?.pages.flatMap((p) => p.data) ?? [],
    [list.data],
  );

  const offerCounts = useQuery({
    queryKey: ["offers-counts"],
    queryFn: () => api<Record<Exclude<Offers, "">, number>>("/parties/offers-counts"),
    refetchOnWindowFocus: false,
  });

  const detail = useQuery({
    queryKey: ["party", selectedId],
    queryFn: () => api<Party>(`/parties/${selectedId}`),
    enabled: !!selectedId,
  });

  // If the selected id vanishes from a fresh list (deleted/archived-out), clear it.
  useEffect(() => {
    if (selectedId && rows.length && !rows.some((p) => p.id === selectedId) && detail.isError) {
      nav("/parties", { replace: true });
    }
  }, [selectedId, rows, detail.isError, nav]);

  // Keyboard: "/" search, ↓ into the list, ↑↓ move, Enter opens
  const kb = useListNav(rows, (p) => nav(`/parties/${p.id}`));

  // a fresh party selection always opens on Details.
  useEffect(() => setDetailTab("details"), [selectedId]);

  return (
    <div className="mx-auto max-w-5xl">
<PageHeader className={showRailPane ? "" : "hidden md:flex"} title="Parties" subtitle="Customers and suppliers." actions={<>
        <div className="md:hidden">
          <HeaderMenu
            label="⋯"
            caret={false}
            className="btn-ghost w-11 px-0 text-xl"
            entries={[{ label: "Import from Tally (masters XML)", onClick: () => nav("/parties/import") }]}
          />
        </div>
        <button
          className="btn-ghost max-md:hidden"
          title="Bulk import from a Tally masters XML"
          onClick={() => nav("/parties/import")}
        >
          ⇧ Tally
        </button>
        <button className="btn-primary max-md:hidden" onClick={() => nav("/parties/new")}>
          + New
        </button>
      </>} />
<div className="flex min-h-[calc(100dvh-13rem)] flex-col gap-0 rounded-xl border border-line bg-card md:min-h-[calc(100dvh-14rem)] md:flex-row md:overflow-hidden">
      {/* rail */}
      <div
        className={`${
          showRailPane ? "flex" : "hidden"
        } w-full shrink-0 flex-col border-b border-line bg-ground md:flex md:w-[300px] md:border-b-0 md:border-r`}
      >
        <div className="flex flex-col gap-2 border-b border-line p-3">
          <input
            id={kb.searchId}
            className="field"
            placeholder="search name, address, phone…  ( / )"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <div className="chip-row">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => setScope(f.key)}
                className={`chip ${
                  scope === f.key
                    ? "border-ink bg-ink text-ground hover:bg-ink"
                    : "border-line bg-card text-muted hover:bg-ground"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
          {offerCounts.data && (
            <div className="chip-row items-center" role="group" aria-label="Offers on WhatsApp">
              <span className="shrink-0 text-[11px] uppercase tracking-wide text-muted">Offers</span>
              {OFFERS.map((f) => (
                <button
                  key={f.key}
                  aria-pressed={offers === f.key}
                  onClick={() => setOffers(offers === f.key ? "" : f.key)}
                  className={`chip ${
                    offers === f.key
                      ? "border-accent bg-accent text-ground hover:bg-accent"
                      : "border-line bg-card text-muted hover:bg-ground"
                  }`}
                >
                  {f.label} <span className="tabular-nums opacity-70">{offerCounts.data[f.key]}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="flex-1 overflow-y-auto">
          {isNew && (
            <div className="border-b border-[#f3eee4] bg-card px-3 py-2 shadow-[inset_2px_0_0_theme(colors.accent.DEFAULT)]">
              <div className="text-[11px] font-medium text-accent">New party — unsaved</div>
              <div className="text-[11px] text-muted">fill name to save</div>
            </div>
          )}
          {list.isLoading && <div className="px-3 py-6 text-xs text-muted">Loading…</div>}
          {!list.isLoading && rows.length === 0 && !isNew && (
            <div className="px-3 py-8 text-center text-xs text-muted">
              {dq || scope || offers ? "No matches." : "No parties yet."}
            </div>
          )}
          {rows.map((p, idx) => (
            <button
              key={p.id}
              {...kb.navProps(idx)}
              onClick={() => nav(`/parties/${p.id}`)}
              className={`flex w-full items-center gap-3 border-b border-[#f3eee4] px-4 py-2 text-left max-md:min-h-[60px] md:block md:px-3 ${
                kb.isActive(idx)
                  ? NAV_ACTIVE
                  : p.id === selectedId
                    ? "bg-card shadow-[inset_2px_0_0_theme(colors.accent.DEFAULT)]"
                    : "hover:bg-accent-soft"
              } ${p.status === "archived" ? "opacity-55" : ""}`}
            >
              <span className="grid h-10 w-10 flex-none place-items-center rounded-full bg-accent-soft text-[13px] font-semibold text-accent md:hidden">
                {initials(p.legal_name)}
              </span>
              <span className="block min-w-0 flex-1">
                <span className="flex items-center gap-1.5">
                  <span className="hidden rounded-sm bg-ground px-1 text-[11px] font-semibold uppercase tracking-wide text-muted md:inline">
                    {roleTag(p.role)}
                  </span>
                  <span className="truncate text-[15px] font-medium md:text-sm">{p.legal_name}</span>
                  {!p.completeness.complete && (
                    <span className="ml-auto flex-none rounded-full bg-[#f1e7d6] px-2 text-[11px] font-semibold text-warn md:rounded-sm md:px-1 md:uppercase">
                      <span className="md:hidden">Pending</span>
                      <span className="max-md:hidden">details pending</span>
                    </span>
                  )}
                </span>
                <span className="mt-0.5 block truncate text-xs text-muted">
                  <span className="capitalize md:hidden">{p.role} · </span>
                  {p.source === "inward_bill" && <span className="text-accent">◆ from inward bill · </span>}
                  {p.completeness.complete
                    ? lastSeenLabel(p.last_txn_at)
                    : `missing: ${missingLabel(p.completeness.missing)}`}
                </span>
              </span>
              <span className="text-lg text-muted md:hidden" aria-hidden>›</span>
            </button>
          ))}
          {paged && list.hasNextPage && (
            <LoadMore
              loading={list.isFetchingNextPage}
              onLoad={() => list.fetchNextPage()}
            />
          )}
          {dq && rows.length >= PAGE_SIZE && (
            <div className="px-3 py-3 text-center text-[11px] text-muted">
              Showing the closest {PAGE_SIZE} — add another word to narrow.
            </div>
          )}
        </div>
      </div>

      {/* detail */}
      <div className={`${showDetailPane ? "flex" : "hidden"} min-w-0 flex-1 flex-col md:flex`}>
        {!isDesktop && (isNew || selectedId) && (
          <button
            className="flex h-11 items-center gap-1 px-3 text-base font-medium text-accent md:hidden"
            onClick={() => nav("/parties")}
          >
            ‹ Parties
          </button>
        )}
        {!isDesktop && !isNew && selectedId && detail.data && (
          <div className="px-4 pt-1">
            <h1 className="font-serif text-[22px] font-semibold leading-tight">{detail.data.legal_name}</h1>
            <p className="meta mt-0.5">
              <span className="capitalize">{detail.data.role}</span>
              {detail.data.phone && ` · ${detail.data.phone}`}
            </p>
          </div>
        )}
        {!isNew && selectedId && detail.data && (
          <div className="mx-4 mt-3 flex gap-0.5 rounded-[10px] bg-line p-[3px] md:mx-0 md:mt-0 md:gap-0 md:rounded-none md:border-b md:border-line md:bg-card md:p-0">
            {(
              [
                "details",
                "account",
                ...((me?.ext_supplier_catalog || me?.ext_inward_import) &&
                detail.data.role !== "customer"
                  ? (["catalogs"] as const)
                  : []),
              ] as const
            ).map((t) => (
              <button
                key={t}
                className={`flex-1 rounded-lg py-2 text-center text-[13px] font-medium md:-mb-px md:flex-none md:rounded-none md:border-b-2 md:px-5 md:py-3 md:text-sm ${
                  detailTab === t
                    ? "bg-card text-ink shadow-sm md:border-accent md:bg-transparent md:font-semibold md:shadow-none"
                    : "text-muted md:border-transparent"
                }`}
                onClick={() => setDetailTab(t)}
              >
                {t === "details" ? "Details" : t === "account" ? "Account" : "Documents"}
              </button>
            ))}
          </div>
        )}
        <div className="flex-1 overflow-y-auto p-4 md:p-5">
        {isNew ? (
          <NewPartyForm
            onCreated={(p) => nav(`/parties/${p.id}`)}
            onCancel={() => nav("/parties")}
          />
        ) : !selectedId ? (
          <div className="grid h-full place-items-center text-sm text-muted">
            Select a party, or add a new one.
          </div>
        ) : detail.isLoading ? (
          <div className="grid h-full place-items-center text-sm text-muted">Loading…</div>
        ) : detail.data ? (
          detailTab === "account" ? (
            <PartyAccountTab party={detail.data} />
          ) : detailTab === "catalogs" ? (
            <PartyCatalogsTab party={detail.data} />
          ) : (
            <>
              {detail.data.role !== "supplier" && <OffersConsent party={detail.data} variant="card" />}
              <PartyForm
                key={detail.data.id}
                party={detail.data}
                onChanged={() => detail.refetch()}
                onDeleted={() => nav("/parties")}
              />
            </>
          )
        ) : (
          <div className="grid h-full place-items-center text-sm text-muted">Party not found.</div>
        )}
        </div>
      </div>
    {showRailPane && !isNew && !selectedId && (
      <button className="btn-primary fixed bottom-[4.5rem] right-4 z-20 h-12 gap-1 rounded-full px-5 shadow-lg md:hidden" onClick={() => nav("/parties/new")}>
        ＋ New party
      </button>
    )}
    </div>
    </div>
  );
}
