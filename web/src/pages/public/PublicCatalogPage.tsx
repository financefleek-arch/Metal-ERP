import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";

interface PublicItem {
  id: string;
  name: string;
  code: string;
  group: string;
  price: string;
  /** what one loose piece costs on this link; null for an item sold singly */
  piece_price: string | null;
  pack_qty: number;
  photo_url: string | null;
  thumb_url: string | null;
  tag: string | null;
}
interface PublicCatalog {
  title: string;
  firm_name: string;
  firm_phone: string | null;
  terms_line: string | null;
  min_order: string | null;
  allow_pieces: boolean;
  price_basis: "pack" | "piece";
  /** items per row on a phone */
  columns: 1 | 2 | 3;
  show_code: boolean;
  show_pack_info: boolean;
  groups: string[];
  items: PublicItem[];
  /** present when the page was opened from a customer's personal link */
  customer?: { name: string };
}
interface Placed {
  number: string;
  status_token: string;
  total: string;
  item_count: number;
}

const MAX_QTY = 999;

type Unit = "pack" | "piece";
type CartLine = { qty: number; unit: Unit };
type Cart = Record<string, CartLine>;
type Line = { item: PublicItem; qty: number; unit: Unit };

/** per row on a phone; more on wider screens (class names written out so Tailwind sees them) */
const GRID: Record<number, string> = {
  1: "grid-cols-1 sm:grid-cols-3 md:grid-cols-4",
  2: "grid-cols-2 sm:grid-cols-3 md:grid-cols-4",
  3: "grid-cols-3 sm:grid-cols-4 md:grid-cols-5",
};

const rateOf = (item: PublicItem, unit: Unit) =>
  unit === "piece" && item.piece_price ? Number(item.piece_price) : Number(item.price);
const unitName = (unit: Unit, n: number) => (unit === "piece" ? (n === 1 ? "piece" : "pieces") : n === 1 ? "pack" : "packs");

/** carts saved before pieces existed were {item: packs}; keep them */
function readCart(token: string): Cart {
  const fresh = read<Cart | null>(`cart2:${token}`, null);
  if (fresh) return fresh;
  const old = read<Record<string, number>>(`cart:${token}`, {});
  return Object.fromEntries(Object.entries(old).map(([id, qty]) => [id, { qty, unit: "pack" as Unit }]));
}
const money = (v: string | number) =>
  Number(v).toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: 2 });

// the cart and the customer's details live in this browser only, so a reload or a second visit
// keeps them; they are never sent anywhere until the order is placed
const read = <T,>(key: string, fallback: T): T => {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
};
const write = (key: string, value: unknown) => {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode: the cart simply will not survive a reload */
  }
};

/**
 * The page a customer opens from a shop's link: browse, add whole packs, review, place the order.
 * No login, no app: the token in the address is the key. It only ever receives the public
 * allow-list from the server. Prices are shown for convenience; the server prices the order.
 */
