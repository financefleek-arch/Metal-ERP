import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../lib/api";
import type { TallyCompanyRow } from "../lib/types";

const KINDS: { value: string; label: string }[] = [
  { value: "gst", label: "GST books" },
  { value: "estimate", label: "Estimate book" },
  { value: "branch", label: "Branch" },
  { value: "fy", label: "Earlier year" },
  { value: "other", label: "Other" },
];
const ROLE_CHOICES: { value: string; label: string }[] = [
  { value: "accountant", label: "Accountant" },
  { value: "counter", label: "Counter" },
  { value: "viewer", label: "Viewer" },
];

const kindLabel = (k: string) => KINDS.find((x) => x.value === k)?.label ?? k;

function Verdict({ c }: { c: TallyCompanyRow }) {
  if (!c.connected) return <span className="meta">Not linked to an agent</span>;
  if (c.verify_state === "verified")
    return <span className="text-xs font-semibold text-ok">Open in Tally and matched</span>;
  if (c.verify_state === "not_open")
    return <span className="text-xs font-semibold text-warn">Not open in Tally right now</span>;
  if (c.verify_state === "renamed" || c.verify_state === "different")
    return <span className="text-xs font-semibold text-warn">{c.verify_note ?? "Tally has a different company open"}</span>;
  return <span className="meta">Not checked yet</span>;
}

/**
 * The firm's Tally companies (S1.4): the owner adds, edits, makes default and switches off their own
 * companies. A firm with one company sees a single card and one link; everything about several lives
 * behind "Add another company".
 */
export function TallyCompaniesCard({ owner }: { owner: boolean }) {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["tally-companies"],
    queryFn: () => api<TallyCompanyRow[]>("/tally/companies"),
  });
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [panel, setPanel] = useState<{ id: string; kind: "ledgers" | "relink" } | null>(null);

  const refresh = () => qc.invalidateQueries({ queryKey: ["tally-companies"] });
  const fail = (e: unknown) =>
    setErr(
      e instanceof ApiError
        ? typeof e.detail === "object" && e.detail && "message" in (e.detail as object)
          ? String((e.detail as { message: string }).message)
          : e.message
        : "Something went wrong. Try again.",
    );

  const act = useMutation({
    mutationFn: (v: { path: string; body?: unknown; method?: string }) =>
      api(v.path, { method: v.method ?? "POST", body: v.body }),
    onSuccess: () => {
      setErr(null);
      refresh();
    },
    onError: fail,
  });

  const companies = list.data ?? [];
  if (list.isLoading) return null;
  const several = companies.length > 1;

  return (
    <div className="card mb-5 p-4 sm:p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h3 className="section-title">{several ? "Tally companies" : "Tally company"}</h3>
          {several && (
            <p className="meta mt-1 max-w-prose">
              Each company is a set of books in Tally. A bill posts to one of them; the default is used when a
              bill does not say.
            </p>
          )}
        </div>
        {owner && editing === null && (
          <button className="btn-ghost" onClick={() => setEditing("new")}>
            {companies.length === 0 ? "Add your Tally company" : "Add another company"}
          </button>
        )}
      </div>

      {err && <p role="alert" className="mt-3 text-sm text-danger">{err}</p>}

      {companies.length === 0 && editing === null && (
        <p className="meta mt-3 max-w-prose">
          No Tally company linked yet. Install the Tally agent on the shop PC, then add the company exactly as
          Tally spells it.
        </p>
      )}

      <ul className="mt-4 flex flex-col gap-3">
        {companies.map((c) =>
          editing === c.id ? (
            <li key={c.id}>
              <CompanyForm
                company={c}
                onCancel={() => setEditing(null)}
                onSaved={() => {
                  setEditing(null);
                  setErr(null);
                  refresh();
                }}
                onError={fail}
              />
            </li>
          ) : (
            <li key={c.id} className="rounded-lg border border-line p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">{c.company_name}</span>
                {c.is_default && several && (
                  <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-semibold text-accent-dark">
                    Default
                  </span>
                )}
                {several && (
                  <span className="rounded-full bg-ground px-2 py-0.5 text-[11px] text-muted">{kindLabel(c.kind)}</span>
                )}
                {c.status === "disabled" && (
                  <span className="rounded-full bg-ground px-2 py-0.5 text-[11px] font-semibold text-muted">Switched off</span>
                )}
                <span className="flex-1" />
                <Verdict c={c} />
              </div>
              {several && (
                <dl className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                  <div>
                    <dt className="meta">Bill numbers</dt>
                    <dd className="font-medium">{c.series}</dd>
                  </div>
                  <div>
                    <dt className="meta">Bill title</dt>
                    <dd className="font-medium">{c.document_label ?? "Firm's title"}</dd>
                  </div>
                  <div>
                    <dt className="meta">In payment reminders</dt>
                    <dd className="font-medium">{c.include_in_reminders ? "Yes" : "No"}</dd>
                  </div>
                  <div>
                    <dt className="meta">Who can see it</dt>
                    <dd className="font-medium">
                      {c.visible_roles && c.visible_roles.length > 0
                        ? `Owner and ${c.visible_roles.join(", ")}`
                        : "Everyone"}
                    </dd>
                  </div>
                </dl>
              )}
              <div className="mt-3 flex flex-wrap gap-4 text-sm">
                <button
                  className="font-medium text-accent"
                  onClick={() => act.mutate({ path: `/tally/companies/${c.id}/verify` })}
                >
                  Check it in Tally
                </button>
                {c.connected && (
                  <button
                    className="font-medium text-accent"
                    onClick={() => act.mutate({ path: `/tally/companies/${c.id}/pull-masters` })}
                  >
                    Pull items and parties
                  </button>
                )}
                {c.connected && (
                  <button className="font-medium text-accent" onClick={() => setPanel({ id: c.id, kind: "ledgers" })}>
                    Ledger names
                  </button>
                )}
                {(c.verify_state === "renamed" || c.verify_state === "different" || c.verify_state === "not_open") && (
                  <button className="font-medium text-accent" onClick={() => setPanel({ id: c.id, kind: "relink" })}>
                    Use the company that is open
                  </button>
                )}
                {owner && (
                  <button className="font-medium text-accent" onClick={() => setEditing(c.id)}>
                    Edit
                  </button>
                )}
                {owner && !c.is_default && c.status === "active" && (
                  <button
                    className="font-medium text-accent"
                    onClick={() => act.mutate({ path: `/tally/companies/${c.id}/default` })}
                  >
                    Make default
                  </button>
                )}
                {owner && !c.is_default && (
                  <button
                    className="font-medium text-accent"
                    onClick={() =>
                      act.mutate({ path: `/tally/companies/${c.id}/active`, body: { active: c.status !== "active" } })
                    }
                  >
                    {c.status === "active" ? "Switch off" : "Switch on"}
                  </button>
                )}
              </div>
              {panel?.id === c.id && panel.kind === "ledgers" && (
                <LedgerForm
                  company={c}
                  onClose={() => setPanel(null)}
                  onSaved={() => {
                    setPanel(null);
                    setErr(null);
                    refresh();
                  }}
                  onError={fail}
                />
              )}
              {panel?.id === c.id && panel.kind === "relink" && (
                <RelinkPanel
                  company={c}
                  onClose={() => setPanel(null)}
                  onSaved={() => {
                    setPanel(null);
                    setErr(null);
                    refresh();
                  }}
                  onError={fail}
                />
              )}
            </li>
          ),
        )}
        {editing === "new" && (
          <li>
            <CompanyForm
              onCancel={() => setEditing(null)}
              onSaved={() => {
                setEditing(null);
                setErr(null);
                refresh();
              }}
              onError={fail}
            />
          </li>
        )}
      </ul>
    </div>
  );
}

