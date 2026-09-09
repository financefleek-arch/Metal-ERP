import { useState } from "react";
import { api, ApiError } from "../lib/api";
import { downloadFile } from "../lib/download";
import { normalizePhone } from "../lib/reference";

/** Period presets for a party account statement (F3b). "This FY" is the
 *  Indian Apr–Mar year, hard-wired server-side. */
type Period = "this_month" | "last_month" | "this_fy" | "last_90" | "custom";

const PERIODS: { key: Period; label: string }[] = [
  { key: "this_month", label: "This month" },
  { key: "last_month", label: "Last month" },
  { key: "this_fy", label: "This FY (Apr–Mar)" },
  { key: "last_90", label: "Last 90 days" },
  { key: "custom", label: "Custom range…" },
];

/**
 * Download or WhatsApp a party's account statement for a chosen period.
 * Used from the party Account tab and from the Collections "Send statement"
 * row action. Opens on `period` (default "this_month"); the recipient
 * picker mirrors the invoice-send dialog (party phone + other number +
 * "save this number" prompt).
 */
export function StatementSendDialog({
  partyId,
  partyName,
  partyPhone,
  onClose,
}: {
  partyId: string;
  partyName: string;
  partyPhone: string | null;
  onClose: () => void;
}) {
  const [period, setPeriod] = useState<Period>("this_month");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [toParty, setToParty] = useState(!!partyPhone);
  const [otherPhone, setOtherPhone] = useState("");
  const [otherTouched, setOtherTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved">("idle");

  const customIncomplete = period === "custom" && (!from || !to);
  const otherNorm = normalizePhone(otherPhone);
  const otherValid = otherNorm !== null;

  const targets: { label: string; phone: string }[] = [];
  if (toParty && partyPhone) targets.push({ label: partyName, phone: partyPhone });
  if (otherValid) targets.push({ label: "Other number", phone: otherNorm });

  const canSend = targets.length > 0 && !busy && !customIncomplete;
  const done = result?.ok === true;

  const offerSave =
    otherValid &&
    result?.ok === true &&
    saveState !== "saved" &&
    normalizePhone(partyPhone ?? "") !== otherNorm;

  function periodQuery(): string {
    const p = new URLSearchParams({ period });
    if (period === "custom") {
      p.set("from", from);
      p.set("to", to);
    }
    return p.toString();
  }

  async function download() {
    setBusy(true);
    setResult(null);
    try {
      await downloadFile(
        `/parties/${partyId}/statement.pdf?${periodQuery()}`,
        `${partyName} statement.pdf`,
      );
    } catch {
      setResult({ ok: false, msg: "Could not generate the statement." });
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    setBusy(true);
    setResult(null);
    let anyOk = false;
    let lastErr = "";
    for (const t of targets) {
      try {
        const body: Record<string, unknown> = { period, to_phone: t.phone };
        if (period === "custom") {
          body.from = from;
          body.to = to;
        }
        await api(`/parties/${partyId}/statement/whatsapp`, { method: "POST", body });
        anyOk = true;
      } catch (e) {
        lastErr = e instanceof ApiError ? e.message : "send failed";
      }
    }
    setBusy(false);
    setResult(
      anyOk
        ? { ok: true, msg: `Statement sent to ${targets.length === 1 ? targets[0].label : `${targets.length} numbers`}.` }
        : { ok: false, msg: lastErr || "Send failed." },
    );
  }

  async function saveToParty() {
    if (!otherNorm) return;
    setSaveState("saving");
    try {
      await api(`/parties/${partyId}`, { method: "PATCH", body: { phone: otherNorm } });
      setSaveState("saved");
    } catch {
      setSaveState("idle");
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-xl bg-card p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="font-serif text-lg font-semibold">Send statement — {partyName}</h2>

        <label className="label mt-4">Period</label>
        <select
          className="field"
          value={period}
          onChange={(e) => setPeriod(e.target.value as Period)}
        >
          {PERIODS.map((p) => (
            <option key={p.key} value={p.key}>
              {p.label}
            </option>
          ))}
        </select>
        {period === "custom" && (
          <div className="mt-2 flex gap-2">
            <input
              type="date"
              className="field flex-1"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
            />
            <input
              type="date"
              className="field flex-1"
              value={to}
              onChange={(e) => setTo(e.target.value)}
            />
          </div>
        )}

        {partyPhone && (
          <label className="mt-4 flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={toParty}
              onChange={(e) => setToParty(e.target.checked)}
            />
            <span>
              Send to {partyName} <span className="text-muted">({partyPhone})</span>
            </span>
          </label>
        )}

        <label className="label mt-4">
          {partyPhone ? "Also send to another number" : "Recipient WhatsApp number"}
        </label>
        <input
          className="field"
          value={otherPhone}
          onChange={(e) => setOtherPhone(e.target.value)}
          onBlur={() => {
            setOtherTouched(true);
            if (otherNorm && otherNorm !== otherPhone) setOtherPhone(otherNorm);
          }}
          placeholder="98765 43210"
          inputMode="tel"
        />
        {otherTouched && otherPhone.trim() && !otherValid && (
          <p className="err mt-1">Enter a 10-digit mobile, or +&lt;country code&gt;&lt;number&gt;.</p>
        )}

        {result && (
          <p className={`mt-3 text-xs ${result.ok ? "text-[#3f7a4f]" : "text-danger"}`}>
            {result.msg}
          </p>
        )}

        {offerSave && (
          <div className="mt-3 flex items-center justify-between gap-2 rounded-md bg-[#f0f6f8] px-3 py-2 text-xs">
            <span>
              Save <span className="font-medium">{otherNorm}</span> to {partyName}?
            </span>
            <button
              className="btn-ghost h-7 shrink-0 px-3 text-xs"
              disabled={saveState === "saving"}
              onClick={saveToParty}
            >
              {saveState === "saving" ? "Saving…" : "Save"}
            </button>
          </div>
        )}
        {saveState === "saved" && (
          <p className="mt-2 text-xs text-[#3f7a4f]">Saved to {partyName}.</p>
        )}

        <div className="mt-5 flex items-center justify-between gap-2">
          <button
            className="btn-ghost h-9 px-3 text-sm"
            disabled={busy || customIncomplete}
            onClick={download}
          >
            Download
          </button>
          <div className="flex gap-2">
            <button className="btn-ghost h-9 px-4 text-sm" onClick={onClose}>
              {done ? "Close" : "Cancel"}
            </button>
            {!done && (
              <button
                className="btn-primary h-9 px-4 text-sm"
                disabled={!canSend}
                onClick={send}
              >
                {busy ? "Working…" : "Send"}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
