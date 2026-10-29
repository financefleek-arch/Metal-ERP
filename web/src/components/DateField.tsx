import { useEffect, useState } from "react";
import { useIsDesktop } from "../lib/useIsDesktop";

const pad = (n: number) => String(n).padStart(2, "0");
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const show = (v: string) => (v ? `${v.slice(8, 10)}/${v.slice(5, 7)}/${v.slice(0, 4)}` : "");

function fromIso(v: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v);
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
}

/** A real, existing calendar date or null (31/02 is not a date). */
function make(y: number, m: number, d: number): Date | null {
  const dt = new Date(y, m - 1, d);
  return dt.getFullYear() === y && dt.getMonth() === m - 1 && dt.getDate() === d ? dt : null;
}

/**
 * Tally-style date typing, relative to `base` (the date already in the field):
 *   5  →  the 5th of base's month        5 10 / 5/10  →  5 Oct of base's year
 *   5/10/26, 051026, 05102026, 5-10-2026   t / today   +3 / -2  →  days from base
 * Returns an ISO date or null when it is not a date.
 */
function parseTypedDate(text: string, base: Date): string | null {
  const t = text.trim().toLowerCase();
  if (!t) return null;
  if (t === "t" || t === "today") return iso(new Date());
  const rel = /^([+-])\s*(\d{1,4})$/.exec(t);
  if (rel) {
    const d = new Date(base);
    d.setDate(d.getDate() + (rel[1] === "-" ? -1 : 1) * Number(rel[2]));
    return iso(d);
  }
  let parts = t.split(/[^0-9]+/).filter(Boolean);
  if (parts.length === 1 && [4, 6, 8].includes(parts[0].length)) {
    const s = parts[0];
    parts = [s.slice(0, 2), s.slice(2, 4), ...(s.length > 4 ? [s.slice(4)] : [])];
  }
  if (!parts.length || parts.length > 3) return null;
  const d = Number(parts[0]);
  const m = parts[1] ? Number(parts[1]) : base.getMonth() + 1;
  let y = parts[2] ? Number(parts[2]) : base.getFullYear();
  if (parts[2] && parts[2].length <= 2) y += 2000;
  const dt = make(y, m, d);
  return dt ? iso(dt) : null;
}

/**
 * A date box you can type into: "5" = the 5th of this month, "t" = today,
 * ↑/↓ = a day either way. Shows and accepts dd/mm/yyyy; the value it hands
 * back is the same ISO yyyy-mm-dd a `type="date"` input gives.
 */
export function DateField({
  value,
  onChange,
  disabled,
  className = "field",
  id,
  clearable,
}: {
  value: string;
  onChange: (iso: string) => void;
  disabled?: boolean;
  className?: string;
  id?: string;
  /** an optional date: emptying the box clears it */
  clearable?: boolean;
}) {
  const desktop = useIsDesktop();
  const [text, setText] = useState(show(value));
  const [bad, setBad] = useState(false);
  useEffect(() => {
    setText(show(value));
    setBad(false);
  }, [value]);

  // a phone keeps its native date picker
  if (!desktop) {
    return (
      <input
        id={id}
        type="date"
        className={className}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }

  const base = fromIso(value) ?? new Date();

  function commit() {
    const v = parseTypedDate(text, base);
    if (v) {
      setBad(false);
      setText(show(v));
      if (v !== value) onChange(v);
    } else if (text.trim()) {
      setBad(true);
    } else if (clearable) {
      if (value) onChange("");
    } else {
      setText(show(value));
    }
  }

  function step(days: number) {
    const d = new Date(base);
    d.setDate(d.getDate() + days);
    onChange(iso(d));
  }

  return (
    <input
      id={id}
      className={`${className} ${bad ? "border-danger focus:border-danger" : ""}`}
      inputMode="numeric"
      placeholder="dd/mm/yyyy"
      title="Type 5 for the 5th, 5/10 for 5 Oct, t for today, ↑ ↓ for a day either way"
      value={text}
      disabled={disabled}
      onFocus={(e) => e.currentTarget.select()}
      onChange={(e) => {
        setText(e.target.value);
        setBad(false);
      }}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          commit();
        } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
          e.preventDefault();
          step(e.key === "ArrowUp" ? 1 : -1);
        }
      }}
    />
  );
}
