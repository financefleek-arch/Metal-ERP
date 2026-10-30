import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import type { Tenant } from "../lib/types";
import { TallyConnectCard } from "../components/TallyConnectCard";
import { TallyBackupsCard } from "../components/TallyBackupsCard";
import { CatalogSettingsCard } from "../components/CatalogSettingsCard";
import { OrderSettingsCard } from "../components/OrderSettingsCard";
import { ReminderSettingsCard } from "../components/ReminderSettingsCard";
import { WhatsappConnectCard } from "../components/WhatsappConnectCard";
import { PhotoUsageLine } from "../components/PhotoUsageLine";
import { useAuth } from "../lib/auth";
import { useFeature } from "../lib/features";
import { canWrite } from "../lib/roles";
import { gstinError, phoneError, pincodeError, useStates } from "../lib/reference";

/** Every box on the page. `shop_name` is the one-name field simple-bill shops see. */
interface FormShape {
  shop_name: string;
  legal_name: string;
  trade_name: string;
  phone: string;
  pincode: string;
  city: string;
  address: string;
  gstin: string;
  upi_id: string;
  bank_holder: string;
  bank_ac_no: string;
  bank_ifsc: string;
  bank_name: string;
  bank_branch: string;
  document_label: string;
  declaration_text: string;
  terms_text: string;
  jurisdiction_text: string;
}

type Tab = "business" | "pay" | "wording" | "connections";

const TAB_OF_FIELD: Partial<Record<keyof FormShape, Tab>> = {
  shop_name: "business",
  legal_name: "business",
  trade_name: "business",
  phone: "business",
  pincode: "business",
  city: "business",
  address: "business",
  gstin: "business",
  upi_id: "pay",
  bank_holder: "pay",
  bank_ac_no: "pay",
  bank_ifsc: "pay",
  bank_name: "pay",
};

const LABELS = ["Invoice", "Bill", "Cash Memo", "Estimate"];
const DECLARATIONS = [
  "Goods once sold will not be taken back.",
  "Weight as per our scale is final.",
  "We declare the details above are true and correct.",
];
/** The first four letters of an IFSC name the bank, so the bank name never needs typing. */
const BANK_OF_IFSC: Record<string, string> = {
  SBIN: "State Bank of India",
  HDFC: "HDFC Bank",
  ICIC: "ICICI Bank",
  UTIB: "Axis Bank",
  PUNB: "Punjab National Bank",
  BARB: "Bank of Baroda",
  KKBK: "Kotak Mahindra Bank",
  CNRB: "Canara Bank",
  UBIN: "Union Bank of India",
  IDIB: "Indian Bank",
};
const IFSC_RE = /^[A-Z]{4}0[A-Z0-9]{6}$/;
const UPI_RE = /^[\w.-]{2,}@[A-Za-z]{2,}$/;

/** The versioned policy page on the corporate site; Section 12 covers Metal ERP. */
const PRIVACY_POLICY_URL = "https://fleekfintech.com/privacy-policy/";

const clean = (s: string | undefined) => (s ?? "").trim();
const orNull = (s: string | undefined) => clean(s) || null;

function seedOf(t: Tenant): FormShape {
  return {
    shop_name: t.trade_name || t.legal_name || "",
    legal_name: t.legal_name ?? "",
    trade_name: t.trade_name ?? "",
    phone: t.phone ?? "",
    pincode: t.pincode ?? "",
    city: t.city ?? "",
    address: t.address ?? "",
    gstin: t.gstin ?? "",
    upi_id: t.upi_id ?? "",
    bank_holder: t.bank_holder ?? "",
    bank_ac_no: t.bank_ac_no ?? "",
    bank_ifsc: t.bank_ifsc ?? "",
    bank_name: t.bank_name ?? "",
    bank_branch: t.bank_branch ?? "",
    document_label: t.document_label || "Invoice",
    declaration_text: t.declaration_text ?? "",
    terms_text: t.terms_text ?? "",
    jurisdiction_text: t.jurisdiction_text ?? "",
  };
}

function Hint({ children, tone }: { children: React.ReactNode; tone?: "ok" | "bad" }) {
  const cls = tone === "bad" ? "text-danger" : tone === "ok" ? "text-ok" : "text-muted";
  return <p className={`mt-1 text-xs ${cls}`}>{children}</p>;
}

