import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { HsnOption } from "../lib/types";

/** Searchable HSN lookup. Value is the 8-digit code (or ""). */
export function HsnPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (code: string, gstRate: number | null) => void;
}) {
  const [q, setQ] = useState(value);
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => setQ(value), [value]);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const results = useQuery({
    queryKey: ["hsn", q],
    queryFn: () => api<HsnOption[]>(`/reference/hsn?q=${encodeURIComponent(q)}`),
    enabled: open && q.trim().length >= 2,
  });

  return (
    <div className="relative" ref={boxRef}>
      <input
        className="field font-mono"
        placeholder="code or words…"
        value={q}
        maxLength={8}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
      />
      {open && (results.data?.length ?? 0) > 0 && (
        <div className="absolute z-20 mt-1 max-h-60 w-[min(320px,calc(100vw-2rem))] overflow-auto rounded-md border border-line bg-card shadow-xl">
          {results.data!.map((h) => (
            <button
              key={h.code}
              type="button"
              className="block w-full border-b border-[#f3eee4] px-3 py-2 text-left text-xs last:border-b-0 hover:bg-accent-soft"
              onClick={() => {
                onChange(h.code, h.gst_rate);
                setQ(h.code);
                setOpen(false);
              }}
            >
              <span className="font-mono font-medium">{h.code}</span>
              {h.gst_rate != null && <span className="ml-2 text-muted">GST {h.gst_rate}%</span>}
              <span className="mt-0.5 block truncate text-[11px] text-muted">{h.description}</span>
            </button>
          ))}
        </div>
      )}
      {value && (
        <button
          type="button"
          className="absolute right-2 top-2 text-xs text-muted hover:text-danger"
          onClick={() => {
            onChange("", null);
            setQ("");
          }}
          title="Clear HSN"
        >
          ×
        </button>
      )}
    </div>
  );
}
