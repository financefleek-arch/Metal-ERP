import { useFormContext } from "react-hook-form";
import { Hint } from "./bits";
import { clean, type FormShape } from "./formShape";

const DAYS_RE = /^\s*\d{1,3}(\s*,\s*\d{1,3}){0,4}\s*$/;
const PRESETS = [
  { name: "Gentle", days: "7,15" },
  { name: "Standard", days: "7,15,30" },
  { name: "Firm", days: "3,7,15,30" },
];

const dayList = (s: string) =>
  s
    .split(",")
    .map((d) => Number(d.trim()))
    .filter((n) => Number.isFinite(n) && n > 0);

const spoken = (nums: number[]) =>
  nums.length < 2 ? nums.join("") : `${nums.slice(0, -1).join(", ")} and ${nums[nums.length - 1]}`;

/** The shop's payment-reminder settings, in the order a person thinks about them: do I want this, how long do
 *  customers get, when do we nudge, and who presses send. Saved with the rest of the Firm page. */
export function RemindersPanel({ autoAllowed }: { autoAllowed: boolean }) {
  const { register, watch, setValue, formState } = useFormContext<FormShape>();
  const { errors } = formState;
  const on = watch("reminder_enabled");
  const auto = watch("reminder_auto_send") && autoAllowed;
  const days = watch("reminder_days") ?? "";
  const credit = clean(watch("default_credit_days"));
  const creditNum = /^\d{1,3}$/.test(credit) && Number(credit) <= 365 ? Number(credit) : null;
  const picked = DAYS_RE.test(days) ? dayList(days) : [];
  const firstDay = picked.length ? Math.min(...picked) : null;
  const same = (a: string) => dayList(a).join() === picked.slice().sort((x, y) => x - y).join();

  return (
    <section className="card p-4 sm:p-5">
      <h2 className="font-serif text-lg font-semibold">Payment reminders</h2>
      <p className="mt-0.5 text-sm text-muted">
        Every morning we list the customers who have not paid on time. You choose who to remind, and one tap sends it on
        WhatsApp.
      </p>

      <label className="mt-4 flex cursor-pointer items-center gap-3 rounded-lg border border-line px-3 py-3">
        <input type="checkbox" className="h-5 w-5 accent-accent" {...register("reminder_enabled")} />
        <span>
          <span className="block font-medium">Find overdue bills for me every morning</span>
          <span className="block text-sm text-muted">{on ? "On. The list is ready each morning." : "Off. Nothing is listed or sent."}</span>
        </span>
      </label>

      <div className={`mt-5 space-y-6 ${on ? "" : "pointer-events-none opacity-50"}`} aria-disabled={!on}>
        <div>
          <label className="label" htmlFor="rm-credit">How many days do customers get to pay?</label>
          <div className="flex items-center gap-2">
            <input
              id="rm-credit"
              inputMode="numeric"
              className={`field w-24 ${errors.default_credit_days ? "border-danger" : ""}`}
              {...register("default_credit_days", {
                validate: (x) => (/^\d{1,3}$/.test(clean(x)) && Number(clean(x)) <= 365 ? true : "Enter a number from 0 to 365"),
              })}
            />
            <span className="text-sm text-muted">days</span>
          </div>
          {errors.default_credit_days ? (
            <p className="err">{errors.default_credit_days.message}</p>
          ) : (
            <Hint>
              {creditNum === 0
                ? "Bills are due on the bill date."
                : creditNum !== null
                  ? `A bill is due ${creditNum} days after its date. After that it counts as overdue.`
                  : "Use 0 if bills are due the day they are made."}
            </Hint>
          )}
        </div>

        <div>
          <div className="label" id="rm-when">When should we remind them?</div>
          <div className="flex flex-wrap gap-2" role="group" aria-labelledby="rm-when">
            {PRESETS.map((p) => (
              <button
                key={p.name}
                type="button"
                aria-pressed={same(p.days)}
                onClick={() => setValue("reminder_days", p.days, { shouldDirty: true, shouldValidate: true })}
                className={`rounded-full border px-3.5 py-1.5 text-sm ${
                  same(p.days) ? "border-accent bg-accent-soft font-medium text-accent-deep" : "border-line bg-card"
                }`}
              >
                {p.name} <span className="text-muted">({p.days.split(",").join(", ")} days)</span>
              </button>
            ))}
          </div>
          <label className="label mt-3" htmlFor="rm-days">Or choose your own: days after the due date</label>
          <input
            id="rm-days"
            className={`field w-48 ${errors.reminder_days ? "border-danger" : ""}`}
            placeholder="7, 15, 30"
            {...register("reminder_days", {
              validate: (x) => (DAYS_RE.test(x ?? "") ? true : "Enter up to 5 numbers, like 7, 15, 30"),
            })}
          />
          {errors.reminder_days ? (
            <p className="err">{errors.reminder_days.message}</p>
          ) : firstDay !== null && creditNum !== null ? (
            <Hint>
              You will be asked to remind on day {spoken(picked.slice().sort((a, b) => a - b).map((d) => d + creditNum))} after the
              bill date.
            </Hint>
          ) : null}
        </div>

        <fieldset>
          <legend className="label">Who presses send?</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            <label
              className={`flex cursor-pointer gap-3 rounded-lg border-[1.5px] px-3.5 py-3 ${
                !auto ? "border-accent bg-accent-soft" : "border-line bg-card"
              }`}
            >
              <input type="radio" name="rm-mode" className="mt-1 accent-accent" checked={!auto} onChange={() => setValue("reminder_auto_send", false, { shouldDirty: true })} />
              <span>
                <span className="block font-medium">I do</span>
                <span className="block text-sm text-muted">You look at the list and tap send. Recommended.</span>
              </span>
            </label>
            <label
              className={`flex gap-3 rounded-lg border-[1.5px] px-3.5 py-3 ${autoAllowed ? "cursor-pointer" : "cursor-not-allowed opacity-60"} ${
                auto ? "border-brass bg-brass-soft" : "border-line bg-card"
              }`}
            >
              <input
                type="radio"
                name="rm-mode"
                className="mt-1 accent-brass"
                disabled={!autoAllowed}
                checked={auto}
                onChange={() => setValue("reminder_auto_send", true, { shouldDirty: true })}
              />
              <span>
                <span className="block font-medium">Send for me</span>
                <span className="block text-sm text-muted">Goes out on its own, no approval.</span>
              </span>
            </label>
          </div>
          {!autoAllowed && <Hint>Sending for you is not switched on for your firm. Please contact Fleek.</Hint>}
          {auto && (
            <p className="mt-2 rounded-md border border-brass/40 bg-brass-soft px-3 py-2 text-sm text-brass-dark">
              Each reminder goes out between 9am and 7pm, on the day a bill reaches one of those days.{" "}
              <b>Bills that are already overdue for longer are not sent on their own.</b> They wait on your list, so turning
              this on never messages everyone at once. At most 40 a day.
            </p>
          )}
        </fieldset>
      </div>
    </section>
  );
}