export function PublicCatalogPage() {
  const { token = "" } = useParams();
  // a personal link carries who the customer is (p) and which campaign it came from (c)
  const [search] = useSearchParams();
  const personal = search.get("p") ?? "";
  const campaign = search.get("c") ?? "";
  const [data, setData] = useState<PublicCatalog | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "gone" | "busy" | "error">("loading");
  const [q, setQ] = useState("");
  const [group, setGroup] = useState("All");
  const [zoom, setZoom] = useState<PublicItem | null>(null);
  const [cart, setCart] = useState<Cart>(() => readCart(token));
  // the Pack | Piece choice for items not in the cart yet
  const [pref, setPref] = useState<Record<string, Unit>>({});
  const [step, setStep] = useState<"browse" | "review" | "done">("browse");
  const [placed, setPlaced] = useState<Placed | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const qs = personal ? `?p=${encodeURIComponent(personal)}` : "";
        const res = await fetch(`/api/public/catalog/${encodeURIComponent(token)}${qs}`);
        if (cancelled) return;
        if (res.status === 404) return setState("gone");
        if (res.status === 429) return setState("busy");
        if (!res.ok) return setState("error");
        setData((await res.json()) as PublicCatalog);
        setState("ok");
      } catch {
        if (!cancelled) setState("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, personal]);

  useEffect(() => {
    if (data) document.title = `${data.title} · ${data.firm_name}`;
  }, [data]);

  useEffect(() => write(`cart2:${token}`, cart), [cart, token]);

  const byId = useMemo(() => new Map((data?.items ?? []).map((i) => [i.id, i])), [data]);
  // drop anything the shop no longer offers
  const lines = useMemo(
    () =>
      Object.entries(cart)
        .map(([id, c]) => ({ item: byId.get(id), qty: c.qty, unit: c.unit }))
        .filter((l): l is Line => !!l.item && l.qty > 0),
    [cart, byId],
  );
  const total = lines.reduce((n, l) => n + rateOf(l.item, l.unit) * l.qty, 0);
  const packs = lines.reduce((n, l) => n + l.qty, 0);
  const min = data?.min_order ? Number(data.min_order) : 0;

  const setQty = (id: string, qty: number) =>
    setCart((c) => {
      const next = { ...c };
      const v = Math.max(0, Math.min(MAX_QTY, Math.floor(qty) || 0));
      if (v === 0) delete next[id];
      else next[id] = { qty: v, unit: c[id]?.unit ?? pref[id] ?? "pack" };
      return next;
    });
  const unitOf = (id: string): Unit => cart[id]?.unit ?? pref[id] ?? "pack";
  const setUnit = (id: string, unit: Unit) => {
    setPref((p) => ({ ...p, [id]: unit }));
    setCart((c) => (c[id] ? { ...c, [id]: { ...c[id], unit } } : c));
  };

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (data?.items ?? []).filter(
      (i) =>
        (group === "All" || i.group === group) &&
        (!needle || i.name.toLowerCase().includes(needle) || i.code.toLowerCase().includes(needle)),
    );
  }, [data, q, group]);

  if (state !== "ok" || !data) {
    const msg =
      state === "loading"
        ? "Loading…"
        : state === "gone"
          ? "This link is not available any more. Please ask the shop for a new one."
          : state === "busy"
            ? "Too many requests. Please wait a moment and refresh."
            : "Something went wrong. Please try again.";
    return (
      <div className="grid min-h-[100dvh] place-items-center bg-ground p-6 text-center text-sm text-muted">
        <p role="status">{msg}</p>
      </div>
    );
  }

  if (step === "done" && placed) {
    return (
      <Shell data={data}>
        <div className="mx-auto max-w-md pt-10 text-center">
          <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-[#e6efe8] text-2xl text-ok">✓</div>
          <h2 className="mt-3 font-serif text-2xl font-semibold">Order received</h2>
          <p className="mt-2 text-sm text-muted">
            Order <b className="text-ink">{placed.number}</b> · ₹{money(placed.total)}
            <br />
            {data.firm_name} will confirm it shortly.
          </p>
          <a className="btn-primary mt-5 inline-block" href={`/o/${placed.status_token}`}>
            See order status
          </a>
          <p className="mt-3 text-xs text-muted">Keep this page&apos;s status link to check on your order.</p>
          <button
            type="button"
            className="mt-6 text-sm text-accent underline"
            onClick={() => {
              setStep("browse");
              setPlaced(null);
            }}
          >
            Back to the catalog
          </button>
        </div>
      </Shell>
    );
  }

  if (step === "review") {
    return (
      <Shell data={data}>
        <ReviewOrder
          token={token}
          personal={personal}
          campaign={campaign}
          data={data}
          lines={lines}
          total={total}
          min={min}
          setQty={setQty}
          onBack={() => setStep("browse")}
          onPlaced={(p) => {
            setCart({});
            setPlaced(p);
            setStep("done");
          }}
          onUnavailable={(ids) => {
            ids.forEach((id) => setQty(id, 0));
            setStep("browse");
          }}
        />
      </Shell>
    );
  }

  return (
    <Shell data={data}>
      <h2 className="font-serif text-xl font-semibold">{data.title}</h2>
      <p className="mt-1 text-xs text-muted">
        {data.items.length.toLocaleString("en-IN")} items
        {data.min_order ? ` · Minimum order ₹${money(data.min_order)}` : ""}
      </p>
      {data.terms_line && <p className="mt-1 text-xs text-muted">{data.terms_line}</p>}

      <div className="mt-4">
        <label className="sr-only" htmlFor="pc-search">
          Search items or codes
        </label>
        <input
          id="pc-search"
          type="search"
          className="field"
          placeholder="Search items or codes"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>
      {data.groups.length > 1 && (
        <div className="mt-3 flex gap-1.5 overflow-x-auto pb-1" role="group" aria-label="Groups">
          {["All", ...data.groups].map((g) => (
            <button
              key={g}
              type="button"
              aria-pressed={group === g}
              onClick={() => setGroup(g)}
              className={`shrink-0 whitespace-nowrap rounded-full border px-3 py-1 text-xs ${
                group === g ? "border-accent bg-accent text-ground" : "border-line bg-card text-muted"
              }`}
            >
              {g}
            </button>
          ))}
        </div>
      )}

      {shown.length === 0 ? (
        <p className="mt-10 text-center text-sm text-muted">Nothing matches that.</p>
      ) : (
        <ul className={`mt-4 grid ${GRID[data.columns] ?? GRID[2]} gap-3 pb-24`}>
          {shown.map((i) => {
            const qty = cart[i.id]?.qty ?? 0;
            const unit = unitOf(i.id);
            const canPiece = data.allow_pieces && !!i.piece_price;
            const perPiece = data.price_basis === "piece" && !!i.piece_price;
            const headline = perPiece ? i.piece_price! : i.price;
            const packInfo = i.pack_qty > 1 && data.show_pack_info;
            const meta = [data.show_code && i.code ? i.code : null].filter(Boolean).join(" · ");
            return (
              <li key={i.id} className={`card overflow-hidden bg-card ${qty ? "ring-2 ring-accent" : ""}`}>
                <button
                  type="button"
                  className="block aspect-[4/3] w-full bg-[#efe9df]"
                  aria-label={`View ${i.name}`}
                  onClick={() => i.photo_url && setZoom(i)}
                >
                  {i.thumb_url ? (
                    <img src={i.thumb_url} alt={i.name} loading="lazy" className="h-full w-full object-cover" />
                  ) : (
                    <span className="grid h-full place-items-center text-[11px] text-muted">No photo</span>
                  )}
                </button>
                <div className="p-2.5">
                  <p className="line-clamp-2 text-[13px] font-medium leading-snug">{i.name}</p>
                  {meta && <p className="mt-0.5 text-[11px] text-muted">{meta}</p>}
                  <p className="mt-1 flex items-baseline gap-1.5">
                    <span className="text-sm font-semibold tabular-nums">₹{money(headline)}</span>
                    <span className="text-[11px] text-muted">{perPiece ? "per piece" : i.pack_qty > 1 ? "per pack" : "each"}</span>
                    {i.tag && (
                      <span className="ml-auto rounded-sm bg-accent-soft px-1 py-0.5 text-[9px] font-bold uppercase text-accent">
                        {i.tag}
                      </span>
                    )}
                  </p>
                  {packInfo && (
                    <p className="text-[11px] leading-snug text-muted">
                      {perPiece
                        ? `pack of ${i.pack_qty}: ₹${money(i.price)}`
                        : `for ${i.pack_qty} pcs${i.piece_price ? ` · ₹${money(i.piece_price)} per piece` : ""}`}
                    </p>
                  )}
                  {canPiece && (
                    <div role="group" aria-label={`Order ${i.name} by`} className="mt-2 inline-flex overflow-hidden rounded-lg border border-line text-xs font-semibold">
                      {(["pack", "piece"] as const).map((u) => (
                        <button
                          key={u}
                          type="button"
                          aria-pressed={unit === u}
                          className={`px-3 py-1.5 ${unit === u ? "bg-accent text-ground" : "bg-card text-muted"}`}
                          onClick={() => setUnit(i.id, u)}
                        >
                          {u === "pack" ? "Pack" : "Piece"}
                        </button>
                      ))}
                    </div>
                  )}
                  {qty === 0 ? (
                    <button
                      type="button"
                      className="mt-2 w-full rounded-lg border border-accent py-1.5 text-xs font-semibold text-accent"
                      onClick={() => setQty(i.id, 1)}
                    >
                      Add
                    </button>
                  ) : (
                    <>
                      <Stepper qty={qty} unit={unit} label={i.name} onChange={(n) => setQty(i.id, n)} />
                      <p className="mt-1.5 text-[11px] tabular-nums text-muted">
                        {qty} {unitName(unit, qty)} × ₹{money(rateOf(i, unit))} ={" "}
                        <b className="text-ink">₹{money(rateOf(i, unit) * qty)}</b>
                      </p>
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {packs > 0 && (
        <div className="fixed inset-x-0 bottom-0 z-10 bg-accent px-4 py-3 text-ground shadow-[0_-2px_8px_rgba(0,0,0,.15)]">
          <button
            type="button"
            className="mx-auto flex w-full max-w-5xl items-center justify-between text-sm font-semibold"
            onClick={() => setStep("review")}
          >
            <span>
              {lines.length} item{lines.length === 1 ? "" : "s"} · ₹{money(total)}
            </span>
            <span>View order ›</span>
          </button>
        </div>
      )}

      {zoom && zoom.photo_url && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={zoom.name}
          className="fixed inset-0 z-20 grid place-items-center bg-black/70 p-4"
          onClick={() => setZoom(null)}
        >
          <figure className="max-h-full max-w-lg overflow-auto rounded-lg bg-card p-2">
            <img src={zoom.photo_url} alt={zoom.name} className="max-h-[75vh] w-full object-contain" />
            <figcaption className="px-1 py-2 text-sm">
              <span className="font-medium">{zoom.name}</span>
              <span className="block text-xs text-muted">
                ₹{money(zoom.price)} per pack of {zoom.pack_qty}
                {zoom.piece_price ? ` · ₹${money(zoom.piece_price)} per piece` : ""}
              </span>
            </figcaption>
          </figure>
        </div>
      )}
    </Shell>
  );
}

function Shell({ data, children }: { data: PublicCatalog; children: React.ReactNode }) {
  return (
    <div className="min-h-[100dvh] bg-ground text-ink">
      <header className="sticky top-0 z-10 bg-ink px-4 py-3 text-ground">
        <div className="mx-auto flex max-w-5xl items-baseline justify-between gap-3">
          <h1 className="font-serif text-lg font-semibold">{data.firm_name}</h1>
          {data.firm_phone && (
            <a href={`tel:${data.firm_phone}`} className="text-xs text-[#b9b3a7] underline">
              {data.firm_phone}
            </a>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 pb-8 pt-4">{children}</main>
    </div>
  );
}

function Stepper({
  qty,
  unit,
  label,
  onChange,
}: {
  qty: number;
  unit: Unit;
  label: string;
  onChange: (n: number) => void;
}) {
  return (
    <div className="mt-2 flex items-center justify-between">
      <button
        type="button"
        aria-label={`Fewer ${label}`}
        className="h-8 w-8 rounded-lg border border-line bg-card text-lg leading-none"
        onClick={() => onChange(qty - 1)}
      >
        −
      </button>
      <input
        aria-label={`${unit === "piece" ? "Pieces" : "Packs"} of ${label}`}
        inputMode="numeric"
        className="w-12 rounded-md border border-line bg-card py-1 text-center text-sm font-semibold tabular-nums"
        value={qty}
        onChange={(e) => onChange(Number(e.target.value.replace(/\D/g, "")))}
      />
      <button
        type="button"
        aria-label={`More ${label}`}
        className="h-8 w-8 rounded-lg border border-line bg-card text-lg leading-none"
        onClick={() => onChange(qty + 1)}
      >
        +
      </button>
    </div>
  );
}

function ReviewOrder({
  token,
  personal,
  campaign,
  data,
  lines,
  total,
  min,
  setQty,
  onBack,
  onPlaced,
  onUnavailable,
}: {
  token: string;
  personal: string;
  campaign: string;
  data: PublicCatalog;
  lines: Line[];
  total: number;
  min: number;
  setQty: (id: string, qty: number) => void;
  onBack: () => void;
  onPlaced: (p: Placed) => void;
  onUnavailable: (ids: string[]) => void;
}) {
  const saved = read<{ name: string; phone: string; firm: string; wa: boolean }>("customer", {
    name: "",
    phone: "",
    firm: "",
    wa: true,
  });
  const [name, setName] = useState(saved.name || data.customer?.name || "");
  const [phone, setPhone] = useState(saved.phone);
  // on a personal link the shop already holds the number, so it need not be typed again
  const knownNumber = !!data.customer;
  const [firm, setFirm] = useState(saved.firm);
  const [note, setNote] = useState("");
  const [wa, setWa] = useState(saved.wa);
  const [trap, setTrap] = useState(""); // hidden field: only bots fill it
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const belowMin = min > 0 && total < min;
  const ready = lines.length > 0 && name.trim().length >= 2 && (knownNumber || phone.replace(/\D/g, "").length >= 10) && !belowMin;

  async function place() {
    setBusy(true);
    setErr(null);
    try {
      const res = await fetch(`/api/public/catalog/${encodeURIComponent(token)}/orders`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          phone,
          p: personal || null,
          c: campaign || null,
          firm: firm || null,
          note: note || null,
          wa_opt_in: wa,
          website: trap || null,
          lines: lines.map((l) => ({ item_id: l.item.id, qty: l.qty, unit: l.unit })),
        }),
      });
      const body = (await res.json().catch(() => ({}))) as {
        detail?: string | { message?: string; unavailable?: { id: string; name: string | null }[] };
      } & Placed;
      if (res.ok) {
        write("customer", { name, phone, firm, wa });
        onPlaced(body);
        return;
      }
      const d = body.detail;
      if (res.status === 409 && typeof d === "object" && d?.unavailable) {
        const names = d.unavailable.map((u) => u.name ?? "an item").join(", ");
        setErr(`${names} ${d.unavailable.length === 1 ? "is" : "are"} no longer available and was removed.`);
        onUnavailable(d.unavailable.map((u) => u.id));
        return;
      }
      setErr(
        typeof d === "string" ? d : res.status === 429 ? "Too many tries. Please call the shop." : "Could not place the order.",
      );
    } catch {
      setErr("Could not reach the shop. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-lg pb-10">
      <button type="button" className="text-sm text-accent" onClick={onBack}>
        ‹ Keep shopping
      </button>
      <h2 className="mt-2 font-serif text-xl font-semibold">Your order</h2>

      <ul className="card mt-3 divide-y divide-line bg-card">
        {lines.map(({ item, qty, unit }) => (
          <li key={item.id} className="flex items-center gap-3 p-3">
            <div className="min-w-0 flex-1">
              <p className="line-clamp-2 text-[13px] font-medium leading-snug">{item.name}</p>
              <p className="text-[11px] text-muted tabular-nums">
                {qty} {unitName(unit, qty)} × ₹{money(rateOf(item, unit))}
                {unit === "pack" && item.pack_qty > 1 ? ` · ${qty * item.pack_qty} pcs` : ""}
              </p>
            </div>
            <div className="w-28 shrink-0">
              <Stepper qty={qty} unit={unit} label={item.name} onChange={(n) => setQty(item.id, n)} />
            </div>
            <p className="w-20 shrink-0 text-right text-sm font-semibold tabular-nums">
              ₹{money(rateOf(item, unit) * qty)}
            </p>
          </li>
        ))}
        <li className="flex items-center justify-between p-3 text-sm">
          <span className="font-semibold">Total{data.terms_line?.toLowerCase().includes("gst") ? "" : " (before GST)"}</span>
          <span className="text-base font-semibold tabular-nums">₹{money(total)}</span>
        </li>
      </ul>
      {belowMin && (
        <p className="mt-2 rounded-md border border-[#e8d9bd] bg-[#faf4ec] px-3 py-2 text-xs text-[#7a5a1c]" role="alert">
          The minimum order is ₹{money(min)}. Add ₹{money(min - total)} more.
        </p>
      )}
      {data.terms_line && <p className="mt-2 text-xs text-muted">{data.terms_line}</p>}

      <div className="mt-4 space-y-3">
        <div>
          <label className="label" htmlFor="co-name">
            Your name
          </label>
          <input id="co-name" className="field" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="co-phone">
            {knownNumber ? "Phone (WhatsApp), optional" : "Phone (WhatsApp)"}
          </label>
          <input
            id="co-phone"
            className="field"
            type="tel"
            inputMode="tel"
            autoComplete="tel"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="co-firm">
            Shop or firm (optional)
          </label>
          <input id="co-firm" className="field" autoComplete="organization" value={firm} onChange={(e) => setFirm(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="co-note">
            Note (optional)
          </label>
          <input id="co-note" className="field" maxLength={500} placeholder="e.g. Deliver by Friday" value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        {/* a field real people never see; bots fill every field they find */}
        <div aria-hidden className="absolute -left-[9999px] h-0 w-0 overflow-hidden">
          <label>
            Website
            <input tabIndex={-1} autoComplete="off" value={trap} onChange={(e) => setTrap(e.target.value)} />
          </label>
        </div>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={wa} onChange={(e) => setWa(e.target.checked)} />
          <span>Send me order updates on WhatsApp</span>
        </label>
      </div>

      {err && (
        <p className="err mt-3" role="alert">
          {err}
        </p>
      )}
      <button type="button" className="btn-primary mt-4 w-full py-3 text-base" disabled={!ready || busy} onClick={place}>
        {busy ? "Placing…" : "Place order"}
      </button>
      <p className="mt-2 text-center text-xs text-muted">
        {data.firm_name} will confirm availability. Nothing is charged by placing an order.
      </p>
    </div>
  );
}
