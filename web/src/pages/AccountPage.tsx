import { useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { PASSWORD_HINT, passwordProblem } from "../lib/password";
import { PageHeader } from "../components/PageHeader";

const message = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Try again.");

/** Current + new + confirm. Shared by My account and the forced change after a Fleek-set password. */
function ChangePasswordForm({ onDone, submitLabel }: { onDone?: () => void; submitLabel: string }) {
  const { acceptToken } = useAuth();
  const [cur, setCur] = useState("");
  const [pw, setPw] = useState("");
  const [again, setAgain] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);

  const problem = pw ? passwordProblem(pw) : null;
  const mismatch = again !== "" && again !== pw;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setOk(false);
    setBusy(true);
    try {
      const r = await api<{ access_token: string }>("/auth/change-password", {
        method: "POST",
        body: { current_password: cur, new_password: pw },
      });
      await acceptToken(r.access_token);
      setCur("");
      setPw("");
      setAgain("");
      setOk(true);
      onDone?.();
    } catch (x) {
      setErr(message(x));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <div>
        <label className="label">Current password</label>
        <input className="field" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} required />
      </div>
      <div>
        <label className="label">New password</label>
        <input className="field" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} required />
        <p className="meta mt-1">{PASSWORD_HINT}</p>
        {problem && <p className="err">{problem}</p>}
      </div>
      <div>
        <label className="label">Confirm new password</label>
        <input className="field" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required />
        {mismatch && <p className="err">The two passwords do not match</p>}
      </div>
      {err && <p className="err" role="alert">{err}</p>}
      {ok && <p className="text-sm text-ok" role="status">Password changed. Your other devices are signed out.</p>}
      <button type="submit" className="btn-primary" disabled={busy || !cur || !pw || !!problem || pw !== again}>
        {busy ? "Please wait…" : submitLabel}
      </button>
    </form>
  );
}

/** Shown instead of the whole app when Fleek set this person's password. */
export function ForcedPasswordPage() {
  const { logout, me } = useAuth();
  return (
    <div className="grid min-h-[100dvh] place-items-center px-4 py-8">
      <div className="w-full max-w-sm">
        <h1 className="mb-3 font-serif text-2xl font-semibold">Choose a new password</h1>
        <div className="mb-4 rounded-lg border border-[#ecdca7] bg-[#faf3e0] px-3 py-2 text-sm">
          Your password was set by Fleek. Choose your own to continue.
        </div>
        <div className="card p-6">
          <ChangePasswordForm submitLabel="Save and continue" />
        </div>
        <p className="mt-5 text-center text-sm text-muted">
          {me?.label} ·{" "}
          <button onClick={logout} className="text-accent hover:text-accent-dark">Sign out</button>
        </p>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card p-5">
      <h2 className="section-title mb-3">{title}</h2>
      {children}
    </section>
  );
}

function Verified({ ok }: { ok: boolean }) {
  return (
    <span className={`ml-2 rounded-full px-2 py-0.5 text-[11px] font-medium ${ok ? "bg-[#e6efe8] text-[#3a5a48]" : "bg-[#efe9df] text-muted"}`}>
      {ok ? "Verified" : "Not verified"}
    </span>
  );
}

export function AccountPage() {
  const { me, acceptToken } = useAuth();
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function signOutOthers() {
    setMsg(null);
    setErr(null);
    try {
      const r = await api<{ access_token: string }>("/auth/sign-out-others", { method: "POST" });
      await acceptToken(r.access_token);
      setMsg("Every other device has been signed out.");
    } catch (x) {
      setErr(message(x));
    }
  }

  return (
    <div className="mx-auto max-w-xl space-y-4">
      <PageHeader title="My account" />
      <Section title="Profile">
        <dl className="grid grid-cols-[90px_1fr] gap-y-2 text-sm">
          <dt className="text-muted">Firm</dt>
          <dd>{me?.firm_name ?? "—"}</dd>
          <dt className="text-muted">Mobile</dt>
          <dd>
            {me?.mobile_masked ?? "Not on file"}
            {me?.mobile_masked && <Verified ok={!!me?.mobile_verified} />}
            {me?.mobile_masked && <span className="meta ml-2">your sign-in</span>}
          </dd>
          <dt className="text-muted">Email</dt>
          <dd>{me?.email ?? <span className="text-muted">Not given</span>}</dd>
          <dt className="text-muted">Role</dt>
          <dd className="capitalize">{me?.role}</dd>
        </dl>
        <p className="meta mt-3">Wrong mobile or email? Ask Fleek to correct it.</p>
      </Section>

      <Section title="Password">
        <ChangePasswordForm submitLabel="Change password" />
      </Section>

      <Section title="Plan">
        <p className="text-sm">
          {me?.plan_tier === "free" ? "Free" : me?.licence?.plan_label ?? "Your plan"}
          {me?.licence?.status === "trial" && me.plan_tier !== "free" && ` · free trial, ${me.licence.trial_days_left ?? 0} days left`}
        </p>
        {me?.role === "owner" && (
          <Link to="/plans" className="btn-ghost mt-3 inline-flex">See plans and billing</Link>
        )}
      </Section>

      <Section title="Devices">
        <p className="meta mb-3">Lost a phone, or signed in on a shared computer?</p>
        <button className="btn-ghost" onClick={() => void signOutOthers()}>
          Sign out of all other devices
        </button>
        {msg && <p className="mt-2 text-sm text-ok" role="status">{msg}</p>}
        {err && <p className="err" role="alert">{err}</p>}
      </Section>

      <p className="meta">App build: {__BUILD_ID__}</p>
      <p className="text-sm text-muted">
        Need to close the account? <Link className="text-danger underline" to="/delete-account">Request deletion</Link>
      </p>
    </div>
  );
}
