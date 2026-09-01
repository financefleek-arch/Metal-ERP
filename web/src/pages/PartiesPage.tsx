import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { lastSeenLabel, missingLabel } from "../lib/format";
import type { Party, PartyListItem, PartyRole, PartyStatus } from "../lib/types";
import { PartyForm } from "../components/PartyForm";
import { NewPartyForm } from "../components/NewPartyForm";

type Scope = "" | PartyRole | "incomplete" | "archived";

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

function buildQuery(q: string, scope: Scope) {
  const p = new URLSearchParams();
  if (q.trim()) p.set("q", q.trim());
  if (scope === "customer" || scope === "supplier" || scope === "both") p.set("role", scope);
  if (scope === "incomplete") p.set("completeness", "incomplete");
  if (scope === "archived") p.set("status", "archived" satisfies PartyStatus);
  return p.toString();
}

export function PartiesPage() {
  const nav = useNavigate();
  const { id } = useParams();
  const isNew = id === "new";
  const selectedId = isNew ? null : (id ?? null);

  const [q, setQ] = useState("");
  const [scope, setScope] = useState<Scope>("");

  const list = useQuery({
    queryKey: ["parties", q, scope],
    queryFn: () => api<PartyListItem[]>(`/parties?${buildQuery(q, scope)}`),
  });

  const detail = useQuery({
    queryKey: ["party", selectedId],
    queryFn: () => api<Party>(`/parties/${selectedId}`),
    enabled: !!selectedId,
  });

  // If the selected id vanishes from a fresh list (deleted/archived-out), clear it.
  useEffect(() => {
    if (selectedId && list.data && !list.data.some((p) => p.id === selectedId) && detail.isError) {
      nav("/parties", { replace: true });
    }
  }, [selectedId, list.data, detail.isError, nav]);

  return (
    <div className="mx-auto flex h-full max-w-5xl gap-0 overflow-hidden rounded-xl border border-line bg-card">
      {/* rail */}
      <div className="flex w-[300px] shrink-0 flex-col border-r border-line bg-ground">
        <div className="flex flex-col gap-2 border-b border-line p-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold">Parties</span>
            <button className="btn-primary h-7 px-3 text-xs" onClick={() => nav("/parties/new")}>
              + New
            </button>
          </div>
          <input
            className="field h-8 text-xs"
            placeholder="search name, address, phone…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <div className="flex flex-wrap gap-1">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => setScope(f.key)}
                className={`rounded-full border px-2.5 py-0.5 text-[10px] ${
                  scope === f.key
                    ? "border-ink bg-ink text-ground"
                    : "border-line bg-card text-muted hover:bg-ground"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {isNew && (
            <div className="border-b border-[#f3eee4] bg-card px-3 py-2 shadow-[inset_2px_0_0_theme(colors.accent.DEFAULT)]">
              <div className="text-[11px] font-medium text-accent">New party — unsaved</div>
              <div className="text-[10px] text-muted">fill name to save</div>
            </div>
          )}
          {list.isLoading && <div className="px-3 py-6 text-xs text-muted">Loading…</div>}
          {!list.isLoading && list.data?.length === 0 && !isNew && (
            <div className="px-3 py-8 text-center text-xs text-muted">
              {q || scope ? "No matches." : "No parties yet."}
            </div>
          )}
          {list.data?.map((p) => (
            <button
              key={p.id}
              onClick={() => nav(`/parties/${p.id}`)}
              className={`block w-full border-b border-[#f3eee4] px-3 py-2 text-left ${
                p.id === selectedId
                  ? "bg-card shadow-[inset_2px_0_0_theme(colors.accent.DEFAULT)]"
                  : "hover:bg-accent-soft"
              } ${p.status === "archived" ? "opacity-55" : ""}`}
            >
              <div className="flex items-center gap-1.5 text-xs">
                <span className="rounded-sm bg-ground px-1 py-0.5 text-[8px] font-bold uppercase text-muted">
                  {roleTag(p.role)}
                </span>
                <span className="font-medium">{p.legal_name}</span>
                {!p.completeness.complete && (
                  <span className="ml-auto rounded-sm bg-[#f1e7d6] px-1 py-0.5 text-[8px] font-bold uppercase text-warn">
                    details pending
                  </span>
                )}
              </div>
              <div className="mt-0.5 text-[10px] text-muted">
                {p.source === "inward_bill" && <span className="text-accent">◆ from inward bill · </span>}
                {p.completeness.complete
                  ? lastSeenLabel(p.last_txn_at)
                  : `missing: ${missingLabel(p.completeness.missing)}`}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* detail */}
      <div className="flex-1 overflow-y-auto p-5">
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
          <PartyForm
            key={detail.data.id}
            party={detail.data}
            onChanged={() => detail.refetch()}
            onDeleted={() => nav("/parties")}
          />
        ) : (
          <div className="grid h-full place-items-center text-sm text-muted">Party not found.</div>
        )}
      </div>
    </div>
  );
}
