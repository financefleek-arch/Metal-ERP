import { useRef, useState } from "react";
import { readUpiQr, type UpiQr } from "../lib/upiQr";

/** "Upload your payment QR": a screenshot or photo of the QR from a payment app. The UPI ID is read out of it, so there is
 *  nothing to type. The parent keeps the result; this only reads the picture. */
export function UpiQrPicker({
  has,
  onRead,
  onRemove,
}: {
  has: boolean;
  onRead: (got: UpiQr) => void;
  onRemove: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    setMsg(null);
    try {
      const got = await readUpiQr(file);
      if (!got) {
        setMsg({ ok: false, text: "We could not find a UPI QR in that picture. Try a clearer photo, or a screenshot of the QR from your payment app." });
      } else {
        onRead(got);
        setMsg({ ok: true, text: `Read your UPI ID: ${got.upiId}` });
      }
    } catch {
      setMsg({ ok: false, text: "That file could not be opened as a picture." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <input ref={input} type="file" accept="image/*" className="hidden" onChange={onFile} aria-label="Upload your payment QR" />
      {has ? (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-ok/40 bg-accent-soft px-3 py-2.5">
          <span className="min-w-0 flex-1 text-sm">
            <b className="text-ok">✓ Your QR is added.</b> Bills print it with the bill amount filled in.
          </span>
          <button type="button" className="btn" onClick={() => input.current?.click()} disabled={busy}>
            Replace
          </button>
          <button type="button" className="btn" onClick={onRemove}>
            Remove
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => input.current?.click()}
          disabled={busy}
          className="flex w-full items-center gap-3 rounded-lg border-[1.5px] border-dashed border-accent bg-accent-soft px-4 py-3.5 text-left"
        >
          <span className="min-w-0 flex-1">
            <span className="block font-semibold text-accent-deep">{busy ? "Reading your QR…" : "Upload your payment QR"}</span>
            <span className="block text-sm text-muted">
              A screenshot or photo of the QR from PhonePe, Google Pay, Paytm or your bank app. We read your UPI ID from it.
            </span>
          </span>
          <span className="text-xl text-accent" aria-hidden>
            +
          </span>
        </button>
      )}
      {msg && <p className={`mt-2 text-sm ${msg.ok ? "text-ok" : "text-danger"}`}>{msg.text}</p>}
    </div>
  );
}
