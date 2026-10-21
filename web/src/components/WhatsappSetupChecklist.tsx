import { setupSteps, type Mark, type TemplateCounts } from "../lib/whatsappSetup";

const MARK: Record<Mark, { glyph: string; tone: string }> = {
  done: { glyph: "✓", tone: "text-ok" },
  waiting: { glyph: "◐", tone: "text-warn" },
  problem: { glyph: "!", tone: "text-danger" },
  todo: { glyph: "○", tone: "text-muted" },
};

export function WhatsappSetupChecklist({
  registered,
  templates,
}: {
  registered: boolean;
  templates: TemplateCounts | null | undefined;
}) {
  return (
    <ul className="mt-3 max-w-prose divide-y divide-line text-xs" aria-label="Setup progress">
      {setupSteps(registered, templates).map((s) => (
        <li key={s.title} className="flex gap-2 py-1.5">
          <span className={`w-4 shrink-0 text-center font-semibold ${MARK[s.mark].tone}`} aria-hidden>
            {MARK[s.mark].glyph}
          </span>
          <span>
            <span className="text-ink">{s.title}</span>
            {s.hint && <span className="block text-muted">{s.hint}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}
