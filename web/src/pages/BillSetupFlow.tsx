import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { track } from "../lib/analytics";
import { UPI_RE, upiHandleUnknown } from "../lib/upiQr";
import { useIsDesktop } from "../lib/useIsDesktop";
import type { Tenant } from "../lib/types";
import { UpiQrPicker } from "../components/UpiQrPicker";
import { BillPreviewFrame, BillPreviewModal } from "../components/BillPreviewFrame";

type Step = "name" | "place" | "pay" | "done";
const ORDER: Step[] = ["name", "place", "pay", "done"];

const clean = (s: string) => s.trim();
const orNull = (s: string) => clean(s) || null;

/** The first-run setup as a short conversation, with the shop's real bill beside it filling in as each question is
 *  answered. Three questions, all but the first skippable, each saved the moment it is answered. The bill is the
 *  product's own template (see BillPreviewFrame), so what the shop watches is what its customers will get. */
export function BillSetupFlow({ heading }: { heading?: React.ReactNode }) {
  const qc = useQueryClient();
  const { me } = useAuth();
  const isDesktop = useIsDesktop();
  const { data } = useQuery({ queryKey: ["tenant"], queryFn: () => api<Tenant>("/tenant") });

  const [step, setStep] = useState<Step>("name");
  const [done, setDone] = useState<Partial<Record<Step, "saved" | "skipped">>>({});
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [city, setCity] = useState("");
  const [upi, setUpi] = useState("");
  const [qr, setQr] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [full, setFull] = useState(false);

  const seeded = useRef(false);
  useEffect(() => {
    if (!data || seeded.current) return;
    seeded.current = true;
    setName(data.trade_name || data.legal_name || "");
    setAddress(data.address ?? "");
    setCity(data.city ?? "");
    setUpi(data.upi_id ?? "");
    setQr(data.upi_qr_payload ?? "");
  }, [data]);

  // A firm that already has a trade name prints that; the shop name it typed at sign-up is the legal name.
  const nameField = data?.trade_name ? "trade_name" : "legal_name";
  const bodyFor = (s: Step): Record<string, unknown> => {
    if (s === "name") return { [nameField]: clean(name) };
    if (s === "place") return { address: orNull(address), city: orNull(city) };
    if (s === "pay") return { upi_id: orNull(upi), upi_qr_payload: orNull(qr) };
    return {};
  };
  // Everything typed so far, answered or not, so the bill moves while the shop types.
  const preview = {
    ...(clean(name) ? { [nameField]: clean(name) } : {}),
    address: orNull(address),
    city: orNull(city),
    upi_id: orNull(upi),
    upi_qr_payload: orNull(qr),
  };

  const save = useMutation({
    mutationFn: (s: Step) => api<Tenant>("/tenant", { method: "PATCH", body: bodyFor(s) }),
    onSuccess: (t, s) => {
      qc.setQueryData(["tenant"], t);
      qc.invalidateQueries({ queryKey: ["setup"] });
      setDone((d) => ({ ...d, [s]: "saved" }));
      setError(null);
      track(`start-${s}-saved`);
      setStep(ORDER[ORDER.indexOf(s) + 1]);
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not save. Try again."),
  });

  const skip = (s: Step) => {
    setDone((d) => ({ ...d, [s]: "skipped" }));
    setError(null);
    track(`start-${s}-skipped`);
    setStep(ORDER[ORDER.indexOf(s) + 1]);
  };

  const upiShapeBad = clean(upi) !== "" && !UPI_RE.test(clean(upi));
  const place = [clean(city), clean(address)].filter(Boolean)[0];
  const chips: { step: Step; text: string }[] = [
    done.name ? { step: "name" as const, text: clean(name) } : null,
    done.place ? { step: "place" as const, text: done.place === "saved" && place ? place : "No address yet" } : null,
    done.pay ? { step: "pay" as const, text: done.pay === "saved" && clean(upi) ? clean(upi) : "No UPI yet" } : null,
  ].filter((c): c is { step: Step; text: string } => c !== null && c.step !== step);

  const days = me?.pdf_days_left ?? null;
  const canImport = me?.features?.import !== false;
  const n = ORDER.indexOf(step) + 1;

  return (
    <div className="mx-auto max-w-5xl">
      {heading}
      <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] md:items-start">
        <div className="min-w-0 space-y-3" aria-live="polite">
          {chips.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {chips.map((c) => (
                <button
                  key={c.step}
                  type="button"
                  onClick={() => setStep(c.step)}
                  className="max-w-full truncate rounded-full border border-line bg-card px-3 py-1 text-sm"
                  title="Change this"
                >
                  <span className="text-ok">✓</span> {c.text}
                </button>
              ))}
            </div>
          )}

          {step !== "done" && <p className="text-xs uppercase tracking-wide text-muted">Step {n} of 3</p>}

          {step === "name" && (
            <Ask text={`Is this how your shop name should look on your bill?`}>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (clean(name)) save.mutate("name");
                }}
                className="space-y-3"
              >
                <input
                  className="field"
                  aria-label="Shop name"
                  value={name}
                  maxLength={200}
                  autoFocus={isDesktop}
                  onChange={(e) => setName(e.target.value)}
                />
                <Actions>
                  <button type="submit" className="btn-brass" disabled={!clean(name) || save.isPending}>
                    {save.isPending ? "Saving…" : clean(name) === clean(data?.trade_name || data?.legal_name || "") ? "Yes, that is right" : "Use this name"}
                  </button>
                </Actions>
              </form>
            </Ask>
          )}

          {step === "place" && (
            <Ask text="Where is your shop? Customers like to see it on the bill.">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  save.mutate("place");
                }}
                className="space-y-3"
              >
                <textarea
                  className="field h-auto resize-y py-2"
                  rows={2}
                  aria-label="Shop address"
                  placeholder="Shop number, street, area"
                  value={address}
                  autoFocus={isDesktop}
                  onChange={(e) => setAddress(e.target.value)}
                />
                <input className="field" aria-label="City" placeholder="City" value={city} onChange={(e) => setCity(e.target.value)} />
                {data?.phone && <p className="text-xs text-muted">Your mobile {data.phone} prints as the contact number.</p>}
                <Actions>
                  <button type="submit" className="btn-brass" disabled={save.isPending || (!clean(address) && !clean(city))}>
                    {save.isPending ? "Saving…" : "Continue"}
                  </button>
                  <button type="button" className="btn" onClick={() => skip("place")}>
                    Skip for now
                  </button>
                </Actions>
              </form>
            </Ask>
          )}

          {step === "pay" && (
            <Ask text="How do customers pay you? We add a QR to the bill so they can scan and pay.">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!upiShapeBad) save.mutate("pay");
                }}
                className="space-y-3"
              >
                <UpiQrPicker
                  has={Boolean(clean(qr))}
                  onRead={(got) => {
                    setQr(got.payload);
                    setUpi(got.upiId);
                  }}
                  onRemove={() => setQr("")}
                />
                <div>
                  <label className="label" htmlFor="start-upi">
                    {clean(qr) ? "UPI ID" : "Or type your UPI ID"}
                  </label>
                  <input
                    id="start-upi"
                    className={`field ${upiShapeBad ? "border-danger" : ""}`}
                    placeholder="yourshop@okhdfcbank"
                    autoComplete="off"
                    value={upi}
                    onChange={(e) => {
                      setUpi(e.target.value);
                      if (clean(qr)) setQr(""); // a QR names who it pays; a different ID is a different QR
                    }}
                  />
                  {upiShapeBad ? (
                    <p className="err">Use the form name@bank, for example shop@okhdfcbank</p>
                  ) : clean(upi) && upiHandleUnknown(clean(upi)) ? (
                    <p className="mt-1 text-xs text-muted">We do not recognise the part after @. Check it in your UPI app.</p>
                  ) : clean(upi) && !clean(qr) ? (
                    <p className="mt-1 text-xs text-ok">Looks right. We make the QR on your bill from it.</p>
                  ) : null}
                </div>
                <Actions>
                  <button type="submit" className="btn-brass" disabled={save.isPending || upiShapeBad || (!clean(upi) && !clean(qr))}>
                    {save.isPending ? "Saving…" : "Continue"}
                  </button>
                  <button type="button" className="btn" onClick={() => skip("pay")}>
                    Skip for now
                  </button>
                </Actions>
              </form>
            </Ask>
          )}

          {step === "done" && (
            <Ask text="That is your bill, ready to send. Your first one takes about two minutes.">
              <div className="space-y-3">
                <Link to="/invoices/new" className="btn-brass w-full" data-umami-event="welcome-first-invoice">
                  Make my first invoice
                </Link>
                <p className="text-sm text-muted">
                  You are on the Free plan{days && days > 0 ? `, with invoice PDFs free for ${days} ${days === 1 ? "day" : "days"}` : ""}.
                  {done.pay !== "saved" && " Add your UPI any time under Firm, and the QR appears on every bill."}
                </p>
                <p className="text-sm">
                  <Link to="/firm" className="text-accent underline underline-offset-2">
                    Add bank details, GST and more
                  </Link>
                  {canImport && (
                    <>
                      {" · "}
                      <Link to="/parties/import" className="text-accent underline underline-offset-2">
                        Using Tally? Bring in your customers and items
                      </Link>
                    </>
                  )}
                </p>
                <p className="meta">
                  <Link to="/setup" className="underline">
                    See all the steps
                  </Link>{" "}
                  ·{" "}
                  <Link to="/invoices" className="underline">
                    Skip to my invoices
                  </Link>
                </p>
              </div>
            </Ask>
          )}

          {error && (
            <p className="err" role="alert">
              {error}
            </p>
          )}
        </div>

        <aside className="min-w-0" aria-label="Your bill">
          <div className="mb-2 flex items-baseline justify-between gap-2">
            <h2 className="font-serif text-base font-semibold">{step === "done" ? "Your bill" : "Your bill, filling in as you go"}</h2>
            <button type="button" className="text-sm text-accent underline underline-offset-2" onClick={() => setFull(true)}>
              Open full size
            </button>
          </div>
          <BillPreviewFrame body={preview} />
          <p className="mt-2 text-xs text-muted">Sample items. Your details fill in as you answer.</p>
        </aside>
      </div>
      {full && <BillPreviewModal body={preview} onClose={() => setFull(false)} />}
    </div>
  );
}

/** One question, asked like a person would, with the way to answer underneath. */
function Ask({ text, children }: { text: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2.5">
        <span aria-hidden className="grid h-8 w-8 flex-none place-items-center rounded-full bg-accent-deep font-serif text-sm font-semibold text-white">
          TC
        </span>
        <p className="rounded-2xl rounded-tl-sm bg-accent-soft px-4 py-2.5 text-[15px] leading-snug">{text}</p>
      </div>
      <div className="card p-4">{children}</div>
    </div>
  );
}

function Actions({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-wrap items-center gap-2 pt-1">{children}</div>;
}