const LEDGERS: { key: string; label: string }[] = [
  { key: "sales_ledger", label: "Sales ledger" },
  { key: "purchase_ledger", label: "Purchase ledger" },
  { key: "cash_ledger", label: "Cash ledger" },
  { key: "bank_ledger", label: "Bank ledger" },
  { key: "round_off_ledger", label: "Round-off ledger" },
  { key: "cgst_ledger", label: "CGST ledger" },
  { key: "sgst_ledger", label: "SGST ledger" },
  { key: "igst_ledger", label: "IGST ledger" },
];

/** The ledger names this company's books use, blank where Tally's default is fine (S1.5). */
function LedgerForm({
  company,
  onClose,
  onSaved,
  onError,
}: {
  company: TallyCompanyRow;
  onClose: () => void;
  onSaved: () => void;
  onError: (e: unknown) => void;
}) {
  const [vals, setVals] = useState<Record<string, string>>(
    Object.fromEntries(LEDGERS.map((l) => [l.key, company.ledger_map[l.key] ?? ""])),
  );
  const save = useMutation({
    mutationFn: () => api(`/tally/companies/${company.id}/ledger-map`, { method: "PUT", body: vals }),
    onSuccess: onSaved,
    onError,
  });
  return (
    <form
      className="mt-3 rounded-lg border border-line bg-ground p-4"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <p className="meta mb-3">Exactly as the ledgers are named in this company. Leave blank to use the usual name.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {LEDGERS.map((l) => (
          <div key={l.key}>
            <label className="label" htmlFor={`lg-${company.id}-${l.key}`}>{l.label}</label>
            <input
              id={`lg-${company.id}-${l.key}`}
              className="field"
              value={vals[l.key]}
              onChange={(e) => setVals({ ...vals, [l.key]: e.target.value })}
            />
          </div>
        ))}
      </div>
      <div className="mt-4 flex gap-2">
        <button type="submit" className="btn-primary" disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save"}
        </button>
        <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
      </div>
    </form>
  );
}

