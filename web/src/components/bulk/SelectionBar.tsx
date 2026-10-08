import { useEffect, useRef, useState } from "react";

type Entry = { label: string; onClick: () => void; danger?: boolean } | "sep";

/** A dropdown on a desktop, a bottom sheet on a phone. */
function BarMenu({ label, entries }: { label: string; entries: Entry[] }) {
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
  if (entries.every((e) => e === "sep")) return null;
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        className="rounded-md border border-ground/40 bg-ground/10 px-2.5 py-1 text-[11px]"
        onClick={() => setOpen((o) => !o)}
      >
        {label} ▾
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30 bg-ink/30 md:hidden" onClick={() => setOpen(false)} />
          <div
            role="menu"
            className="fixed inset-x-0 bottom-0 z-40 max-h-[80dvh] overflow-y-auto rounded-t-2xl border border-line bg-card py-2 text-ink shadow-xl md:absolute md:inset-x-auto md:bottom-auto md:left-0 md:top-full md:mt-1 md:w-56 md:rounded-lg md:py-1"
          >
            {entries.map((e, i) =>
              e === "sep" ? (
                <div key={i} className="my-1 border-t border-line" />
              ) : (
                <button
                  key={e.label}
                  role="menuitem"
                  className={`block w-full px-4 py-3 text-left text-sm hover:bg-ground md:px-3 md:py-1.5 md:text-xs ${
                    e.danger ? "text-danger" : ""
                  }`}
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
        </>
      )}
    </div>
  );
}

/**
 * The teal action bar that replaces the list header while a bulk selection is live. Shows what is
 * selected, the common action up front and the rest grouped in three menus, an optional "select
 * all N matching" and Clear.
 */
export function SelectionBar({
  count,
  label,
  matching,
  allMatching,
  showRestore,
  onEditFields,
  onRename,
  onSetAvailability,
  onMoveCategory,
  onChangePrice,
  onConfirm,
  onArchive,
  onRestore,
  onDelete,
  onMakeCatalog,
  onPrintLabels,
  onShareLink,
  onSendToTally,
  onSelectAllMatching,
  onClear,
}: {
  count: number;
  /** a whole tree node is selected: say which, instead of a count of ticks */
  label?: string | null;
  /** how many items the current filter matches (null while unknown) */
  matching: number | null;
  /** true once the selection is "everything matching the filter" */
  allMatching: boolean;
  /** the Archived list is showing, so offer Restore instead of Archive */
  showRestore?: boolean;
  onEditFields: () => void;
  onRename: () => void;
  onSetAvailability: () => void;
  onMoveCategory: () => void;
  onChangePrice: () => void;
  onConfirm: () => void;
  onArchive: () => void;
  onRestore: () => void;
  onDelete: () => void;
  /** present only when the firm has the catalog module */
  onMakeCatalog?: () => void;
  onPrintLabels?: () => void;
  onShareLink?: () => void;
  onSendToTally?: () => void;
  onSelectAllMatching: () => void;
  onClear: () => void;
}) {
  const canSelectAll = !label && !allMatching && matching != null && matching > count;
  const share: Entry[] = [
    ...(onMakeCatalog ? [{ label: "Make customer catalog", onClick: onMakeCatalog }] : []),
    ...(onShareLink ? [{ label: "Share catalog link", onClick: onShareLink }] : []),
    ...(onPrintLabels ? [{ label: "Print labels", onClick: onPrintLabels }] : []),
    ...(onSendToTally ? [{ label: "Send to Tally", onClick: onSendToTally }] : []),
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 bg-accent px-3 py-2 text-ground">
      <span className="text-xs font-semibold tabular-nums">
        {label
          ? `${label} · ${count} item${count === 1 ? "" : "s"}`
          : allMatching && matching != null
            ? `All ${matching} matching`
            : `${count} selected`}
      </span>
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          className="rounded-md bg-ground px-2.5 py-1 text-[11px] font-semibold text-accent-dark"
          onClick={onSetAvailability}
        >
          Set availability
        </button>
        <BarMenu
          label="Change"
          entries={[
            { label: "Change price…", onClick: onChangePrice },
            { label: "Edit fields…", onClick: onEditFields },
            { label: "Rename (find / replace)", onClick: onRename },
            { label: "Category / group…", onClick: onMoveCategory },
            "sep",
            { label: "Confirm", onClick: onConfirm },
            showRestore
              ? { label: "Restore from archive", onClick: onRestore }
              : { label: "Archive", onClick: onArchive },
          ]}
        />
        {share.length > 0 && <BarMenu label="Share" entries={share} />}
        <BarMenu label="More" entries={[{ label: "Delete…", onClick: onDelete, danger: true }]} />
      </div>
      <div className="ml-auto flex items-center gap-3 text-[11px]">
        {canSelectAll && (
          <button className="underline underline-offset-2 opacity-90" onClick={onSelectAllMatching}>
            Select all {matching} matching
          </button>
        )}
        <button className="underline underline-offset-2 opacity-90" onClick={onClear}>
          Clear
        </button>
      </div>
    </div>
  );
}
