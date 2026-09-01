import { useRef, useState } from "react";

/**
 * Dev-only quick tester: pick a supplier PDF, get the Tally XML back (or the
 * parsed JSON) without going through upload / review / approve. Talks to the
 * unauthenticated /api/inward-debug endpoints (mounted only when APP_ENV !=
 * production). Handy for iterating on a real Tally import.
 */

interface DebugExtract {
  supplier_name: string | null;
  supplier_gstin: string | null;
  bill_no: string | null;
  bill_date: string | null;
  place_of_supply_state_code: string | null;
  supply_type: string | null;
  totals: Record<string, string>;
  reconciled: boolean;
  reconcile_discrepancy: string;
  lines: {
    sl_no: number;
    description: string;
    hsn: string | null;
    quantity: string;
    uom: string | null;
    unit_rate: string;
    line_total: string;
  }[];
}

export function InwardDebugPage() {
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [json, setJson] = useState<DebugExtract | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(kind: "extract" | "xml") {
    if (!file) return;
    setErr(null);
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`/api/inward-debug/${kind}`, { method: "POST", body: fd });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b.detail ?? res.statusText);
      }
      if (kind === "extract") {
        setJson(await res.json());
      } else {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `inward-${json?.bill_no ?? "debug"}.xml`;
        a.click();
        URL.revokeObjectURL(url);
        setJson((j) => j); // keep the panel
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="mb-1 font-serif text-xl font-semibold">Inward · Debug</h1>
      <p className="mb-4 text-sm text-muted">
        Dev tool. Runs the real extractor + reconciliation + Tally XML builder on a PDF.
        Nothing is saved. Every line is treated as a new stock item — what a first import
        into a fresh Tally company needs.
      </p>

      <div className="card flex flex-wrap items-center gap-3 p-4">
        <input
          ref={fileRef}
          type="file"
          accept="application/pdf"
          className="hidden"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setJson(null);
          }}
        />
        <button className="btn-ghost" onClick={() => fileRef.current?.click()}>
          Choose PDF
        </button>
        <span className="min-w-0 flex-1 truncate text-sm text-muted">{file?.name ?? "no file"}</span>
        <div className="flex w-full gap-2 sm:ml-auto sm:w-auto">
          <button
            className="btn-ghost"
            disabled={!file || busy}
            onClick={() => run("extract")}
          >
            Extract → JSON
          </button>
          <button
            className="btn-primary"
            disabled={!file || busy}
            onClick={() => run("xml")}
          >
            Download Tally XML
          </button>
        </div>
      </div>

      {err && <p className="err mt-3">{err}</p>}

      {json && (
        <div className="card mt-4 p-4 text-sm">
          <div className="mb-3 flex flex-wrap gap-x-6 gap-y-1">
            <span>
              <b>{json.supplier_name}</b>{" "}
              <span className="font-mono text-xs text-muted">{json.supplier_gstin}</span>
            </span>
            <span className="font-mono text-xs">
              {json.bill_no} · {json.bill_date}
            </span>
            <span className="text-xs">
              {json.supply_type} · POS {json.place_of_supply_state_code}
            </span>
            <span
              className={`rounded-full px-2 py-0.5 text-[11px] ${
                json.reconciled ? "bg-[#e6efe8] text-ok" : "bg-[#f4e3df] text-danger"
              }`}
            >
              {json.reconciled
                ? "reconciled ✓"
                : `off by ${json.reconcile_discrepancy}`}
            </span>
          </div>
          <div className="mb-3 grid grid-cols-2 gap-2 font-mono text-xs sm:grid-cols-3">
            {Object.entries(json.totals).map(([k, v]) => (
              <div key={k} className="rounded bg-ground px-2 py-1">
                {k}: {v}
              </div>
            ))}
          </div>
          <div className="overflow-x-auto">
          <table className="w-full min-w-[32rem] text-xs">
            <thead className="bg-ground text-[10px] uppercase text-muted">
              <tr>
                <th className="p-1 text-left">#</th>
                <th className="p-1 text-left">Description</th>
                <th className="p-1 text-left">HSN</th>
                <th className="p-1 text-right">Qty</th>
                <th className="p-1 text-right">Rate</th>
                <th className="p-1 text-right">Amount</th>
              </tr>
            </thead>
            <tbody>
              {json.lines.map((ln) => (
                <tr key={ln.sl_no} className="border-t border-line">
                  <td className="p-1">{ln.sl_no}</td>
                  <td className="p-1">{ln.description}</td>
                  <td className="p-1 font-mono">{ln.hsn}</td>
                  <td className="p-1 text-right">{ln.quantity}</td>
                  <td className="p-1 text-right font-mono">{ln.unit_rate}</td>
                  <td className="p-1 text-right font-mono">{ln.line_total}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </div>
      )}
    </div>
  );
}
