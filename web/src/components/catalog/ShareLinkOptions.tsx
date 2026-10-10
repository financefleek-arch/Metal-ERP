import type { LinkOptions } from "./shareLinkUtils";

/** How a catalog link looks to customers and what they can order. Used when making a link and when
 *  changing an existing one (its address stays the same). */
export function ShareLinkOptions({
  value,
  onChange,
  idPrefix = "slo",
}: {
  value: LinkOptions;
  onChange: (next: LinkOptions) => void;
  idPrefix?: string;
}) {
  const patch = (p: Partial<LinkOptions>) => onChange({ ...value, ...p });
  const premiumText = String(value.piece_premium_pct);
  const premiumBad = !Number.isFinite(value.piece_premium_pct) || value.piece_premium_pct < 0 || value.piece_premium_pct > 100;

  return (
    <div className="space-y-4">
      <fieldset>
        <legend className="label">Page layout on a phone</legend>
        <div className="flex flex-wrap gap-1.5">
          {(
            [
              [1, "1 per row", "large"],
              [2, "2 per row", "default"],
              [3, "3 per row", "compact"],
            ] as const
          ).map(([n, label, hint]) => (
            <label
              key={n}
              className={`cursor-pointer rounded-full border px-3 py-1 text-xs ${
                value.columns === n ? "border-accent bg-accent text-ground" : "border-line bg-card text-muted"
              }`}
            >
              <input
                type="radio"
                name={`${idPrefix}-cols`}
                className="sr-only"
                checked={value.columns === n}
                onChange={() => patch({ columns: n })}
              />
              {label} <span className="opacity-70">({hint})</span>
            </label>
          ))}
        </div>
        <label className="mt-2 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={value.show_code} onChange={(e) => patch({ show_code: e.target.checked })} />
          Show item code
        </label>
        <label className="mt-1 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={value.show_pack_info} onChange={(e) => patch({ show_pack_info: e.target.checked })} />
          Show &ldquo;for 6 pcs&rdquo; and the per-piece price
        </label>
        <p className="mt-1 text-xs text-muted">Tablets and computers show more per row automatically.</p>
      </fieldset>

      <fieldset>
        <legend className="label">Price shown</legend>
        <label className="flex items-baseline gap-2 text-sm">
          <input
            type="radio"
            name={`${idPrefix}-basis`}
            checked={value.price_basis === "pack"}
            onChange={() => patch({ price_basis: "pack" })}
          />
          <span>
            Pack price <span className="text-xs text-muted">(with the per-piece price underneath)</span>
          </span>
        </label>
        <label className="mt-1 flex items-baseline gap-2 text-sm">
          <input
            type="radio"
            name={`${idPrefix}-basis`}
            checked={value.price_basis === "piece"}
            onChange={() => patch({ price_basis: "piece" })}
          />
          <span>
            Price per piece <span className="text-xs text-muted">(with the pack price underneath)</span>
          </span>
        </label>
      </fieldset>

      <fieldset>
        <legend className="label">Customers can order</legend>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            name={`${idPrefix}-pieces`}
            checked={!value.allow_pieces}
            onChange={() => patch({ allow_pieces: false })}
          />
          Whole packs only
        </label>
        <label className="mt-1 flex items-center gap-2 text-sm">
          <input
            type="radio"
            name={`${idPrefix}-pieces`}
            checked={value.allow_pieces}
            onChange={() => patch({ allow_pieces: true })}
          />
          Packs or single pieces
        </label>
        <div className={`mt-2 flex flex-wrap items-center gap-2 ${value.allow_pieces ? "" : "opacity-50"}`}>
          <label className="label m-0" htmlFor={`${idPrefix}-prem`}>
            Loose-piece premium
          </label>
          <input
            id={`${idPrefix}-prem`}
            className="field w-20"
            inputMode="decimal"
            disabled={!value.allow_pieces}
            value={premiumText}
            onChange={(e) => {
              const v = e.target.value.replace(/[^0-9.]/g, "");
              patch({ piece_premium_pct: v === "" ? 0 : Number(v) });
            }}
          />
          <span className="text-xs text-muted">
            % on top of pack price &divide; pieces. Leave 0 for no premium. Only used when pieces can be ordered.
          </span>
        </div>
        {premiumBad && (
          <p className="err" role="alert">
            The premium must be between 0 and 100.
          </p>
        )}
      </fieldset>
    </div>
  );
}
