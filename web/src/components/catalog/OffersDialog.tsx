import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import type { CatalogSelection } from "../../lib/catalog";

interface Preview {
  selected: number;
  offered: number;
  with_photo: number;
  without_photo: number;
  template: { ref: string; title: string; cards: number } | null;
  cards: { item_id: string; name: string; price: string; photo_url: string | null }[];
  audience: { agreed: number; not_asked: number; opted_out: number };
  allowance: { cap: number; used: number } | null;
  approx_cost: number;
  ready: boolean;
  blockers: string[];
}

interface Sent {
  id: string;
  state: string;
  recipients: number;
  approx_cost: number;
}

interface Progress {
  campaign?: { state?: string; recipients?: number };
  state?: string;
  steps?: { whatsapp?: { sent?: number; failed?: number } };
}

const money = (v: number | string) =>
  Number(v).toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: 2 });

const defaultTitle = () =>
  `New range, ${new Date().toLocaleDateString("en-IN", { month: "long", year: "numeric" })}`;

/**
 * Send the picked items to customers who agreed, as a WhatsApp message with a swipeable row of
 * item cards. One screen: every choice is already made from what the shop has (best sellers with
 * photos as the cards, customers who agreed as the audience). The shop only decides whether to
 * send. Fleek's backend does the sending from the shop's own number.
 */
