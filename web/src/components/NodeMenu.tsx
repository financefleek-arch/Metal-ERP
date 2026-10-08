import { useEffect, useRef, useState } from "react";
import type { NodeAction } from "../lib/itemNodes";

type Entry = { action: NodeAction; label: string; catalog?: boolean };

const BLOCKS: { title: string; entries: Entry[] }[] = [
  {
    title: "Share and sell",
    entries: [
      { action: "catalog", label: "Make customer catalog", catalog: true },
      { action: "share", label: "Share catalog link", catalog: true },
      { action: "labels", label: "Print labels", catalog: true },
      { action: "tally", label: "Send to Tally", catalog: true },
    ],
  },
  {
    title: "Change",
    entries: [
      { action: "availability", label: "Set availability" },
      { action: "price", label: "Change price…" },
      { action: "fields", label: "Edit fields" },
      { action: "move", label: "Move to another group" },
      { action: "rename", label: "Rename (find / replace)" },
      { action: "archive", label: "Archive" },
    ],
  },
  { title: "Get", entries: [{ action: "export", label: "Export to Excel" }] },
];

/**
 * The ⋯ menu on a tree row. A dropdown on a desktop, a bottom sheet on a phone (a hover menu is
 * not reachable with a thumb). `children` adds node-specific entries, e.g. "Open group".
 */
export function NodeMenu({
  title,
  count,
  catalogModule,
  onAction,
  children,
}: {
  title: string;
  count: number;
  catalogModule: boolean;
  onAction: (a: NodeAction) => void;
  children?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
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

  const item = "block w-full whitespace-nowrap px-4 py-3 text-left text-sm hover:bg-ground md:px-3 md:py-1.5 md:text-xs";

  return (
    <div ref={ref} className="relative shrink-0" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Actions for ${title}`}
        className="grid h-9 w-9 place-items-center rounded-md text-base text-muted hover:bg-ground hover:text-ink md:h-6 md:w-6 md:text-sm"
        onClick={() => setOpen((o) => !o)}
      >
        ⋯
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30 bg-ink/30 md:hidden" onClick={() => setOpen(false)} />
          <div
            role="menu"
            className="fixed inset-x-0 bottom-0 z-40 max-h-[80dvh] overflow-y-auto rounded-t-2xl border border-line bg-card pb-4 shadow-xl md:absolute md:inset-x-auto md:bottom-auto md:right-0 md:top-full md:mt-1 md:max-h-[70dvh] md:w-60 md:rounded-lg md:pb-0"
          >
            <div className="border-b border-line px-4 py-2.5 md:px-3 md:py-2">
              <div className="truncate text-xs font-semibold">{title}</div>
              <div className="text-[10px] text-muted">
                {count} item{count === 1 ? "" : "s"}
              </div>
            </div>
            <div onClick={() => setOpen(false)}>{children}</div>
            {BLOCKS.map((b) => {
              const entries = b.entries.filter((e) => !e.catalog || catalogModule);
              if (!entries.length) return null;
              return (
                <div key={b.title} className="border-b border-line last:border-0">
                  <div className="px-4 pb-0.5 pt-2 text-[9px] font-bold uppercase tracking-wide text-faint md:px-3">
                    {b.title}
                  </div>
                  {entries.map((e) => (
                    <button
                      key={e.action}
                      role="menuitem"
                      className={item}
                      onClick={() => {
                        setOpen(false);
                        onAction(e.action);
                      }}
                    >
                      {e.label}
                    </button>
                  ))}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

/** A plain entry for NodeMenu's `children` slot. */
export function NodeMenuEntry({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      role="menuitem"
      className="block w-full whitespace-nowrap border-b border-line px-4 py-3 text-left text-sm hover:bg-ground md:px-3 md:py-1.5 md:text-xs"
      onClick={onClick}
    >
      {label}
    </button>
  );
}
