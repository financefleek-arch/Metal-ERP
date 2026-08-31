import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import type { Party, PartyListItem, PartyRole } from "../lib/types";
import { PartyDrawer } from "../components/PartyDrawer";

const ROLE_FILTERS: { key: "" | PartyRole; label: string }[] = [
  { key: "", label: "All" },
  { key: "customer", label: "Customers" },
  { key: "supplier", label: "Suppliers" },
  { key: "both", label: "Both" },
];

export function PartiesPage() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [role, setRole] = useState<"" | PartyRole>("");
  const [editing, setEditing] = useState<Party | "new" | null>(null);

  const params = new URLSearchParams();
  if (q.trim()) params.set("q", q.trim());
  if (role) params.set("role", role);
  const qs = params.toString();

  const { data, isLoading } = useQuery({
    queryKey: ["parties", qs],
    queryFn: () => api<PartyListItem[]>(`/parties${qs ? `?${qs}` : ""}`),
  });

  const del = useMutation({
    mutationFn: (id: string) => api<void>(`/parties/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["parties"] }),
  });

  const openEdit = async (id: string) => {
    setEditing(await api<Party>(`/parties/${id}`));
  };

  return (
    <div className="max-w-4xl">
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="font-serif text-2xl font-semibold">Parties</h1>
        <button className="btn-primary" onClick={() => setEditing("new")}>
          + New party
        </button>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <input
          className="field max-w-xs"
          placeholder="Search by name…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        {ROLE_FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setRole(f.key)}
            className={`rounded-full border px-3 py-1 text-[11px] ${
              role === f.key
                ? "border-ink bg-ink text-ground"
                : "border-line bg-card text-muted hover:bg-ground"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="card overflow-hidden">
        <div className="grid grid-cols-[1fr_110px_140px_90px_70px] gap-3 bg-[#efe9df] px-4 py-2.5 text-[10px] uppercase tracking-wide text-muted">
          <span>Name</span>
          <span>Role</span>
          <span>Phone</span>
          <span>State</span>
          <span />
        </div>

        {isLoading && <div className="px-4 py-6 text-sm text-muted">Loading…</div>}
        {!isLoading && data?.length === 0 && (
          <div className="px-4 py-8 text-center text-sm text-muted">
            No parties yet. Add your first one.
          </div>
        )}
        {data?.map((p) => (
          <div
            key={p.id}
            className="grid grid-cols-[1fr_110px_140px_90px_70px] items-center gap-3 border-t border-[#f3eee4] px-4 py-3 text-sm"
          >
            <button
              className="text-left font-medium hover:text-accent"
              onClick={() => void openEdit(p.id)}
            >
              {p.legal_name}
            </button>
            <span className="capitalize text-muted">{p.role}</span>
            <span className="text-muted">{p.phone ?? "—"}</span>
            <span className="text-muted">{p.default_state_code ?? "—"}</span>
            <button
              className="text-right text-xs text-danger hover:underline"
              onClick={() => {
                if (confirm(`Delete "${p.legal_name}"?`)) del.mutate(p.id);
              }}
            >
              Delete
            </button>
          </div>
        ))}
      </div>

      {del.isError && (
        <p className="err mt-2">
          {del.error instanceof ApiError ? del.error.message : "Delete failed"}
        </p>
      )}

      {editing && (
        <PartyDrawer
          party={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            qc.invalidateQueries({ queryKey: ["parties"] });
          }}
        />
      )}
    </div>
  );
}
