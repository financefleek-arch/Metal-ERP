import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";

/** A4 at 96 dpi: the size the server lays the bill out at. */
const PAGE_W = 794;
const PAGE_H = 1123;

/** The firm's bill exactly as it prints. The server renders the same template the PDF is made from, with a
 *  sample sale and the details currently typed on the page, so nothing here imitates the bill. */
export function BillPreviewFrame({ body }: { body: Record<string, unknown> }) {
  const [html, setHtml] = useState("");
  const [failed, setFailed] = useState(false);
  const [width, setWidth] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const latest = useRef(0);
  const key = JSON.stringify(body);

  useEffect(() => {
    const id = window.setTimeout(() => {
      const n = ++latest.current;
      api<{ html: string }>("/tenant/bill-preview", { method: "POST", body: JSON.parse(key) })
        .then((r) => {
          if (n !== latest.current) return; // a newer edit is already on its way
          setHtml(r.html);
          setFailed(false);
        })
        .catch(() => n === latest.current && setFailed(true));
    }, 350);
    return () => window.clearTimeout(id);
  }, [key]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setWidth(el.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const scale = width ? Math.min(1, width / PAGE_W) : 0;
  return (
    <div ref={box} className="w-full">
      <div
        className="relative overflow-hidden rounded-md border border-line bg-white shadow-sm"
        style={{ height: scale ? PAGE_H * scale : 320 }}
      >
        {html && scale > 0 && (
          <iframe
            title="Your bill as it prints"
            sandbox=""
            srcDoc={html}
            tabIndex={-1}
            style={{
              width: PAGE_W,
              height: PAGE_H,
              border: 0,
              transform: `scale(${scale})`,
              transformOrigin: "top left",
              pointerEvents: "none",
            }}
          />
        )}
        {!html && !failed && <p className="p-4 text-sm text-muted">Drawing your bill…</p>}
        {failed && <p className="p-4 text-sm text-danger">The preview could not load. Your details are still saved when you save.</p>}
      </div>
    </div>
  );
}

/** The same bill, full width, for reading the small print. */
export function BillPreviewModal({ body, onClose }: { body: Record<string, unknown>; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-ink/60" role="dialog" aria-modal="true" aria-label="Your bill">
      <div className="flex items-center justify-between bg-ground px-4 py-3">
        <div>
          <div className="font-serif text-base font-semibold">Your bill</div>
          <div className="text-xs text-muted">Sample items, your details.</div>
        </div>
        <button type="button" className="btn" onClick={onClose}>
          Close
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        <div className="mx-auto" style={{ maxWidth: PAGE_W }}>
          <BillPreviewFrame body={body} />
        </div>
      </div>
    </div>
  );
}
