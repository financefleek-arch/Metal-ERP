import { useFormContext } from "react-hook-form";
import { PhotoUsageLine } from "../../components/PhotoUsageLine";
import { validCodePrefix } from "../../lib/catalog";
import { phoneError } from "../../lib/reference";
import { Hint } from "./bits";
import { clean, type FormShape } from "./formShape";

/** What customers see on a catalog link, and how item codes and product groups are made. */
export function CatalogPanel() {
  const { register, watch, formState } = useFormContext<FormShape>();
  const { errors } = formState;
  const prefix = clean(watch("catalog_code_prefix")).toUpperCase();

  return (
    <div className="space-y-4">
      <section className="card p-4 sm:p-5">
        <h2 className="font-serif text-lg font-semibold">Your catalog page for customers</h2>
        <p className="mb-4 mt-0.5 text-sm text-muted">What a customer sees, and what you hear, when they open a catalog link you sent.</p>
        <div className="grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="om-min">Smallest order you will take (₹)</label>
            <input
              id="om-min"
              inputMode="decimal"
              placeholder="No minimum"
              className={`field ${errors.order_min_value ? "border-danger" : ""}`}
              {...register("order_min_value", {
                validate: (x) => (clean(x) === "" || /^\d{1,8}(\.\d{1,2})?$/.test(clean(x)) ? true : "Enter a number, for example 2000"),
              })}
            />
            {errors.order_min_value ? <p className="err">{errors.order_min_value.message}</p> : <Hint>Customers cannot send an order below this. Leave empty for none.</Hint>}
          </div>
          <div>
            <label className="label" htmlFor="om-alert">WhatsApp me when an order arrives</label>
            <input
              id="om-alert"
              type="tel"
              inputMode="tel"
              placeholder="Your mobile number"
              className={`field ${errors.order_alert_phone ? "border-danger" : ""}`}
              {...register("order_alert_phone", { validate: (x) => phoneError(clean(x)) ?? true })}
            />
            {errors.order_alert_phone ? <p className="err">{errors.order_alert_phone.message}</p> : <Hint>Leave empty if you do not want alerts. Customers who tick the WhatsApp box are updated on their own.</Hint>}
          </div>
          <div className="sm:col-span-2">
            <label className="label" htmlFor="om-terms">A note for customers</label>
            <input
              id="om-terms"
              maxLength={300}
              placeholder="Prices exclude GST. Payment as agreed."
              className="field"
              {...register("order_terms_line")}
            />
            <Hint>Shown at the bottom of every catalog page.</Hint>
          </div>
        </div>
      </section>

      <section className="card p-4 sm:p-5">
        <h2 className="font-serif text-lg font-semibold">Item codes and groups</h2>
        <p className="mb-4 mt-0.5 text-sm text-muted">How new items are named and sorted when you add them from supplier catalogs.</p>
        <div className="grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="catalog-prefix">Start every item code with (optional)</label>
            <input
              id="catalog-prefix"
              maxLength={8}
              placeholder="Nothing"
              className={`field uppercase ${errors.catalog_code_prefix ? "border-danger" : ""}`}
              {...register("catalog_code_prefix", {
                validate: (x) => (clean(x) === "" || validCodePrefix(x) ? true : "Use 2 to 8 letters or digits"),
              })}
            />
            {errors.catalog_code_prefix ? (
              <p className="err">{errors.catalog_code_prefix.message}</p>
            ) : (
              <Hint>
                Codes are one running number that never changes, like {prefix}100234. A prefix only applies to items added from now on.
              </Hint>
            )}
          </div>
          <div>
            <label className="label" htmlFor="catalog-group-policy">When a new product type turns up</label>
            <select id="catalog-group-policy" className="field" {...register("catalog_group_create_policy")}>
              <option value="auto">Add the group for me</option>
              <option value="suggest_only">Ask me first</option>
            </select>
            <Hint>A group is a product type, such as Beer Mugs. You can rename or merge groups later.</Hint>
          </div>
        </div>
        <PhotoUsageLine />
      </section>
    </div>
  );
}