export function OffersDialog({
  selection,
  onClose,
}: {
  selection: CatalogSelection;
  onClose: () => void;
}) {
  const [title, setTitle] = useState(defaultTitle);
  const [pieces, setPieces] = useState(false);
  const [sent, setSent] = useState<Sent | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const preview = useQuery({
    queryKey: ["offer-preview", JSON.stringify(selection)],
    queryFn: () => api<Preview>("/offers/preview", { method: "POST", body: { selection } }),
    refetchOnWindowFocus: false,
    retry: false,
  });

  const send = useMutation({
    mutationFn: () =>
      api<Sent>("/offers/send", {
        method: "POST",
        body: { selection, title: title.trim() || defaultTitle(), allow_pieces: pieces },
      }),
    onSuccess: (s) => {
      setErr(null);
      setSent(s);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "That did not work. Try again."),
  });

  const progress = useQuery({
    queryKey: ["offer-progress", sent?.id],
    queryFn: () => api<Progress>(`/offers/${sent?.id}`),
    enabled: !!sent?.id,
    refetchInterval: 5000,
  });
  const stop = useMutation({
    mutationFn: () => api(`/offers/${sent?.id}/cancel`, { method: "POST" }),
    onSuccess: () => progress.refetch(),
    onError: (e) => setErr(e instanceof ApiError ? e.message : "That did not work. Try again."),
  });

  const p = preview.data;
  const agreed = p?.audience.agreed ?? 0;
  const left = p?.allowance ? Math.max(0, p.allowance.cap - p.allowance.used) : null;
  const state = progress.data?.campaign?.state ?? progress.data?.state ?? sent?.state;
  const done = progress.data?.steps?.whatsapp?.sent ?? 0;

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/40 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="of-title"
        className="card w-full max-w-lg bg-card p-5 shadow-xl"
      >
        <div className="flex items-start justify-between gap-3">
          <h2 id="of-title" className="font-serif text-xl font-semibold">
            Send to customers on WhatsApp
          </h2>
          <button type="button" className="btn-ghost h-9 px-3" onClick={onClose}>
            Close
          </button>
        </div>

        {sent ? (
          <div className="mt-4">
            <p className="font-medium">
              {state === "cancelled"
                ? "Stopped."
                : state === "completed"
                  ? "Sent."
                  : `Sending to ${sent.recipients} customer${sent.recipients === 1 ? "" : "s"}.`}
            </p>
            <p className="mt-1 text-sm text-muted tabular-nums">
              {done} of {sent.recipients} sent so far. It goes out in small batches, so it can take a
              while. Orders show up under Orders.
            </p>
            {err && <p className="err">{err}</p>}
            <div className="mt-4 flex justify-end gap-2">
              {state !== "cancelled" && state !== "completed" && (
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={stop.isPending}
                  onClick={() => stop.mutate()}
                >
                  Stop sending
                </button>
              )}
              <button type="button" className="btn-primary" onClick={onClose}>
                Done
              </button>
            </div>
          </div>
        ) : preview.isLoading ? (
          <p className="mt-4 text-sm text-muted">Checking what can be sent…</p>
        ) : preview.isError || !p ? (
          <p className="err mt-4" role="alert">
            {preview.error instanceof ApiError ? preview.error.message : "Could not check. Try again."}
          </p>
        ) : (
          <>
            <section className="mt-4 border-t border-line pt-3">
              <div className="flex items-baseline justify-between text-sm">
                <h3 className="font-semibold">Items</h3>
                <span className="text-xs text-muted tabular-nums">
                  {p.selected} picked · {p.with_photo} with a photo
                </span>
              </div>
              {p.offered < p.selected && (
                <p className="mt-1 text-xs text-muted">
                  {p.selected - p.offered} are not shown to customers (no price, or not in stock).
                </p>
              )}
              {p.without_photo > 0 && (
                <p className="mt-1 text-xs text-muted">
                  {p.without_photo} without a photo stay in the catalogue but get no card.
                </p>
              )}
            </section>

            <section className="mt-3 border-t border-line pt-3">
              <h3 className="text-sm font-semibold">Cards in the message</h3>
              {p.cards.length === 0 ? (
                <p className="mt-1 text-xs text-muted">No cards yet.</p>
              ) : (
                <ul className="mt-2 flex gap-2 overflow-x-auto pb-1">
                  {p.cards.map((c) => (
                    <li key={c.item_id} className="w-24 shrink-0 overflow-hidden rounded-lg border border-line">
                      <div className="h-20 bg-[#efe9df]">
                        {c.photo_url && (
                          <img src={c.photo_url} alt="" className="h-full w-full object-cover" />
                        )}
                      </div>
                      <p className="line-clamp-2 px-1.5 pt-1 text-[11px] leading-snug">{c.name}</p>
                      <p className="px-1.5 pb-1.5 text-[11px] font-semibold tabular-nums">₹{money(c.price)}</p>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-1 text-xs text-muted">Best sellers with photos, picked for you.</p>
            </section>

            <section className="mt-3 border-t border-line pt-3">
              <h3 className="text-sm font-semibold">Who gets it</h3>
              <p className="mt-1 text-sm">
                <b className="tabular-nums">{agreed}</b> customer{agreed === 1 ? "" : "s"} agreed to offers
                on WhatsApp.
              </p>
              {p.audience.not_asked > 0 && (
                <p className="text-xs text-muted">
                  {p.audience.not_asked} more have not been asked yet and are not sent to.{" "}
                  <Link to="/parties?offers=not_asked" className="text-accent underline">
                    Ask them
                  </Link>
                </p>
              )}
              <p className="mt-1 text-xs text-muted tabular-nums">
                About {agreed} × ₹0.86 = <b className="text-ink">about ₹{money(p.approx_cost)}</b>, billed
                to your WhatsApp account by Meta.
                {left !== null && ` ${left} offer messages left this month.`}
              </p>
            </section>

            <section className="mt-3 border-t border-line pt-3">
              <label className="label" htmlFor="of-name">
                Name shown to customers
              </label>
              <input
                id="of-name"
                className="field"
                maxLength={120}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
              <label className="mt-2 flex items-center gap-2 text-sm">
                <input type="checkbox" checked={pieces} onChange={(e) => setPieces(e.target.checked)} />
                Customers can also order loose pieces
              </label>
            </section>

            {p.blockers.length > 0 && (
              <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-danger" role="alert">
                {p.blockers.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            )}
            {err && (
              <p className="err" role="alert">
                {err}
              </p>
            )}

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className="btn-ghost" onClick={onClose}>
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={!p.ready || send.isPending}
                onClick={() => send.mutate()}
              >
                {send.isPending ? "Sending…" : `Send to ${agreed} customer${agreed === 1 ? "" : "s"}`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
