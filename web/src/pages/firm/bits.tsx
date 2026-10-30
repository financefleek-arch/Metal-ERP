export function Hint({ children, tone }: { children: React.ReactNode; tone?: "ok" | "bad" }) {
  const cls = tone === "bad" ? "text-danger" : tone === "ok" ? "text-ok" : "text-muted";
  return <p className={`mt-1 text-xs ${cls}`}>{children}</p>;
}

export function Pill({ children, tone }: { children: React.ReactNode; tone?: "need" | "gst" | "auto" }) {
  const cls =
    tone === "need"
      ? "border-accent bg-accent-soft text-accent-deep"
      : tone === "gst"
        ? "border-brass bg-brass-soft text-brass-dark"
        : tone === "auto"
          ? "border-dashed border-line text-muted"
          : "border-line";
  return <span className={`rounded-full border px-2.5 py-0.5 text-xs ${cls}`}>{children}</span>;
}
