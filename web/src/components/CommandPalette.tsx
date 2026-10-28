import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { inr } from "../lib/previewTotal";
import { useDebounced } from "../lib/useDebounced";
import { useDialogKeys } from "../lib/keyboard";
import type { InvoiceListItem, PartyListItem } from "../lib/types";

interface Entry {
  key: string;
  group: string;
  label: string;
  hint?: string;
  to: string;
}

/** Ctrl+K: jump to a page, start something new, or find a party / invoice. */
export function CommandPalette({
  pages,
  showParties,
  onClose,
}: {
  pages: { to: string; label: string }[];
  showParties: boolean;
  onClose: () => void;
}) {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [hi, setHi] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const dq = useDebounced(q.trim(), 200);
  useDialogKeys(onClose);

  const lookup = dq.length >= 2;
  const parties = useQuery({
    queryKey: ["palette-parties", dq],
    queryFn: () => api<PartyListItem[]>(`/parties?q=${encodeURIComponent(dq)}`),
    enabled: lookup && showParties,
  });
  const invoices = useQuery({
    queryKey: ["palette-invoices", dq],
    queryFn: () => api<InvoiceListItem[]>(`/invoices?q=${encodeURIComponent(dq)}`),
    enabled: lookup,
  });

  const entries = useMemo<Entry[]>(() => {
    const low = q.trim().toLowerCase();
    const match = (s: string) => !low || s.toLowerCase().includes(low);
    const out: Entry[] = [];
    const actions = [
      { to: "/invoices/new", label: "New invoice", hint: "N" },
      ...(showParties ? [{ to: "/parties/new", label: "New party", hint: "" }] : []),
    ];
    for (const a of actions) {
      if (match(a.label)) out.push({ key: a.to, group: "Do", label: a.label, hint: a.hint, to: a.to });
    }
    for (const p of pages) {
      if (match(p.label)) out.push({ key: p.to, group: "Go to", label: p.label, to: p.to });
    }
    for (const p of (parties.data ?? []).slice(0, 6)) {
      out.push({ key: `p${p.id}`, group: "Parties", label: p.legal_name, hint: p.phone ?? "", to: `/parties/${p.id}` });
    }
    for (const iv of (invoices.data ?? []).slice(0, 6)) {
      out.push({
        key: `i${iv.id}`,
        group: "Invoices",
        label: `${iv.number != null ? `#${iv.number}` : "Draft"} · ${iv.party_name}`,
        hint: iv.grand_total ? inr(iv.grand_total) : "",
        to: `/invoices/${iv.id}`,
      });
    }
    return out;
  }, [q, pages, showParties, parties.data, invoices.data]);

  // results changed under the cursor: never point past the end
  useEffect(() => setHi((h) => Math.min(h, Math.max(entries.length - 1, 0))), [entries.length]);
  useEffect(() => {
    listRef.current?.querySelector(`[data-hi="${hi}"]`)?.scrollIntoView({ block: "nearest" });
  }, [hi]);

  function go(e: Entry | undefined) {
    if (!e) return;
    onClose();
    nav(e.to);
  }

  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center bg-black/40 p-4 pt-[12vh]"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Go to"
        className="w-full max-w-lg overflow-hidden rounded-xl border border-line bg-card shadow-xl"
      >
        <input
          autoFocus
          className="w-full border-b border-line bg-card px-4 py-3 text-sm outline-none"
          placeholder="Go to a page, or find a party or invoice…"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setHi(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setHi((h) => Math.min(h + 1, entries.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setHi((h) => Math.max(h - 1, 0));
            } else if (e.key === "Enter") {
              e.preventDefault();
              go(entries[hi]);
            }
          }}
        />
        <div ref={listRef} className="max-h-[50vh] overflow-y-auto py-1">
          {entries.length === 0 && (
            <p className="px-4 py-6 text-center text-xs text-muted">
              {lookup && (parties.isFetching || invoices.isFetching) ? "Searching…" : "Nothing found."}
            </p>
          )}
          {entries.map((e, i) => (
            <button
              key={e.key}
              type="button"
              tabIndex={-1}
              data-hi={i}
              className={`flex w-full items-center justify-between gap-3 px-4 py-2 text-left text-sm ${
                i === hi ? "bg-accent-soft" : "hover:bg-ground"
              }`}
              onMouseMove={() => setHi(i)}
              onClick={() => go(e)}
            >
              <span className="min-w-0 truncate">
                <span className="mr-2 inline-block w-14 text-[11px] font-semibold uppercase tracking-wide text-muted">
                  {i === 0 || entries[i - 1].group !== e.group ? e.group : ""}
                </span>
                {e.label}
              </span>
              {e.hint && <span className="shrink-0 text-[11px] text-muted">{e.hint}</span>}
            </button>
          ))}
        </div>
        <div className="border-t border-line px-4 py-1.5 text-[11px] text-muted">
          ↑↓ choose · Enter open · Esc close
        </div>
      </div>
    </div>
  );
}

const HELP: { keys: string; what: string }[] = [
  { keys: "Ctrl+K", what: "Go to a page, party or invoice" },
  { keys: "N", what: "New invoice" },
  { keys: "G then S / C / P / I / O / D / F", what: "Go to Sales / Collections / Parties / Items / Orders / Documents / Firm" },
  { keys: "/", what: "Search the list on this page" },
  { keys: "↑ ↓  Enter", what: "Move through a list, open the row" },
  { keys: "Enter (on an invoice line)", what: "Next field; after Rate, the next line" },
  { keys: "Enter on an empty item", what: "Done with lines — on to Finalize" },
  { keys: "Ctrl+Enter (item)", what: "Use what you typed as a new item" },
  { keys: "Ctrl+S", what: "Save draft" },
  { keys: "Alt+P", what: "Cycle payment on finalize / record payment" },
  { keys: "Alt+1…4, Alt+F (payment)", what: "Cash / UPI / Bank / Cheque, pay in full" },
  { keys: "Esc", what: "Close a dialog" },
  { keys: "?", what: "This list" },
];

/** `?`: every keyboard shortcut in one place. */
export function ShortcutHelp({ onClose }: { onClose: () => void }) {
  useDialogKeys(onClose);
  return (
    <div
      className="fixed inset-0 z-[70] grid place-items-center bg-black/40 p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Keyboard shortcuts"
        className="w-full max-w-lg rounded-xl border border-line bg-card p-5 shadow-xl"
      >
        <h2 className="font-serif text-base font-semibold">Keyboard shortcuts</h2>
        <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
          {HELP.map((h) => (
            <div key={h.keys} className="contents">
              <dt className="whitespace-nowrap font-mono text-xs text-accent">{h.keys}</dt>
              <dd className="text-muted">{h.what}</dd>
            </div>
          ))}
        </dl>
        <div className="mt-4 text-right">
          <button className="btn-ghost h-8 px-3 text-xs" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
