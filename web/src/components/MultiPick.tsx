import { useEffect, useMemo, useRef, useState } from "react";

export type PickOption = { value: string; label: string; hint?: string };

/**
 * A dropdown of tick-boxes with a search box, for picking several of many (categories, groups).
 * A bottom sheet on a phone.
 */
export function MultiPick({
  label,
  options,
  value,
  onChange,
  allLabel,
}: {
  label: string;
  options: PickOption[];
  value: string[];
  onChange: (v: string[]) => void;
  /** shown on the button when nothing is picked */
  allLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  const shown = useMemo(() => {
    const t = q.trim().toLowerCase();
    return t ? options.filter((o) => o.label.toLowerCase().includes(t)) : options;
  }, [options, q]);

  const picked = new Set(value);
  const toggle = (v: string) => onChange(picked.has(v) ? value.filter((x) => x !== v) : [...value, v]);
  const first = options.find((o) => o.value === value[0])?.label;
  const summary =
    value.length === 0 ? allLabel : value.length === 1 ? (first ?? "1 picked") : `${value.length} picked`;

  return (
    <div ref={ref} className="relative min-w-0 flex-1">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={label}
        onClick={() => setOpen((o) => !o)}
        className={`field flex h-8 w-full items-center justify-between gap-1 text-left text-xs ${
          value.length ? "border-accent bg-accent-soft" : ""
        }`}
      >
        <span className="truncate">{summary}</span>
        <span className="shrink-0 text-[8px] text-faint">▾</span>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30 bg-ink/30 md:hidden" onClick={() => setOpen(false)} />
          <div className="fixed inset-x-0 bottom-0 z-40 flex max-h-[80dvh] flex-col rounded-t-2xl border border-line bg-card pb-3 shadow-xl md:absolute md:inset-x-auto md:bottom-auto md:left-0 md:top-full md:mt-1 md:max-h-72 md:w-64 md:rounded-lg md:pb-0">
            <div className="flex items-center gap-2 border-b border-line p-2">
              <input
                className="field h-8 flex-1 text-xs"
                placeholder={`find a ${label.toLowerCase()}…`}
                value={q}
                autoFocus
                onChange={(e) => setQ(e.target.value)}
              />
              {value.length > 0 && (
                <button className="shrink-0 text-[11px] text-accent underline" onClick={() => onChange([])}>
                  Clear
                </button>
              )}
            </div>
            <div role="listbox" aria-multiselectable="true" className="overflow-y-auto">
              {shown.length === 0 && <p className="px-3 py-3 text-xs text-muted">Nothing matches.</p>}
              {shown.map((o) => (
                <label
                  key={o.value}
                  className="flex cursor-pointer items-center gap-2 border-b border-[#f3eee4] px-3 py-3 text-sm last:border-0 hover:bg-ground md:py-1.5 md:text-xs"
                >
                  <input
                    type="checkbox"
                    className="h-4 w-4 accent-[color:theme(colors.accent.DEFAULT)]"
                    checked={picked.has(o.value)}
                    onChange={() => toggle(o.value)}
                  />
                  <span className="min-w-0 flex-1 truncate">{o.label}</span>
                  {o.hint && <span className="shrink-0 font-mono text-[10px] text-muted">{o.hint}</span>}
                </label>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
