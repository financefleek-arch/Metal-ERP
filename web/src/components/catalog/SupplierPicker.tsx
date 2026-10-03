import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import { useDebounced } from "../../lib/useDebounced";
import { isDup409, type PartyListItem, type PartyMatchRef } from "../../lib/types";
import { SimilarParties } from "../SimilarParties";

export interface SupplierChoice {
  id: string;
  name: string;
}

/**
 * Pick the supplier a catalog comes from (or add one). The supplier is what lets a later PDF
 * from the same supplier find the same products, with the same codes, again.
 */
export function SupplierPicker({
  value,
  onPick,
  disabled,
  id,
}: {
  id?: string;
  value: SupplierChoice | null;
  onPick: (s: SupplierChoice | null) => void;
  disabled?: boolean;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [dup, setDup] = useState<PartyMatchRef[] | null>(null);
  const debQ = useDebounced(q.trim(), 250);

  const results = useQuery({
    queryKey: ["supplier-search", debQ],
    queryFn: () => api<PartyListItem[]>(`/parties?q=${encodeURIComponent(debQ)}`),
    enabled: open && debQ.length >= 2,
  });

  function choose(p: { id: string; legal_name: string; role?: string }) {
    // a customer-only party used as a supplier becomes "both", so it is found either way
    if (p.role === "customer") {
      api(`/parties/${p.id}`, { method: "PATCH", body: { role: "both" } }).catch(() => {});
    }
    onPick({ id: p.id, name: p.legal_name });
    setOpen(false);
    setQ("");
    setDup(null);
    setErr(null);
  }

  const create = useMutation<PartyListItem, unknown, boolean>({
    mutationFn: (force) =>
      api<PartyListItem>(`/parties${force ? "?force=true" : ""}`, {
        method: "POST",
        body: { legal_name: q.trim(), role: "supplier" },
      }),
    onSuccess: (p) => choose(p),
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409 && isDup409(e.detail)) {
        const list = e.detail.match ? [e.detail.match] : e.detail.candidates;
        setDup(list);
        setErr(e.detail.message);
      } else {
        setErr(e instanceof ApiError ? e.message : "Could not add the supplier.");
      }
    },
  });

  if (value && !open) {
    return (
      <div className="flex items-center justify-between gap-2 rounded-md border border-line bg-card px-3 py-2 text-sm">
        <span className="truncate font-medium">{value.name}</span>
        {!disabled && (
          <button type="button" className="text-xs text-accent hover:underline" onClick={() => onPick(null)}>
            Change
          </button>
        )}
      </div>
    );
  }

  const typed = q.trim();
  const noMatch = debQ.length >= 2 && !results.isFetching && (results.data?.length ?? 0) === 0;

  return (
    <div className="relative">
      <input
        id={id}
        className="field"
        placeholder="Search or add a supplier…"
        aria-label="Supplier"
        disabled={disabled}
        value={q}
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
          setErr(null);
          setDup(null);
        }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && (results.data?.length ?? 0) > 0 && (
        <div className="absolute z-20 mt-1 max-h-64 w-full overflow-y-auto rounded-md border border-line bg-card text-left shadow-lg">
          {results.data!.map((p) => (
            <button
              key={p.id}
              type="button"
              className="block w-full border-b border-[#f3eee4] px-3 py-2 text-left text-sm hover:bg-accent-soft"
              onMouseDown={() => choose(p)}
            >
              <span className="font-medium">{p.legal_name}</span>
              {p.role !== "customer" && (
                <span className="ml-2 rounded-sm bg-[#f1e7d6] px-1 text-[9px] font-bold uppercase text-warn">
                  supplier
                </span>
              )}
            </button>
          ))}
        </div>
      )}
      {open && typed.length >= 2 && (noMatch || (results.data?.length ?? 0) > 0) && (
        <div className="absolute z-20 mt-1 w-full rounded-md border border-line bg-card text-left shadow-lg"
          style={{ top: (results.data?.length ?? 0) > 0 ? undefined : "100%" }}
        >
          {noMatch && <div className="px-3 pt-2 text-[11px] text-muted">No matching party.</div>}
          {noMatch && (
            <button
              type="button"
              className="block w-full px-3 py-2 text-left text-sm text-accent hover:bg-accent-soft"
              onMouseDown={() => create.mutate(false)}
            >
              + Add “{typed}” as a new supplier
            </button>
          )}
        </div>
      )}
      {!open && typed.length >= 3 && !value && (
        <div className="mt-2">
          <SimilarParties name={debQ} onUse={(p) => choose(p)} />
        </div>
      )}
      {err && (
        <div className="mt-2 text-left" role="alert">
          <p className="err">{err}</p>
          {dup && dup.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-2">
              {dup.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  className="rounded-md border border-line px-2 py-1 text-xs hover:bg-accent-soft"
                  onClick={() => choose({ id: m.id, legal_name: m.legal_name })}
                >
                  Use {m.legal_name}
                </button>
              ))}
              {dup.length > 0 && !create.isPending && (
                <button
                  type="button"
                  className="rounded-md border border-line px-2 py-1 text-xs text-muted hover:bg-ground"
                  onClick={() => create.mutate(true)}
                >
                  It's a different supplier: add “{typed}”
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
