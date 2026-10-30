import { useEffect, useRef, useState } from "react";
import { FormProvider, useForm } from "react-hook-form";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import type { Tenant } from "../lib/types";
import { TallyConnectCard } from "../components/TallyConnectCard";
import { TallyBackupsCard } from "../components/TallyBackupsCard";
import { WhatsappConnectCard } from "../components/WhatsappConnectCard";
import { LockedConnection } from "../components/LockedConnection";
import type { FeatureKey } from "../lib/types";
import { useAuth } from "../lib/auth";
import { useFeature } from "../lib/features";
import { canWrite } from "../lib/roles";
import { gstinError, IFSC_RE, phoneError, pincodeError, useIfsc, useStates } from "../lib/reference";
import { UPI_RE, upiHandleUnknown } from "../lib/upiQr";
import { UpiQrPicker } from "../components/UpiQrPicker";
import { useIsDesktop } from "../lib/useIsDesktop";
import { BillPreviewFrame, BillPreviewModal } from "../components/BillPreviewFrame";
import { Hint, Pill } from "./firm/bits";
import { clean, orNull, type FormShape, type Tab } from "./firm/formShape";
import { RemindersPanel } from "./firm/RemindersPanel";
import { CatalogPanel } from "./firm/CatalogPanel";

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
  bank_ac_confirm: "pay",
  bank_ifsc: "pay",
  bank_name: "pay",
  reminder_days: "reminders",
  default_credit_days: "reminders",
  order_min_value: "catalog",
  order_alert_phone: "catalog",
  catalog_code_prefix: "catalog",
};

const LABELS = ["Invoice", "Bill", "Cash Memo", "Estimate"];
const DECLARATIONS = [
  "Goods once sold will not be taken back.",
  "Weight as per our scale is final.",
  "We declare the details above are true and correct.",
];

/** The versioned policy page on the corporate site; Section 12 covers Metal ERP. */
const PRIVACY_POLICY_URL = "https://fleekfintech.com/privacy-policy/";

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
    upi_qr_payload: t.upi_qr_payload ?? "",
    bank_holder: t.bank_holder ?? "",
    bank_ac_no: t.bank_ac_no ?? "",
    bank_ac_confirm: t.bank_ac_no ?? "",
    bank_ifsc: t.bank_ifsc ?? "",
    bank_name: t.bank_name ?? "",
    bank_branch: t.bank_branch ?? "",
    document_label: t.document_label || "Invoice",
    declaration_text: t.declaration_text ?? "",
    terms_text: t.terms_text ?? "",
    jurisdiction_text: t.jurisdiction_text ?? "",
    reminder_enabled: t.reminder_enabled,
    reminder_auto_send: t.reminder_auto_send,
    reminder_days: t.reminder_days,
    default_credit_days: String(t.default_credit_days ?? 0),
    order_min_value: t.order_min_value ? String(Number(t.order_min_value)) : "",
    order_terms_line: t.order_terms_line ?? "",
    order_alert_phone: t.order_alert_phone ?? "",
    catalog_code_prefix: t.catalog_code_prefix ?? "",
    catalog_group_create_policy: t.catalog_group_create_policy,
  };
}

/** What the page sends to the server, both to save and to draw the preview, so the two never disagree. */
function profileBody(f: Partial<FormShape>, gst: boolean, tenant: Tenant | undefined): Record<string, unknown> {
  const body: Record<string, unknown> = {
    phone: orNull(f.phone),
    pincode: orNull(f.pincode),
    city: orNull(f.city),
    address: orNull(f.address),
    upi_id: orNull(f.upi_id),
    upi_qr_payload: orNull(f.upi_qr_payload),
    bank_holder: orNull(f.bank_holder),
    bank_ac_no: orNull(f.bank_ac_no),
    bank_ifsc: orNull(clean(f.bank_ifsc).toUpperCase()),
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
  } else if (tenant?.trade_name) {
    body.trade_name = clean(f.shop_name); // the invoice prints the trade name when there is one
  } else {
    body.legal_name = clean(f.shop_name);
  }
  return body;
}

/** The settings that are not on the bill. Only the panels this person can see are sent, so a role that cannot
 *  open Reminders never overwrites them. */
