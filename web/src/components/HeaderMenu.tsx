import { useState } from "react";

export type MenuEntry = { label: string; onClick: () => void } | "sep";

/** A small header dropdown: "Import ▾", "Photos ▾". */
export function HeaderMenu({ label, entries, caret = true, className = "btn-ghost" }: { label: string; entries: MenuEntry[]; caret?: boolean; className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        className={className}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
      >
        {label}{caret && " ▾"}
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-20 mt-1 w-max min-w-[220px] max-w-[min(20rem,calc(100vw-2rem))] overflow-hidden rounded-lg border border-line bg-card text-sm shadow-xl"
        >
          {entries.map((e, i) =>
            e === "sep" ? (
              <div key={i} className="border-t border-line" />
            ) : (
              <button
                key={e.label}
                role="menuitem"
                className="block w-full whitespace-nowrap px-4 py-3 text-left hover:bg-ground md:px-3 md:py-2"
                onMouseDown={(ev) => ev.preventDefault()}
                onClick={() => {
                  setOpen(false);
                  e.onClick();
                }}
              >
                {e.label}
              </button>
            ),
          )}
        </div>
      )}
    </div>
  );
}
