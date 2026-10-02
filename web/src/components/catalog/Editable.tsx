import { useEffect, useState } from "react";
import { validMultiplier } from "../../lib/catalog";

/** A text cell that saves on blur or Enter, and reverts on Escape. */
export function EditableText({
  value,
  onSave,
  label,
  className = "",
  placeholder,
  list,
  disabled,
}: {
  value: string;
  onSave: (v: string) => void;
  label: string;
  className?: string;
  placeholder?: string;
  /** id of a <datalist> for suggestions. */
  list?: string;
  disabled?: boolean;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);

  function commit() {
    const next = draft.trim();
    if (next === value.trim()) {
      setDraft(value);
      return;
    }
    onSave(next);
  }

  return (
    <input
      aria-label={label}
      className={`w-full rounded border border-transparent bg-transparent px-1.5 py-1 text-sm hover:border-line focus:border-accent focus:bg-card focus:outline-none focus:ring-2 focus:ring-accent/25 disabled:opacity-60 ${className}`}
      value={draft}
      placeholder={placeholder}
      list={list}
      disabled={disabled}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") {
          setDraft(value);
          (e.target as HTMLInputElement).blur();
        }
      }}
    />
  );
}

/** A number cell. `kind` decides validation: whole pack counts, money, or a multiplier.
 *  With `allowEmpty`, clearing the cell calls `onSave("")` (the caller treats it as "none"). */
export function EditableNumber({
  value,
  onSave,
  label,
  kind,
  className = "",
  placeholder,
  allowEmpty = false,
}: {
  value: string;
  onSave: (v: string) => void;
  label: string;
  kind: "int" | "money" | "mult";
  className?: string;
  placeholder?: string;
  allowEmpty?: boolean;
}) {
  const [draft, setDraft] = useState(value);
  const [bad, setBad] = useState(false);
  useEffect(() => {
    setDraft(value);
    setBad(false);
  }, [value]);

  const valid = (s: string) => {
    if (kind === "int") return /^[1-9]\d{0,4}$/.test(s);
    if (kind === "mult") return validMultiplier(s);
    return /^\d{1,13}(\.\d{1,2})?$/.test(s);
  };

  function commit() {
    const next = draft.trim();
    if (next === value) return;
    if (next === "" && allowEmpty) {
      setBad(false);
      onSave("");
      return;
    }
    if (!valid(next)) {
      setBad(true);
      return;
    }
    setBad(false);
    onSave(next);
  }

  return (
    <input
      aria-label={label}
      aria-invalid={bad}
      placeholder={placeholder}
      inputMode={kind === "int" ? "numeric" : "decimal"}
      className={`rounded border bg-transparent px-1.5 py-1 text-right text-sm tabular-nums hover:border-line focus:border-accent focus:bg-card focus:outline-none focus:ring-2 focus:ring-accent/25 ${
        bad ? "border-danger" : "border-transparent"
      } ${className}`}
      value={draft}
      onChange={(e) => {
        setDraft(e.target.value);
        setBad(false);
      }}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") {
          setDraft(value);
          setBad(false);
          (e.target as HTMLInputElement).blur();
        }
      }}
    />
  );
}
