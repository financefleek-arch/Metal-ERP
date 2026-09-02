/**
 * The teal action bar that replaces the list header while a bulk selection is
 * live. Shows the count, the three bulk actions, an optional "select all N"
 * (extend the page selection to the whole filtered result), and Clear.
 */
export function SelectionBar({
  count,
  totalAvailable,
  onEditFields,
  onMoveCategory,
  onDelete,
  onSelectAll,
  onClear,
}: {
  count: number;
  /** rows currently loaded by the search+filter; enables "select all N" */
  totalAvailable: number;
  onEditFields: () => void;
  onMoveCategory: () => void;
  onDelete: () => void;
  onSelectAll: () => void;
  onClear: () => void;
}) {
  const canSelectAll = totalAvailable > count;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 bg-accent px-3 py-2 text-ground">
      <span className="text-xs font-semibold tabular-nums">{count} selected</span>
      <div className="flex flex-wrap gap-1.5">
        <button
          className="rounded-md bg-ground px-2.5 py-1 text-[11px] font-semibold text-accent-dark"
          onClick={onEditFields}
        >
          Edit fields
        </button>
        <button
          className="rounded-md border border-ground/40 bg-ground/10 px-2.5 py-1 text-[11px]"
          onClick={onMoveCategory}
        >
          Category / group
        </button>
        <button
          className="rounded-md border border-ground/40 bg-ground/10 px-2.5 py-1 text-[11px]"
          onClick={onDelete}
        >
          Delete…
        </button>
      </div>
      <div className="ml-auto flex items-center gap-3 text-[11px]">
        {canSelectAll && (
          <button className="underline underline-offset-2 opacity-90" onClick={onSelectAll}>
            Select all {totalAvailable}
          </button>
        )}
        <button className="underline underline-offset-2 opacity-90" onClick={onClear}>
          Clear
        </button>
      </div>
    </div>
  );
}