function settingsBody(
  f: Partial<FormShape>,
  show: { reminders: boolean; catalog: boolean; autoAllowed: boolean },
): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  if (show.reminders) {
    body.reminder_enabled = Boolean(f.reminder_enabled);
    body.reminder_auto_send = Boolean(f.reminder_enabled && f.reminder_auto_send && show.autoAllowed);
    body.reminder_days = clean(f.reminder_days).replace(/\s/g, "");
    body.default_credit_days = Number(clean(f.default_credit_days) || 0);
  }
  if (show.catalog) {
    body.order_min_value = clean(f.order_min_value) === "" ? null : Number(clean(f.order_min_value)).toFixed(2);
    body.order_terms_line = orNull(f.order_terms_line);
    body.order_alert_phone = orNull(f.order_alert_phone);
    body.catalog_code_prefix = orNull(f.catalog_code_prefix);
    body.catalog_group_create_policy = f.catalog_group_create_policy ?? "auto";
  }
  return body;
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

  const methods = useForm<FormShape>();
  const {
    register,
    handleSubmit,
    reset,
    watch,
    setValue,
    formState: { errors },
  } = methods;
  const v = watch();

  const [gst, setGst] = useState(false);
  // Phone: a list of sections, each opening on its own screen (null = the list). Desktop: tabs, Business first.
  const isDesktop = useIsDesktop();
  const [picked, setPicked] = useState<Tab | null>(null);
  const tab: Tab = picked ?? "business";
  const sheet = !isDesktop && picked !== null;
  const [previewOpen, setPreviewOpen] = useState(false);
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

  // Bank and branch come from the IFSC the moment it is complete, unless it is the one already saved.
  const ifscQ = useIfsc(clean(v.bank_ifsc));
  const looked = ifscQ.data;
  useEffect(() => {
    if (!looked || looked.ifsc === (data?.bank_ifsc ?? "").toUpperCase()) return;
    if (looked.bank) setValue("bank_name", looked.bank);
    if (looked.found) setValue("bank_branch", looked.branch ?? "");
  }, [looked, data?.bank_ifsc, setValue]);

  const mut = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<Tenant>("/tenant", { method: "PATCH", body }),
    onSuccess: (t) => {
      qc.setQueryData(["tenant"], t);
      qc.invalidateQueries({ queryKey: ["setup"] });
      qc.invalidateQueries({ queryKey: ["collections-ageing"] }); // credit days move what counts as overdue
      setSaved(true);
      setServerError(null);
      if (!isDesktop) setPicked(null); // back to the list, which shows what was saved
      setTimeout(() => setSaved(false), 2200);
    },
    onError: (e) => setServerError(e instanceof ApiError ? e.message : "Save failed"),
  });

  const owner = me?.role === "owner";
  const showReminders = remindersOn && canWrite(me?.role);
  const showCatalog = Boolean(me?.ext_supplier_catalog);
  const showConnections = tallyOn || owner; // an owner always sees what is on offer, even what the plan leaves out
  const autoAllowed = data?.reminder_auto_allowed !== false;

  const onSubmit = handleSubmit(
    (f) => mut.mutate({ ...profileBody(f, gst, data), ...settingsBody(f, { reminders: showReminders, catalog: showCatalog, autoAllowed }) }),
    (bad) => {
      const first = (Object.keys(bad) as (keyof FormShape)[]).find((k) => TAB_OF_FIELD[k]);
      if (first) setPicked(TAB_OF_FIELD[first]!);
    },
  );

  if (isLoading) return <p className="text-sm text-muted">Loading…</p>;

  const tabs: { key: Tab; label: string }[] = [
    { key: "business", label: "Business" },
    { key: "pay", label: "Get paid" },
    { key: "wording", label: "Bill wording" },
    ...(showReminders ? [{ key: "reminders" as const, label: "Payment reminders" }] : []),
    ...(showCatalog ? [{ key: "catalog" as const, label: "Catalog and orders" }] : []),
    ...(showConnections ? [{ key: "connections" as const, label: "Connections" }] : []),
  ];
  const editing = tab !== "connections";
  const aboutBill = tab === "business" || tab === "pay" || tab === "wording";
  const previewBody = profileBody(v, gst, data);

  const nameNow = gst ? clean(v.trade_name) || clean(v.legal_name) : clean(v.shop_name);
  const summaries: { key: Tab; title: string; line: string; todo: boolean }[] = [
    {
      key: "business",
      title: "Your shop",
      line: [nameNow || "Add your shop name", clean(v.phone) || "add your phone"].join(" · "),
      todo: !nameNow || !clean(v.phone),
    },
    {
      key: "pay",
      title: "Get paid",
      line: clean(v.upi_id) ? `UPI ${clean(v.upi_id)}` : clean(v.bank_ac_no) ? "Bank account added" : "Add your UPI so customers can pay you",
      todo: !clean(v.upi_id) && !clean(v.bank_ac_no),
    },
    {
      key: "wording",
      title: "Bill wording",
      line: `Title: ${clean(v.document_label) || "Invoice"}${clean(v.declaration_text) ? " · declaration set" : ""}${clean(v.terms_text) ? " · terms set" : ""}`,
      todo: false,
    },
    ...(showReminders
      ? [
          {
            key: "reminders" as const,
            title: "Payment reminders",
            line: v.reminder_enabled
              ? `On · remind after ${clean(v.reminder_days).replace(/\s/g, "").split(",").join(", ")} days${v.reminder_auto_send && autoAllowed ? " · sent for you" : ""}`
              : "Off. Turn on to chase overdue bills",
            todo: false,
          },
        ]
      : []),
    ...(showCatalog
      ? [
          {
            key: "catalog" as const,
            title: "Catalog and orders",
            line: [
              clean(v.order_min_value) ? `Minimum order ₹${clean(v.order_min_value)}` : "No minimum order",
              clean(v.order_alert_phone) ? "order alerts on" : "no order alerts",
            ].join(" · "),
            todo: false,
          },
        ]
      : []),
    ...(showConnections
      ? [{ key: "connections" as const, title: "Connections", line: [(tallyOn || owner) && "Tally", owner && "WhatsApp"].filter(Boolean).join(" and "), todo: false }]
      : []),
  ];

  const gstinNow = clean(v.gstin).toUpperCase();
  const gstinOk = gstinNow.length === 15 && !gstinError(gstinNow);
  const stateName = gstinOk ? states.data?.find((s) => s.code === gstinNow.slice(0, 2))?.name : undefined;
  const upiNow = clean(v.upi_id);
  const ifscNow = clean(v.bank_ifsc).toUpperCase();
  const acChanged = Boolean(clean(v.bank_ac_no)) && clean(v.bank_ac_no) !== (data?.bank_ac_no ?? "");
  const labelChoices = LABELS.includes(v.document_label) || !v.document_label ? LABELS : [...LABELS, v.document_label];

  const fieldClass = (bad: boolean) => `field${bad ? " border-danger" : ""}`;

  return (
    <div className="max-w-6xl">
      <div className="mb-4 flex items-baseline justify-between gap-3">
        <h1 className="font-serif text-2xl font-semibold">Firm</h1>
        {saved && <span className="text-xs text-ok">Saved ✓</span>}
      </div>

      <section className={`card mb-4 p-4 sm:p-5${!isDesktop || aboutBill ? "" : " hidden"}`} aria-labelledby="bill-mode">
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
              onClick={() => {
                setGst(o.gstOpt);
                // On a phone, going to GST takes you straight to the one thing it needs
                if (o.gstOpt && !isDesktop && !gstinOk) setPicked("business");
              }}
              className={`rounded-lg border-[1.5px] px-4 py-3 text-left ${
                o.on ? (o.gstOpt ? "border-brass bg-brass-soft" : "border-accent bg-accent-soft") : "border-line bg-card"
              }`}
            >
              <div className="font-semibold">{o.title}</div>
              <div className="text-sm text-muted">{o.hint}</div>
            </button>
          ))}
        </div>
        <div className="mt-3 hidden flex-wrap items-center gap-x-2 gap-y-1.5 text-sm md:flex">
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

      {/* Phone: the sections as a list. Each opens full screen with only its own boxes. */}
      {!isDesktop && picked === null && (
        <div className="mb-4 space-y-3">
          <button type="button" className="btn-primary w-full" onClick={() => setPreviewOpen(true)}>
            See your bill
          </button>
          <div className="card divide-y divide-line">
            {summaries.map((s) => (
              <button key={s.key} type="button" onClick={() => setPicked(s.key)} className="flex w-full items-center gap-3 px-4 py-3.5 text-left">
                <span className="min-w-0 flex-1">
                  <span className="block font-semibold">{s.title}</span>
                  <span className="block truncate text-sm text-muted">{s.line}</span>
                </span>
                {s.todo ? (
                  <span className="rounded-full bg-brass-soft px-2 py-0.5 text-xs text-brass-dark">To do</span>
                ) : (
                  <span className="text-ok" aria-label="Done">✓</span>
                )}
                <span className="text-muted" aria-hidden>
                  ›
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className={`grid gap-5 ${aboutBill ? "lg:grid-cols-[minmax(0,1fr)_400px]" : "lg:max-w-3xl"}`}>
        <div className="min-w-0">
          <div className="mb-4 hidden gap-1 overflow-x-auto border-b border-line md:flex" role="tablist" aria-label="Firm settings">
            {tabs.map((t) => (
              <button
                key={t.key}
                role="tab"
                type="button"
                aria-selected={tab === t.key}
                onClick={() => setPicked(t.key)}
                className={`-mb-px whitespace-nowrap border-b-2 px-4 py-2.5 text-sm font-medium ${
                  tab === t.key ? "border-accent text-ink" : "border-transparent text-muted"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          <div className={sheet ? "fixed inset-0 z-40 flex flex-col bg-ground" : isDesktop ? "" : "hidden"}>
          {sheet && (
            <div className="flex items-center gap-2 border-b border-line bg-card px-2 py-2">
              <button type="button" className="px-3 py-2 text-sm font-medium text-accent" onClick={() => setPicked(null)}>
                ‹ Back
              </button>
              <h2 className="min-w-0 flex-1 truncate text-center font-serif text-base font-semibold">
                {summaries.find((s) => s.key === picked)?.title}
              </h2>
              <button type="button" className="px-3 py-2 text-sm font-medium text-accent" onClick={() => setPreviewOpen(true)}>
                See bill
              </button>
            </div>
          )}
          <div className={sheet ? "flex-1 overflow-y-auto px-4 pt-4" : ""}>
          {tab === "connections" && (
            <>
              {tallyOn ? (
                <>
                  <TallyConnectCard />
                  <TallyBackupsCard />
                </>
              ) : (
                owner && <LockedConnection feature={"tally" as FeatureKey} title="Tally" text="Back up your Tally data to the cloud and bring your customers and items in." />
              )}
              {owner &&
                (whatsappOn ? (
                  <WhatsappConnectCard />
                ) : (
                  <LockedConnection feature="whatsapp" title="WhatsApp Business" text="Send bills and payment reminders to your customers from your own WhatsApp number." />
                ))}
            </>
          )}

          <FormProvider {...methods}>
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
                <p className="mb-3 text-sm text-muted">
                  Customers scan a QR on your bill and the amount is already filled in.
                </p>
                <input type="hidden" {...register("upi_qr_payload")} />
                <UpiQrPicker
                  has={Boolean(clean(v.upi_qr_payload))}
                  onRead={(got) => {
                    setValue("upi_qr_payload", got.payload, { shouldDirty: true });
                    setValue("upi_id", got.upiId, { shouldDirty: true, shouldValidate: true });
                  }}
                  onRemove={() => setValue("upi_qr_payload", "", { shouldDirty: true })}
                />

                <label className="label mt-4" htmlFor="f-upi">
                  {clean(v.upi_qr_payload) ? "UPI ID" : "Or type your UPI ID"}
                </label>
                <input
                  id="f-upi"
                  placeholder="yourshop@okhdfcbank"
                  autoComplete="off"
                  className={fieldClass(Boolean(errors.upi_id))}
                  {...register("upi_id", {
                    // a QR names who it pays; a different ID means that QR is no longer the shop's
                    onChange: () => clean(v.upi_qr_payload) && setValue("upi_qr_payload", ""),
                    validate: (x) => (!clean(x) || UPI_RE.test(clean(x)) ? true : "Use the form name@bank, for example shop@okhdfcbank"),
                  })}
                />
                {errors.upi_id ? (
                  <p className="err">{errors.upi_id.message}</p>
                ) : upiNow && UPI_RE.test(upiNow) ? (
                  upiHandleUnknown(upiNow) ? (
                    <Hint>We do not recognise the part after @. Check it in your UPI app.</Hint>
                  ) : (
                    <Hint tone="ok">Looks right. {clean(v.upi_qr_payload) ? "" : "We make the QR on your bill from it."}</Hint>
                  )
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
                    <label className="label" htmlFor="f-ifsc">IFSC</label>
                    <input
                      id="f-ifsc"
                      maxLength={11}
                      placeholder="SBIN0001234"
                      autoComplete="off"
                      className={`${fieldClass(Boolean(errors.bank_ifsc))} uppercase`}
                      {...register("bank_ifsc", {
                        setValueAs: (x: string) => (x ?? "").trim().toUpperCase(),
                        validate: (x) => (!clean(x) || IFSC_RE.test(clean(x).toUpperCase()) ? true : "IFSC is 4 letters, a zero, then 6 letters or digits"),
                      })}
                    />
                    {errors.bank_ifsc ? (
                      <p className="err">{errors.bank_ifsc.message}</p>
                    ) : !IFSC_RE.test(ifscNow) ? (
                      <Hint>The 11 character code on your cheque book or bank app. We fill in the bank and branch.</Hint>
                    ) : ifscQ.isFetching ? (
                      <Hint>Looking up your bank…</Hint>
                    ) : looked?.found ? (
                      <Hint tone="ok">
                        {[looked.bank, looked.branch, looked.city].filter(Boolean).join(" · ")}
                        {looked.imps === false ? ". This branch does not take IMPS." : ""}
                      </Hint>
                    ) : looked && looked.checked ? (
                      <Hint tone="bad">No bank has this IFSC. Check it against your cheque book.</Hint>
                    ) : (
                      <Hint>Could not look this up just now. Check it, then type the bank name below.</Hint>
                    )}
                  </div>
                  <div className="sm:col-span-2">
                    <label className="label" htmlFor="f-bank">Bank name</label>
                    <input id="f-bank" className="field" {...register("bank_name")} />
                  </div>
                  <div>
                    <label className="label" htmlFor="f-acno">Account number</label>
                    <input
                      id="f-acno"
                      inputMode="numeric"
                      autoComplete="off"
                      className={fieldClass(Boolean(errors.bank_ac_no))}
                      {...register("bank_ac_no", {
                        validate: (x) => (!clean(x) || /^\d{9,18}$/.test(clean(x)) ? true : "Account numbers are 9 to 18 digits"),
                      })}
                    />
                    {errors.bank_ac_no && <p className="err">{errors.bank_ac_no.message}</p>}
                  </div>
                  {acChanged && (
                    <div>
                      <label className="label" htmlFor="f-acno2">Type it again</label>
                      <input
                        id="f-acno2"
                        inputMode="numeric"
                        autoComplete="off"
                        className={fieldClass(Boolean(errors.bank_ac_confirm))}
                        {...register("bank_ac_confirm", {
                          validate: (x) => (clean(x) === clean(v.bank_ac_no) ? true : "The two numbers do not match"),
                        })}
                      />
                      {errors.bank_ac_confirm ? <p className="err">{errors.bank_ac_confirm.message}</p> : <Hint>A wrong digit here prints on every bill.</Hint>}
                    </div>
                  )}
                  <div className="sm:col-span-2">
                    <label className="label" htmlFor="f-holder">Account holder name</label>
                    <input id="f-holder" className="field" {...register("bank_holder")} />
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

            {showReminders && (
              <div hidden={tab !== "reminders"}>
                <RemindersPanel autoAllowed={autoAllowed} />
              </div>
            )}
            {showCatalog && (
              <div hidden={tab !== "catalog"}>
                <CatalogPanel />
              </div>
            )}

            {serverError && <p className="err mt-3">{serverError}</p>}
            <div className="sticky bottom-0 -mx-1 mt-4 flex items-center justify-end gap-3 bg-ground/95 px-1 py-3">
              {saved && <span className="text-sm text-ok">Saved ✓</span>}
              <button type="submit" className={gst ? "btn-brass" : "btn-primary"} disabled={mut.isPending}>
                {mut.isPending ? "Saving…" : "Save changes"}
              </button>
            </div>
          </form>
          </FormProvider>
          </div>
          </div>

          <div className={`card mt-5 p-4 sm:p-6${sheet ? " hidden" : ""}`}>
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

        <aside className={`${aboutBill ? "hidden md:block" : "hidden"} min-w-0 lg:sticky lg:top-4 lg:self-start`} aria-label="Bill preview">
          <div className="mb-2 flex items-baseline justify-between gap-2">
            <h2 className="font-serif text-base font-semibold">Your bill, as it prints</h2>
            <button type="button" className="text-sm text-accent underline underline-offset-2" onClick={() => setPreviewOpen(true)}>
              Open full size
            </button>
          </div>
          <BillPreviewFrame body={previewBody} />
          <p className="mt-2 text-xs text-muted">Sample items. The layout is the real bill, filled with what you type.</p>
        </aside>
      </div>

      {previewOpen && <BillPreviewModal body={previewBody} onClose={() => setPreviewOpen(false)} />}
    </div>
  );
}
