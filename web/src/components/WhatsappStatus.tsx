/** WhatsApp send-status UI: a compact badge for the invoice list row, and a
 *  message-log panel for the invoice detail page. Both read
 *  `whatsapp_message` rows the send + the delivery webhook keep current. */

import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { InvoiceWhatsappMessage, WhatsappStatus } from "../lib/types";

// ---- shared presentation ------------------------------------------------

type Look = { label: string; ticks: string; cls: string };

const LOOK: Record<WhatsappStatus, Look> = {
  pending: { label: "Sending", ticks: "·", cls: "text-muted" },
  sent: { label: "Sent", ticks: "✓", cls: "text-muted" },
  delivered: { label: "Delivered", ticks: "✓✓", cls: "text-[#25a566]" },
  read: { label: "Read", ticks: "✓✓", cls: "text-[#25a566]" },
  failed: { label: "Failed", ticks: "⚠", cls: "text-danger" },
};

function WaGlyph({ status }: { status: WhatsappStatus }) {
  if (status === "failed") {
    return (
      <svg viewBox="0 0 24 24" fill="currentColor" className="h-3.5 w-3.5 shrink-0">
        <path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm1 15h-2v-2h2Zm0-4h-2V7h2Z" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className="h-3.5 w-3.5 shrink-0">
      <path d="M12 2a10 10 0 0 0-8.7 15l-1.2 4.4 4.5-1.2A10 10 0 1 0 12 2Zm0 18a8 8 0 0 1-4.1-1.1l-.3-.2-2.7.7.7-2.6-.2-.3A8 8 0 1 1 12 20Zm4.4-6c-.2-.1-1.4-.7-1.6-.8s-.4-.1-.6.1-.6.8-.8 1-.3.2-.5.1a6.5 6.5 0 0 1-1.9-1.2 7.2 7.2 0 0 1-1.4-1.7c-.1-.2 0-.4.1-.5l.4-.5c.1-.1.1-.3.2-.4s0-.3-.1-.4l-.7-1.7c-.2-.4-.3-.4-.5-.4H8a.9.9 0 0 0-.7.3A2.8 2.8 0 0 0 6.4 9a4.8 4.8 0 0 0 1 2.6 11 11 0 0 0 4.3 3.8c2.5 1 2.5.7 3 .6a2.4 2.4 0 0 0 1.6-1.1 2 2 0 0 0 .1-1.1c-.1-.1-.3-.2-.5-.3Z" />
    </svg>
  );
}

function timeShort(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
}

// ---- list-row badge ---------------------------------------------------

/** One glyph + word for the invoice list "WhatsApp" column. `null` => not sent. */
export function WhatsappBadge({ status }: { status: WhatsappStatus | null }) {
  if (!status) {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] text-[#b7b1a4]">
        <span className="opacity-50">
          <WaGlyph status="sent" />
        </span>
        Not sent
      </span>
    );
  }
  const look = LOOK[status];
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap text-[11px] font-semibold ${look.cls}`}
      title={`WhatsApp: ${look.label.toLowerCase()}`}
    >
      <WaGlyph status={status} />
      <span className="tracking-[-1px]">{look.ticks}</span> {look.label}
    </span>
  );
}

// ---- detail-page log panel ------------------------------------------

/** Per-invoice WhatsApp send history + a resend affordance. Renders nothing
 *  until the invoice has at least one message. Polls while any row is still
 *  in flight so delivery/read land without a manual refresh. */
export function WhatsappLog({
  invoiceId,
  onResend,
}: {
  invoiceId: string;
  onResend?: () => void;
}) {
  const q = useQuery({
    queryKey: ["invoice-whatsapp", invoiceId],
    queryFn: () => api<InvoiceWhatsappMessage[]>(`/invoices/${invoiceId}/whatsapp`),
    refetchInterval: (query) => {
      const rows = query.state.data ?? [];
      return rows.some((r) => r.status === "pending" || r.status === "sent")
        ? 15_000
        : false;
    },
  });

  const rows = q.data ?? [];
  if (rows.length === 0) return null;

  return (
    <div className="card p-4">
      <div className="flex items-baseline justify-between">
        <h3 className="font-serif text-sm font-semibold">WhatsApp</h3>
        {onResend && (
          <button className="btn-ghost h-8 px-3 text-xs" onClick={onResend}>
            Send again
          </button>
        )}
      </div>
      <ul className="mt-2 divide-y divide-line">
        {rows.map((m) => {
          const look = LOOK[m.status];
          const timeline = [
            m.sent_at && `sent ${timeShort(m.sent_at)}`,
            m.delivered_at && `delivered ${timeShort(m.delivered_at)}`,
            m.read_at && `read ${timeShort(m.read_at)}`,
          ]
            .filter(Boolean)
            .join(" · ");
          return (
            <li
              key={m.id}
              className="flex items-start justify-between gap-3 py-2 text-xs"
            >
              <div className="min-w-0">
                <div className="font-medium">{m.to_phone}</div>
                <div className="mt-0.5 text-[11px] text-muted">
                  {m.status === "failed"
                    ? m.error || "delivery failed"
                    : timeline || "queued"}
                </div>
              </div>
              <span
                className={`inline-flex shrink-0 items-center gap-1 font-semibold ${look.cls}`}
              >
                <WaGlyph status={m.status} />
                <span className="tracking-[-1px]">{look.ticks}</span> {look.label}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
