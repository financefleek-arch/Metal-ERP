import { useEffect, useState } from "react";

/** A six-digit one-time code. One input (not six boxes) so the phone can paste and auto-fill it from a message. */
export function CodeField({
  label,
  hint,
  value,
  onChange,
  autoFocus,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (v: string) => void;
  autoFocus?: boolean;
}) {
  return (
    <div>
      <label className="label">
        {label}
        {hint && <span className="ml-1 font-normal normal-case tracking-normal">{hint}</span>}
      </label>
      <input
        className="field text-center font-mono text-xl tracking-[0.5em] md:text-base"
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={6}
        autoFocus={autoFocus}
        value={value}
        onChange={(e) => onChange(e.target.value.replace(/\D/g, "").slice(0, 6))}
        placeholder="••••••"
      />
    </div>
  );
}

/** "Resend" after the one-minute wait the server enforces. */
export function ResendLink({ onResend, seconds = 60 }: { onResend: () => Promise<void>; seconds?: number }) {
  const [left, setLeft] = useState(seconds);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (left <= 0) return;
    const t = window.setTimeout(() => setLeft((s) => s - 1), 1000);
    return () => window.clearTimeout(t);
  }, [left]);
  if (left > 0) return <span className="meta">Resend in 0:{String(left).padStart(2, "0")}</span>;
  return (
    <button
      type="button"
      disabled={busy}
      className="text-xs text-accent hover:text-accent-dark disabled:opacity-50"
      onClick={async () => {
        setBusy(true);
        try {
          await onResend();
          setLeft(seconds);
        } finally {
          setBusy(false);
        }
      }}
    >
      Resend
    </button>
  );
}
