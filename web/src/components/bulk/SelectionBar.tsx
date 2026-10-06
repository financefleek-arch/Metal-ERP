/**
 * The teal action bar that replaces the list header while a bulk selection is
 * live. Shows the count, the bulk actions, an optional "select all N matching"
 * (extend the selection to everything the current filter matches, not only the
 * rows loaded on screen) and Clear.
 */
export function SelectionBar({
  count,
  matching,
  allMatching,
  onEditFields,
  onRename,
  onSetAvailability,
  onMoveCategory,
  onDelete,
  onMakeCatalog,
  onPrintLabels,
  onShareLink,
  onSendToTally,
  onSelectAllMatching,
  onClear,
}: {
  count: number;
  /** how many items the current filter matches (null while unknown) */
  matching: number | null;
  /** true once the selection is "everything matching the filter" */
  allMatching: boolean;
  onEditFields: () => void;
  onRename: () => void;
  onSetAvailability: () => void;
  onMoveCategory: () => void;
  onDelete: () => void;
  /** present only when the firm has the catalog module */
  onMakeCatalog?: () => void;
  onPrintLabels?: () => void;
  onShareLink?: () => void;
  onSendToTally?: () => void;
  onSelectAllMatching: () => void;
  onClear: () => void;
}) {
  const canSelectAll = !allMatching && matching != null && matching > count;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 bg-accent px-3 py-2 text-ground">
      <span className="text-xs font-semibold tabular-nums">
        {allMatching && matching != null ? `All ${matching} matching` : `${count} selected`}
      </span>
      <div className="flex flex-wrap gap-1.5">
        <button
          className="rounded-md bg-ground px-2.5 py-1 text-[11px] font-semibold text-accent-dark"
          onClick={onSetAvailability}
        >
          Set availability
        </button>
        <button
          className="rounded-md border border-ground/40 bg-ground/10 px-2.5 py-1 text-[11px]"
          onClick={onEditFields}
        >
          Edit fields
        </button>
        <button
          className="rounded-md border border-ground/40 bg-ground/10 px-2.5 py-1 text-[11px]"
          onClick={onRename}
        >
          Rename (find / replace)
        </button>
        <button
          className="rounded-md border border-ground/40 bg-ground/10 px-2.5 py-1 text-[11px]"
          onClick={onMoveCategory}
        >
          Category / group
        </button>
        {onShareLink && (
          <button
            className="rounded-md border border-ground/40 bg-ground/10 px-2.5 py-1 text-[11px]"
            onClick={onShareLink}
          >
            Share catalog link
          </button>
        )}
        {onSendToTally && (
          <button
            className="rounded-md border border-ground/40 bg-ground/10 px-2.5 py-1 text-[11px]"
            onClick={onSendToTally}
          >
            Send to Tally
          </button>
        )}
        {onPrintLabels && (
          <button
            className="rounded-md border border-ground/40 bg-ground/10 px-2.5 py-1 text-[11px]"
            onClick={onPrintLabels}
          >
            Print labels
          </button>
        )}
        {onMakeCatalog && (
          <button
            className="rounded-md border border-ground/40 bg-ground/10 px-2.5 py-1 text-[11px]"
            onClick={onMakeCatalog}
          >
            Make customer catalog
          </button>
        )}
        <button
          className="rounded-md border border-ground/40 bg-ground/10 px-2.5 py-1 text-[11px]"
          onClick={onDelete}
        >
          Delete…
        </button>
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