function Pill({ children, tone }: { children: React.ReactNode; tone?: "need" | "gst" | "auto" }) {
  const cls =
    tone === "need"
      ? "border-accent bg-accent-soft text-accent-deep"
      : tone === "gst"
        ? "border-brass bg-brass-soft text-brass-dark"
        : tone === "auto"
          ? "border-dashed border-line text-muted"
          : "border-line";
  return <span className={`rounded-full border px-2.5 py-0.5 text-xs ${cls}`}>{children}</span>;
}

export function FirmPage() {
  const qc = useQueryClient();
  const { me } = useAuth();
  const tallyOn = useFeature("tally");
  const whatsappOn = useFeature("whatsapp");
  const remindersOn = useFeature("reminders");
  const states = useStates();
  const { data, isLoading } = useQuery({
    queryKey: ["tenant"],
    queryFn: () => api<Tenant>("/tenant"),
  });

  const {
    register,
    handleSubmit,
    reset,
    watch,
    setValue,
    formState: { errors },
  } = useForm<FormShape>();
  const v = watch();

  const [gst, setGst] = useState(false);
  const [tab, setTab] = useState<Tab>("business");
  const [bankOpen, setBankOpen] = useState(false);
  const [saved, setSaved] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  // Re-seed the form only when the profile itself changed. Saving one of the settings cards also
  // replaces `data`, and that must not wipe what is half-typed in this form.
  const seededWith = useRef("");
  useEffect(() => {
    if (!data) return;
    const seed = seedOf(data);
    const sig = JSON.stringify([seed, data.gst_enabled]);
    if (sig === seededWith.current) return;
    seededWith.current = sig;
    reset(seed);
    setGst(data.gst_enabled);
    setBankOpen(Boolean(data.bank_ac_no || data.bank_ifsc || data.bank_name || data.bank_holder));
  }, [data, reset]);

  const mut = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<Tenant>("/tenant", { method: "PATCH", body }),
    onSuccess: (t) => {
      qc.setQueryData(["tenant"], t);
      qc.invalidateQueries({ queryKey: ["setup"] });
      setSaved(true);
      setServerError(null);
      setTimeout(() => setSaved(false), 2200);
    },
    onError: (e) => setServerError(e instanceof ApiError ? e.message : "Save failed"),
  });

  const onSubmit = handleSubmit(
    (f) => {
      const body: Record<string, unknown> = {
        phone: orNull(f.phone),
        pincode: orNull(f.pincode),
        city: orNull(f.city),
        address: orNull(f.address),
        upi_id: orNull(f.upi_id),
        bank_holder: orNull(f.bank_holder),
        bank_ac_no: orNull(f.bank_ac_no),
        bank_ifsc: orNull(f.bank_ifsc.toUpperCase()),
        bank_name: orNull(f.bank_name),
        bank_branch: orNull(f.bank_branch),
        document_label: clean(f.document_label) || "Invoice",
        declaration_text: orNull(f.declaration_text),
        terms_text: orNull(f.terms_text),
        jurisdiction_text: orNull(f.jurisdiction_text),
        gst_enabled: gst,
      };
      if (gst) {
        body.legal_name = clean(f.legal_name);
        body.trade_name = orNull(f.trade_name);
        body.gstin = clean(f.gstin).toUpperCase();
      } else if (data?.trade_name) {
        body.trade_name = clean(f.shop_name); // the invoice prints the trade name when there is one
      } else {
        body.legal_name = clean(f.shop_name);
      }
      mut.mutate(body);
    },
    (bad) => {
      const first = (Object.keys(bad) as (keyof FormShape)[]).find((k) => TAB_OF_FIELD[k]);
      if (first) setTab(TAB_OF_FIELD[first]!);
    },
  );

  if (isLoading) return <p className="text-sm text-muted">Loading…</p>;

  const owner = me?.role === "owner";
  const showConnections =
    tallyOn ||
    (whatsappOn && owner) ||
    (remindersOn && canWrite(me?.role)) ||
    Boolean(me?.ext_supplier_catalog);
  const tabs: { key: Tab; label: string }[] = [
    { key: "business", label: "Business" },
    { key: "pay", label: "Get paid" },
    { key: "wording", label: "Bill wording" },
    ...(showConnections ? [{ key: "connections" as const, label: "Connections" }] : []),
  ];
  const editing = tab !== "connections";

  const gstinNow = clean(v.gstin).toUpperCase();
  const gstinOk = gstinNow.length === 15 && !gstinError(gstinNow);
  const stateName = gstinOk ? states.data?.find((s) => s.code === gstinNow.slice(0, 2))?.name : undefined;
  const upiNow = clean(v.upi_id);
  const ifscNow = clean(v.bank_ifsc).toUpperCase();
  const labelChoices = LABELS.includes(v.document_label) || !v.document_label ? LABELS : [...LABELS, v.document_label];

  const fieldClass = (bad: boolean) => `field${bad ? " border-danger" : ""}`;

  return (
    <div className="max-w-5xl">
      <div className="mb-4 flex items-baseline justify-between gap-3">
        <h1 className="font-serif text-2xl font-semibold">Firm</h1>
        {saved && <span className="text-xs text-ok">Saved ✓</span>}
      </div>

      <section className="card mb-4 p-4 sm:p-5" aria-labelledby="bill-mode">
        <h2 id="bill-mode" className="font-serif text-lg font-semibold">
          How do you bill your customers?
        </h2>
        <p className="mt-0.5 text-sm text-muted">This decides what we ask you. You can change it any time.</p>
        <div className="mt-3 grid gap-2 sm:grid-cols-2" role="radiogroup" aria-labelledby="bill-mode">
          {[
            { on: !gst, gstOpt: false, title: "Simple bills", hint: "No GST. Your name, phone and how you get paid." },
            { on: gst, gstOpt: true, title: "GST invoices", hint: "For shops with a GSTIN. We read your state and PAN from it." },
          ].map((o) => (
            <button
              key={o.title}
              type="button"
              role="radio"
              aria-checked={o.on}
              onClick={() => setGst(o.gstOpt)}
              className={`rounded-lg border-[1.5px] px-4 py-3 text-left ${
                o.on ? (o.gstOpt ? "border-brass bg-brass-soft" : "border-accent bg-accent-soft") : "border-line bg-card"
              }`}
            >
              <div className="font-semibold">{o.title}</div>
              <div className="text-sm text-muted">{o.hint}</div>
            </button>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-sm">
          <span className="text-muted">We ask:</span>
          {gst ? (
            <>
              <Pill tone="need">Trade name</Pill>
              <Pill tone="need">Legal name</Pill>
              <Pill tone="need">Phone</Pill>
              <Pill tone="gst">GSTIN</Pill>
              <span className="text-muted">Read from GSTIN:</span>
              <Pill tone="auto">State</Pill>
              <Pill tone="auto">PAN</Pill>
            </>
          ) : (
            <>
              <Pill tone="need">Shop name</Pill>
              <Pill tone="need">Phone</Pill>
              <span className="text-muted">Optional:</span>
              <Pill>Address</Pill>
              <Pill>UPI</Pill>
              <Pill>Bank</Pill>
            </>
          )}
        </div>
        <p className="mt-2 text-xs text-muted">
          {gst
            ? "Your GSTIN prints on bills. Tax lines on bills are not added yet. Old bills stay as they were issued."
            : "Nothing about GST is asked or printed. If you start charging GST later, switch here. Old bills stay as they were."}
        </p>
      </section>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0">
          <div className="mb-4 flex gap-1 overflow-x-auto border-b border-line" role="tablist" aria-label="Firm settings">
            {tabs.map((t) => (
              <button
                key={t.key}
                role="tab"
                type="button"
                aria-selected={tab === t.key}
                onClick={() => setTab(t.key)}
                className={`-mb-px whitespace-nowrap border-b-2 px-4 py-2.5 text-sm font-medium ${
                  tab === t.key ? "border-accent text-ink" : "border-transparent text-muted"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          {tab === "connections" && (
            <>
              {tallyOn && (
                <>
                  <TallyConnectCard />
                  <TallyBackupsCard />
                </>
              )}
              {whatsappOn && owner && <WhatsappConnectCard />}
              {remindersOn && canWrite(me?.role) && <ReminderSettingsCard tenant={data} />}
              {me?.ext_supplier_catalog && (
                <>
                  <CatalogSettingsCard tenant={data} />
                  <OrderSettingsCard tenant={data} />
                  <PhotoUsageLine />
                </>
              )}
            </>
          )}

          <form onSubmit={onSubmit} hidden={!editing}>
            {/* BUSINESS */}
            <div hidden={tab !== "business"} className="space-y-4">
              <section className="card p-4 sm:p-5">
                <h2 className="mb-3 font-serif text-lg font-semibold">Your shop</h2>
                <div className="grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2">
                  {gst ? (
                    <>
                      <div className="sm:col-span-2">
                        <label className="label" htmlFor="f-trade">Trade name (the name customers know you by)</label>
                        <input id="f-trade" className="field" {...register("trade_name")} />
                        <Hint>Printed at the top of every bill.</Hint>
                      </div>
                      <div className="sm:col-span-2">
                        <label className="label" htmlFor="f-legal">Legal name (as on your GST certificate)</label>
                        <input
                          id="f-legal"
                          className={fieldClass(Boolean(errors.legal_name))}
                          {...register("legal_name", { validate: (x) => (clean(x) ? true : "Enter your legal name") })}
                        />
                        {errors.legal_name && <p className="err">{errors.legal_name.message}</p>}
                      </div>
                    </>
                  ) : (
                    <div className="sm:col-span-2">
                      <label className="label" htmlFor="f-shop">Shop name</label>
                      <input
                        id="f-shop"
                        className={fieldClass(Boolean(errors.shop_name))}
                        {...register("shop_name", { validate: (x) => (clean(x) ? true : "Enter your shop name") })}
                      />
                      {errors.shop_name ? <p className="err">{errors.shop_name.message}</p> : <Hint>Printed at the top of every bill.</Hint>}
                    </div>
                  )}
                  <div>
                    <label className="label" htmlFor="f-phone">Phone</label>
                    <input
                      id="f-phone"
                      inputMode="tel"
                      className={fieldClass(Boolean(errors.phone))}
                      {...register("phone", { validate: (x) => phoneError(clean(x)) ?? true })}
                    />
                    {errors.phone ? <p className="err">{errors.phone.message}</p> : <Hint>Customers see this on bills and on your catalog page.</Hint>}
                  </div>
                  <div>
                    <label className="label" htmlFor="f-city">City (optional)</label>
                    <input id="f-city" className="field" {...register("city")} />
                  </div>
                  <div className="sm:col-span-2">
                    <label className="label" htmlFor="f-address">Address (optional)</label>
                    <textarea id="f-address" rows={2} className="field h-auto resize-y py-2" {...register("address")} />
                  </div>
                  <div>
                    <label className="label" htmlFor="f-pin">PIN code (optional)</label>
                    <input
                      id="f-pin"
                      inputMode="numeric"
                      maxLength={6}
                      className={fieldClass(Boolean(errors.pincode))}
                      {...register("pincode", { validate: (x) => pincodeError(clean(x)) ?? true })}
                    />
                    {errors.pincode && <p className="err">{errors.pincode.message}</p>}
                  </div>
                </div>
              </section>

              {gst ? (
                <section className="card border-brass bg-brass-soft p-4 sm:p-5">
                  <h2 className="font-serif text-lg font-semibold text-brass-dark">GST details</h2>
                  <p className="mb-3 text-sm text-muted">Type the GSTIN and we read the state and PAN from it.</p>
                  <label className="label" htmlFor="f-gstin">GSTIN</label>
                  <input
                    id="f-gstin"
                    maxLength={15}
                    placeholder="23ABCDE1234F1Z5"
                    autoComplete="off"
                    className={`${fieldClass(Boolean(errors.gstin))} uppercase`}
                    {...register("gstin", {
                      setValueAs: (x: string) => (x ?? "").trim().toUpperCase(),
                      validate: (x) => (!clean(x) ? "Enter your GSTIN" : (gstinError(clean(x)) ?? true)),
                    })}
                  />
                  {errors.gstin ? (
                    <p className="err">{errors.gstin.message}</p>
                  ) : gstinOk ? (
                    <div className="mt-2 flex flex-wrap gap-2">
                      <Hint tone="ok">Looks right.</Hint>
                      <Pill tone="auto">State: {stateName ?? `code ${gstinNow.slice(0, 2)}`}</Pill>
                      <Pill tone="auto">PAN: {gstinNow.slice(2, 12)}</Pill>
                    </div>
                  ) : (
                    <Hint>15 characters, for example 23ABCDE1234F1Z5.</Hint>
                  )}
                </section>
              ) : (
                <section className="rounded-xl border border-dashed border-line px-4 py-3 text-sm">
                  <b>Not asking for:</b> GSTIN, PAN, state, legal name. Switch to GST invoices above if you need them later.
                  Anything you entered there is kept.
                </section>
              )}
            </div>

            {/* GET PAID */}
            <div hidden={tab !== "pay"} className="space-y-4">
              <section className="card p-4 sm:p-5">
                <h2 className="font-serif text-lg font-semibold">Get paid by UPI</h2>
                <p className="mb-3 text-sm text-muted">Most customers pay on their phone. Add your UPI ID and every bill shows it.</p>
                <label className="label" htmlFor="f-upi">UPI ID</label>
                <input
                  id="f-upi"
                  placeholder="yourshop@okhdfcbank"
                  autoComplete="off"
                  className={fieldClass(Boolean(errors.upi_id))}
                  {...register("upi_id", {
                    validate: (x) => (!clean(x) || UPI_RE.test(clean(x)) ? true : "Use the form name@bank, for example shop@okhdfcbank"),
                  })}
                />
                {errors.upi_id ? (
                  <p className="err">{errors.upi_id.message}</p>
                ) : upiNow && UPI_RE.test(upiNow) ? (
                  <Hint tone="ok">Looks right. It prints on every bill, even without a bank account.</Hint>
                ) : (
                  <Hint>Use the form name@bank. Find it in your UPI app.</Hint>
                )}
              </section>

              <section className="card p-4 sm:p-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h2 className="font-serif text-lg font-semibold">Bank account</h2>
                    <p className="text-sm text-muted">Optional. For customers who pay by bank transfer.</p>
                  </div>
                  <button type="button" className="btn" aria-expanded={bankOpen} onClick={() => setBankOpen((o) => !o)}>
                    {bankOpen ? "Hide bank details" : "Add bank account"}
                  </button>
                </div>
                <div hidden={!bankOpen} className="mt-4 grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2">
                  <div className="sm:col-span-2">
                    <label className="label" htmlFor="f-holder">Account holder name</label>
                    <input id="f-holder" className="field" {...register("bank_holder")} />
                  </div>
                  <div>
                    <label className="label" htmlFor="f-acno">Account number</label>
                    <input id="f-acno" inputMode="numeric" className="field" {...register("bank_ac_no")} />
                  </div>
                  <div>
                    <label className="label" htmlFor="f-ifsc">IFSC</label>
                    <input
                      id="f-ifsc"
                      maxLength={11}
                      placeholder="SBIN0001234"
                      className={`${fieldClass(Boolean(errors.bank_ifsc))} uppercase`}
                      {...register("bank_ifsc", {
                        onChange: (e) => {
                          const code = String(e.target.value).toUpperCase();
                          const bank = BANK_OF_IFSC[code.slice(0, 4)];
                          if (bank && IFSC_RE.test(code)) setValue("bank_name", bank);
                        },
                        validate: (x) => (!clean(x) || IFSC_RE.test(clean(x).toUpperCase()) ? true : "IFSC is 4 letters, a zero, then 6 letters or digits"),
                      })}
                    />
                    {errors.bank_ifsc && <p className="err">{errors.bank_ifsc.message}</p>}
                  </div>
                  <div className="sm:col-span-2">
                    <label className="label" htmlFor="f-bank">Bank name</label>
                    <input id="f-bank" className="field" {...register("bank_name")} />
                    <Hint>{ifscNow && IFSC_RE.test(ifscNow) && BANK_OF_IFSC[ifscNow.slice(0, 4)] ? "Filled in from the IFSC." : "Filled in from the IFSC when we know the bank."}</Hint>
                  </div>
                </div>
              </section>
            </div>

            {/* BILL WORDING */}
            <div hidden={tab !== "wording"} className="space-y-4">
              <section className="card p-4 sm:p-5">
                <h2 className="mb-3 font-serif text-lg font-semibold">Bill title</h2>
                <div className="flex flex-wrap gap-2">
                  {labelChoices.map((l) => (
                    <button
                      key={l}
                      type="button"
                      aria-pressed={v.document_label === l}
                      onClick={() => setValue("document_label", l, { shouldDirty: true })}
                      className={`rounded-full border px-3.5 py-1.5 text-sm ${
                        v.document_label === l ? "border-accent bg-accent-soft font-medium text-accent-deep" : "border-line bg-card"
                      }`}
                    >
                      {l}
                    </button>
                  ))}
                </div>
                <input type="hidden" {...register("document_label")} />
                <Hint>Printed at the top right of the bill.</Hint>
              </section>

              <section className="card p-4 sm:p-5">
                <h2 className="font-serif text-lg font-semibold">Lines at the bottom</h2>
                <p className="mb-3 text-sm text-muted">Tap a suggestion to start, then edit it. All optional.</p>
                <div className="space-y-4">
                  <div>
                    <label className="label" htmlFor="f-decl">Declaration</label>
                    <div className="mb-2 flex flex-wrap gap-2">
                      {DECLARATIONS.map((d) => (
                        <button key={d} type="button" className="rounded-full border border-line bg-card px-3 py-1 text-sm" onClick={() => setValue("declaration_text", d, { shouldDirty: true })}>
                          {d}
                        </button>
                      ))}
                    </div>
                    <textarea id="f-decl" rows={2} className="field h-auto resize-y py-2" {...register("declaration_text")} />
                  </div>
                  <div>
                    <label className="label" htmlFor="f-terms">Terms</label>
                    <textarea id="f-terms" rows={2} placeholder="Payment within 15 days." className="field h-auto resize-y py-2" {...register("terms_text")} />
                  </div>
                  <div>
                    <label className="label" htmlFor="f-juris">Legal line</label>
                    {clean(v.city) && (
                      <div className="mb-2">
                        <button
                          type="button"
                          className="rounded-full border border-line bg-card px-3 py-1 text-sm"
                          onClick={() => setValue("jurisdiction_text", `Subject to ${clean(v.city)} jurisdiction only.`, { shouldDirty: true })}
                        >
                          Subject to {clean(v.city)} jurisdiction only.
                        </button>
                      </div>
                    )}
                    <input id="f-juris" className="field" {...register("jurisdiction_text")} />
                  </div>
                </div>
                <p className="mt-3 text-xs text-muted">Changes apply to new bills. Bills you already finalized keep the wording they were issued with.</p>
              </section>
            </div>

            {serverError && <p className="err mt-3">{serverError}</p>}
            <div className="sticky bottom-0 -mx-1 mt-4 flex items-center justify-end gap-3 bg-ground/95 px-1 py-3">
              {saved && <span className="text-sm text-ok">Saved ✓</span>}
              <button type="submit" className={gst ? "btn-brass" : "btn-primary"} disabled={mut.isPending}>
                {mut.isPending ? "Saving…" : "Save changes"}
              </button>
            </div>
          </form>

          <div className="card mt-5 p-4 sm:p-6">
            <h2 className="font-serif text-lg font-semibold">Account and data</h2>
            <p className="mt-1 text-sm text-muted">
              How we handle your data is in our{" "}
              <a className="text-accent underline underline-offset-2" href={PRIVACY_POLICY_URL}>
                privacy policy
              </a>
              .
            </p>
            {owner && (
              <p className="mt-3 text-sm">
                To close this firm&apos;s account and delete its data,{" "}
                <a className="text-danger underline underline-offset-2" href="/delete-account">
                  request account deletion
                </a>
                . Download or export what you need first: deletion is permanent.
              </p>
            )}
          </div>
        </div>

        <aside className="min-w-0 lg:sticky lg:top-4 lg:self-start" aria-label="Bill preview">
          <h2 className="mb-2 font-serif text-base font-semibold">Your bill, as customers see it</h2>
          <BillPreview v={v} gst={gst} gstin={gstinOk ? gstinNow : ""} />
          <p className="mt-2 text-xs text-muted">Example items. This is what the PDF prints today.</p>
        </aside>
      </div>
    </div>
  );
}

/** A paper-coloured miniature of the bill header and footer, so each box has a visible reason. */
function BillPreview({ v, gst, gstin }: { v: FormShape; gst: boolean; gstin: string }) {
  const name = gst ? clean(v.trade_name) || clean(v.legal_name) : clean(v.shop_name);
  const place = [clean(v.city), clean(v.pincode) ? `PIN ${clean(v.pincode)}` : ""].filter(Boolean).join(", ");
  const ifsc = clean(v.bank_ifsc).toUpperCase();
  const bankLine = [clean(v.bank_holder), clean(v.bank_name), clean(v.bank_ac_no) && `A/C ${clean(v.bank_ac_no)}`, ifsc && IFSC_RE.test(ifsc) ? `IFSC ${ifsc}` : ""]
    .filter(Boolean)
    .join(" · ");
  const upi = clean(v.upi_id);
  const initials = (name.split(/\s+/).slice(0, 2).map((w) => w[0]).join("") || "S").toUpperCase();
  return (
    <div className="rounded-md border border-line bg-white p-4 text-[11.5px] leading-snug text-[#23201b] shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 gap-2.5">
          <div className="grid h-9 w-9 flex-none place-items-center rounded bg-accent-soft font-serif text-base font-semibold text-accent-deep">
            {initials}
          </div>
          <div className="min-w-0 break-words">
            <div className="text-[13px] font-semibold">{name || "Your shop name"}</div>
            {gst && clean(v.legal_name) && clean(v.legal_name) !== name && <div className="text-[#7c766a]">{clean(v.legal_name)}</div>}
            {clean(v.address) && <div className="text-[#7c766a]">{clean(v.address)}</div>}
            {place && <div className="text-[#7c766a]">{place}</div>}
            {gst && gstin && <div className="text-[#7c766a]">GSTIN: {gstin}</div>}
            {gst && gstin && <div className="text-[#7c766a]">PAN: {gstin.slice(2, 12)}</div>}
            {clean(v.phone) && <div className="text-[#7c766a]">Contact: {clean(v.phone)}</div>}
          </div>
        </div>
        <div className="flex-none text-right">
          <div className="font-serif text-[15px] font-semibold uppercase tracking-wide">{clean(v.document_label) || "Invoice"}</div>
          <div className="text-[#7c766a]">No. 0042</div>
        </div>
      </div>
      <hr className="my-2.5 border-[#e6e0d6]" />
      <table className="w-full">
        <tbody>
          <tr>
            <td>Brass sheet 12.40 kg</td>
            <td className="text-right tabular-nums">6,448</td>
          </tr>
          <tr>
            <td>Copper wire 8.15 kg</td>
            <td className="text-right tabular-nums">6,520</td>
          </tr>
          <tr className="border-t-[1.5px] border-[#23201b] text-[13px] font-semibold">
            <td className="pt-1">Total</td>
            <td className="pt-1 text-right tabular-nums">₹12,968</td>
          </tr>
        </tbody>
      </table>
      {(bankLine || upi) && <hr className="my-2.5 border-[#e6e0d6]" />}
      {bankLine && (
        <div className="break-words text-[#7c766a]">
          <b className="text-[#23201b]">Bank:</b> {bankLine}
        </div>
      )}
      {upi && (
        <div className="break-words text-[#7c766a]">
          <b className="text-[#23201b]">UPI:</b> {upi}
        </div>
      )}
      {clean(v.declaration_text) && (
        <div className="mt-1.5 break-words text-[#7c766a]">
          <b className="text-[#23201b]">Declaration:</b> {clean(v.declaration_text)}
        </div>
      )}
      {clean(v.terms_text) && (
        <div className="mt-1 break-words text-[#7c766a]">
          <b className="text-[#23201b]">Terms:</b> {clean(v.terms_text)}
        </div>
      )}
      {clean(v.jurisdiction_text) && <div className="mt-1 break-words text-[#7c766a]">{clean(v.jurisdiction_text)}</div>}
      <div className="mt-5 text-right text-[#7c766a]">Authorised Signatory</div>
    </div>
  );
}