/** Pick which open Tally company this one now is, after a rename or a new year (S1.8). */
function RelinkPanel({
  company,
  onClose,
  onSaved,
  onError,
}: {
  company: TallyCompanyRow;
  onClose: () => void;
  onSaved: () => void;
  onError: (e: unknown) => void;
}) {
  const open = useQuery({
    queryKey: ["tally-companies-open"],
    queryFn: () => api<{ name: string; guid: string; books_from: string | null }[]>("/tally/companies/open/now"),
  });
  const relink = useMutation({
    mutationFn: (guid: string) => api(`/tally/companies/${company.id}/relink`, { body: { guid } }),
    onSuccess: onSaved,
    onError,
  });
  const rows = open.data ?? [];
  return (
    <div className="mt-3 rounded-lg border border-line bg-ground p-4">
      <p className="meta mb-3">Companies open in Tally right now. Pick the one that is this company.</p>
      {open.isLoading ? (
        <p className="meta">Looking…</p>
      ) : rows.length === 0 ? (
        <p className="meta">Nothing open in Tally right now. Open the company in Tally and try again.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {rows.map((r) => (
            <li key={r.guid} className="flex items-center justify-between gap-3 text-sm">
              <span className="font-medium">{r.name}</span>
              <button className="btn-ghost" disabled={relink.isPending} onClick={() => relink.mutate(r.guid)}>
                Use this one
              </button>
            </li>
          ))}
        </ul>
      )}
      <button className="mt-3 text-sm font-medium text-accent" onClick={onClose}>Close</button>
    </div>
  );
}

function CompanyForm({
  company,
  onCancel,
  onSaved,
  onError,
}: {
  company?: TallyCompanyRow;
  onCancel: () => void;
  onSaved: () => void;
  onError: (e: unknown) => void;
}) {
  const [name, setName] = useState(company?.company_name ?? "");
  const [kind, setKind] = useState(company?.kind ?? "gst");
  const [label, setLabel] = useState(company?.document_label ?? "");
  const [prefix, setPrefix] = useState(company?.series_prefix ?? "");
  const [reminders, setReminders] = useState(company?.include_in_reminders ?? true);
  const [roles, setRoles] = useState<string[]>(company?.visible_roles ?? ROLE_CHOICES.map((r) => r.value));

  const toggle = (r: string) => setRoles((cur) => (cur.includes(r) ? cur.filter((x) => x !== r) : [...cur, r]));
  // every role ticked means "everyone": stored as null, so a role added later is not locked out
  const allTicked = ROLE_CHOICES.every((r) => roles.includes(r.value));

  const save = useMutation({
    mutationFn: () =>
      company
        ? api(`/tally/companies/${company.id}`, {
            method: "PATCH",
            body: {
              company_name: name,
              kind,
              document_label: label,
              series_prefix: prefix,
              include_in_reminders: reminders,
              visible_roles: allTicked ? null : roles,
            },
          })
        : api("/tally/companies", { method: "POST", body: { company_name: name, kind } }),
    onSuccess: onSaved,
    onError,
  });

  return (
    <form
      className="rounded-lg border border-accent/40 bg-accent-soft/30 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label className="label" htmlFor="tc-name">Company name in Tally</label>
          <input id="tc-name" className="field" value={name} onChange={(e) => setName(e.target.value)} required />
          <p className="meta mt-1">Exactly as Tally spells it. We check it against what is open in Tally.</p>
        </div>
        <div>
          <label className="label" htmlFor="tc-kind">What it is</label>
          <select id="tc-kind" className="field" value={kind} onChange={(e) => setKind(e.target.value)}>
            {KINDS.map((k) => (
              <option key={k.value} value={k.value}>{k.label}</option>
            ))}
          </select>
        </div>
        {company && (
          <>
            <div>
              <label className="label" htmlFor="tc-label">Bill title</label>
              <input id="tc-label" className="field" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Blank uses the firm's title" />
            </div>
            <div>
              <label className="label" htmlFor="tc-prefix">Bill number prefix</label>
              <input id="tc-prefix" className="field" value={prefix} onChange={(e) => setPrefix(e.target.value)} placeholder="Blank picks one for you" />
              <p className="meta mt-1">Numbers run on their own from 1 for this company.</p>
            </div>
            <fieldset>
              <legend className="label">Who can see this company</legend>
              <p className="meta mb-1">You always can.</p>
              {ROLE_CHOICES.map((r) => (
                <label key={r.value} className="flex min-h-[32px] items-center gap-2 text-sm">
                  <input type="checkbox" checked={roles.includes(r.value)} onChange={() => toggle(r.value)} />
                  {r.label}
                </label>
              ))}
            </fieldset>
            <div className="sm:col-span-2">
              <label className="flex min-h-[32px] items-center gap-2 text-sm">
                <input type="checkbox" checked={reminders} onChange={(e) => setReminders(e.target.checked)} />
                Include its unpaid bills in payment reminders
              </label>
              <p className="meta ml-6">
                Off for an estimate book, so a customer is not chased for money that belongs in the other books.
              </p>
            </div>
          </>
        )}
      </div>
      <div className="mt-4 flex gap-2">
        <button type="submit" className="btn-primary" disabled={save.isPending || !name.trim()}>
          {save.isPending ? "Saving…" : company ? "Save" : "Add company"}
        </button>
        <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}
