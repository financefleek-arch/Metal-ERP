import { useState } from "react";
import { useNavigate } from "react-router-dom";
import type { FormEvent, ReactNode } from "react";
import { ApiError, api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { PASSWORD_HINT, passwordProblem } from "../lib/password";
import { CodeField, ResendLink } from "../components/CodeField";
import { Turnstile } from "../components/Turnstile";
import { useCaptchaSiteKey } from "../lib/captcha";
import { LandingPage } from "./LandingPage";
import { markLandingSeen, shouldShowLanding } from "../lib/landing";
import { adClick, noteSignup, track } from "../lib/analytics";

// The policy of record (Section 12 covers Metal ERP); see pages/public/LegalPages.tsx.
const PRIVACY_POLICY = "https://fleekfintech.com/privacy-policy/";
const TERMS = "https://fleekfintech.com/terms/";

type Mode = "landing" | "login" | "signup" | "verify" | "forgot";

interface Started {
  request_id: string;
  mobile_masked: string;
}

/** What the sign-up form holds, kept here so "Edit" on the code screen does not empty it. */
interface Draft {
  firm_name: string;
  mobile: string;
}

const message = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Try again.");

function Shell({ title, sub, step, children }: { title: string; sub?: string; step?: 1 | 2; children: ReactNode }) {
  return (
    <div className="grid min-h-[100dvh] place-items-center px-4 py-8">
      <div className="w-full max-w-sm">
        {step && (
          <div className="mb-4 flex gap-1.5" aria-label={`Step ${step} of 2`}>
            <i className="h-1 flex-1 rounded bg-accent" />
            <i className={`h-1 flex-1 rounded ${step === 2 ? "bg-accent" : "bg-line"}`} />
          </div>
        )}
        <h1 className="mb-1 font-serif text-2xl font-semibold">{title}</h1>
        {sub && <p className="mb-5 text-sm text-muted">{sub}</p>}
        {children}
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div>
      <label className="label">{label}</label>
      {children}
      {hint && <p className="meta mt-1">{hint}</p>}
    </div>
  );
}

/** Mobile is the sign-in. An older account (made before mobile sign-in) can still use its email, one tap away. */
function IdentityField({
  value,
  onChange,
  byEmail,
  onToggle,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  byEmail: boolean;
  onToggle: () => void;
  disabled?: boolean;
}) {
  return (
    <div>
      <label className="label">{byEmail ? "Email" : "Mobile number"}</label>
      {byEmail ? (
        <input className="field" type="email" autoComplete="username" value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} required />
      ) : (
        <div className="flex gap-2">
          <span className="field flex w-14 flex-none items-center justify-center text-muted">+91</span>
          <input className="field" type="tel" inputMode="numeric" autoComplete="username" value={value} onChange={(e) => onChange(e.target.value)} placeholder="98765 43210" disabled={disabled} required />
        </div>
      )}
      {!disabled && (
        <button type="button" onClick={onToggle} className="meta mt-1 text-accent hover:text-accent-dark">
          {byEmail ? "Use my mobile number" : "Older account? Use my email instead"}
        </button>
      )}
    </div>
  );
}

export function AuthPage() {
  // a first visit in a browser (an ad click, say) sees what the product is before a form
  const [mode, setMode] = useState<Mode>(() => (shouldShowLanding() ? "landing" : "login"));
  const [started, setStarted] = useState<Started | null>(null);
  const [draft, setDraft] = useState<Draft>({ firm_name: "", mobile: "" });
  const go = (m: Mode) => {
    if (m !== "landing") markLandingSeen();
    setMode(m);
  };

  if (mode === "landing") return <LandingPage onSignup={() => go("signup")} onSignin={() => go("login")} />;

  if (mode === "signup")
    return <SignupForm draft={draft} onDraft={setDraft} onStarted={(s) => { setStarted(s); go("verify"); }} onBack={() => go("login")} />;
  if (mode === "verify" && started)
    return <VerifyForm started={started} shown={draft.mobile} onBack={() => go("signup")} />;
  if (mode === "forgot") return <ForgotForm onBack={() => go("login")} />;
  return (
    <LoginForm
      onSignup={(mobile) => {
        if (mobile) setDraft((d) => ({ ...d, mobile }));
        go("signup");
      }}
      onForgot={() => go("forgot")}
    />
  );
}

// ------------------------------------------------------------------------------------------ sign in

function LoginForm({ onSignup, onForgot }: { onSignup: (mobile?: string) => void; onForgot: () => void }) {
  const { login } = useAuth();
  const [who, setWho] = useState("");
  const [byEmail, setByEmail] = useState(false);
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await login(who.trim(), password);
    } catch (x) {
      setErr(message(x));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="Tally Companion" sub="Sign in to your firm">
      <form onSubmit={submit} className="card space-y-4 p-6">
        <IdentityField value={who} onChange={setWho} byEmail={byEmail} onToggle={() => { setByEmail(!byEmail); setWho(""); }} />
        <div>
          <div className="flex items-baseline justify-between">
            <label className="label">Password</label>
            <button type="button" onClick={onForgot} className="mb-1 text-xs text-accent hover:text-accent-dark">
              Forgot password?
            </button>
          </div>
          <input className="field" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>
        {err && <p className="err" role="alert">{err}</p>}
        {err && !byEmail && (
          // The same answer for every number (it never says whether one is registered), with the way forward beside it.
          <div className="rounded-lg border border-[#ecdca7] bg-brass-soft px-3 py-2.5 text-sm">
            <b>New here?</b> It takes 2 minutes to create your free account.
            <button type="button" onClick={() => onSignup(who)} data-umami-event="login-error-create-account" className="btn-brass mt-2 w-full">
              Create my free account
            </button>
          </div>
        )}
        <button type="submit" className="btn-primary w-full" disabled={busy}>
          {busy ? "Please wait…" : "Sign in"}
        </button>
      </form>
      <p className="mt-5 text-center text-sm text-muted">
        New to Tally Companion?{" "}
        <button onClick={() => onSignup(who)} className="text-accent hover:text-accent-dark">
          Create a free account
        </button>
      </p>
    </Shell>
  );
}

// ------------------------------------------------------------------------------------------ sign up

function SignupForm({
  draft,
  onDraft,
  onStarted,
  onBack,
}: {
  draft: Draft;
  onDraft: (d: Draft) => void;
  onStarted: (s: Started) => void;
  onBack: () => void;
}) {
  const f = draft;
  const [terms, setTerms] = useState(false);
  const { siteKey, ready: captchaReady } = useCaptchaSiteKey();
  const [token, setToken] = useState<string | null>(null);
  const [tsKey, setTsKey] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof Draft) => (e: { target: { value: string } }) => onDraft({ ...f, [k]: e.target.value });

  // Only what proves it is you. The password and an email come once you are in.
  const ready = f.firm_name.trim().length >= 2 && f.mobile.replace(/\D/g, "").length >= 10 && terms && captchaReady && (!siteKey || !!token);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const r = await api<Started>("/auth/signup/start", {
        method: "POST",
        auth: false,
        body: { firm_name: f.firm_name.trim(), mobile: f.mobile, accept_terms: terms, captcha_token: token, ...adClick() },
      });
      track("signup-codes-sent");
      onStarted(r);
    } catch (x) {
      setErr(message(x));
      setToken(null);
      setTsKey((k) => k + 1); // a token works once
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="Create your free account" sub="Free, no card. Takes 2 minutes." step={1}>
      <form onSubmit={submit} className="card space-y-4 p-6">
        <Field label="Firm name">
          <input className="field" autoFocus value={f.firm_name} onChange={set("firm_name")} maxLength={200} required />
        </Field>
        <Field label="Mobile (WhatsApp)" hint="We send a code here on WhatsApp. You will sign in with this number.">
          <div className="flex gap-2">
            <span className="field flex w-14 flex-none items-center justify-center text-muted">+91</span>
            <input className="field" type="tel" inputMode="numeric" autoComplete="tel-national" value={f.mobile} onChange={set("mobile")} placeholder="98765 43210" required />
          </div>
        </Field>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1 h-4 w-4" checked={terms} onChange={(e) => setTerms(e.target.checked)} />
          <span>
            I agree to the{" "}
            <a className="text-accent underline" href={TERMS} target="_blank" rel="noreferrer">Terms</a> and have read the{" "}
            <a className="text-accent underline" href={PRIVACY_POLICY} target="_blank" rel="noreferrer">Privacy Policy</a>
            <span className="meta mt-1 block">
              We share how you use the app (counts, such as invoices made) with Fleek to help you get started. Never your customers, items or amounts.
            </span>
          </span>
        </label>
        <Turnstile key={tsKey} siteKey={siteKey} onToken={setToken} />
        {err && <p className="err" role="alert">{err}</p>}
        <button type="submit" className="btn-brass w-full" disabled={busy || !ready}>
          {busy ? "Sending code…" : "Send code"}
        </button>
      </form>
      <p className="mt-5 text-center text-sm text-muted">
        Already have an account?{" "}
        <button onClick={onBack} className="text-accent hover:text-accent-dark">Sign in</button>
      </p>
    </Shell>
  );
}

// ------------------------------------------------------------------------------------------- the code

function VerifyForm({ started, shown, onBack }: { started: Started; shown: string; onBack: () => void }) {
  const { acceptToken } = useAuth();
  const navigate = useNavigate();
  const [wa, setWa] = useState("");
  const [asked, setAsked] = useState(false);
  const [askErr, setAskErr] = useState<string | null>(null);

  async function askForCall() {
    setAskErr(null);
    try {
      await api<void>("/auth/signup/callback", { method: "POST", auth: false, body: { request_id: started.request_id } });
      track("signup-callback-asked");
      setAsked(true);
    } catch (x) {
      setAskErr(message(x));
    }
  }
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const resend = async () => {
    setErr(null);
    try {
      await api<void>("/auth/signup/resend", { method: "POST", auth: false, body: { request_id: started.request_id } });
    } catch (x) {
      setErr(message(x));
      throw x;
    }
  };

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const r = await api<{ access_token: string }>("/auth/signup/verify", {
        method: "POST",
        auth: false,
        body: { request_id: started.request_id, code: wa },
      });
      noteSignup();
      track("signup-complete");
      // a brand-new account lands on its own page, which the ad campaign counts as a sign-up
      navigate("/welcome", { replace: true });
      await acceptToken(r.access_token);
    } catch (x) {
      setErr(message(x));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="Check your WhatsApp" sub="Your account is created once the code is right." step={2}>
      <div className="card mb-3 flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
        <span>
          <span className="meta block">Code sent to</span>
          <b>+91 {shown.replace(/\D/g, "").slice(-10) || started.mobile_masked}</b>
        </span>
        <button type="button" onClick={onBack} className="text-accent hover:text-accent-dark">Wrong number? Edit</button>
      </div>
      <form onSubmit={submit} className="card space-y-5 p-6">
        <div className="space-y-1">
          <CodeField label="WhatsApp code" value={wa} onChange={setWa} autoFocus />
          <ResendLink onResend={resend} />
        </div>
        {err && <p className="err" role="alert">{err}</p>}
        <button type="submit" className="btn-brass w-full" disabled={busy || wa.length !== 6}>
          {busy ? "Creating your account…" : "Create my account"}
        </button>
      </form>
      <div className="mt-4 rounded-xl border border-[#ecdca7] bg-brass-soft px-4 py-3 text-sm">
        <b>No code yet?</b>
        <ul className="mb-2 mt-1 list-disc pl-5">
          <li>WhatsApp must be installed on this number</li>
          <li>Open WhatsApp and look for a message from Fleek</li>
          <li>Check the number above</li>
        </ul>
        {asked ? (
          <p role="status"><b>Thank you.</b> We will call you on this number.</p>
        ) : (
          <button type="button" onClick={() => void askForCall()} className="text-accent underline hover:text-accent-dark">
            Still nothing? Ask us to call you
          </button>
        )}
        {askErr && <p className="err" role="alert">{askErr}</p>}
      </div>
    </Shell>
  );
}

// ---------------------------------------------------------------------------------- forgot password

function ForgotForm({ onBack }: { onBack: () => void }) {
  const { acceptToken } = useAuth();
  const { siteKey, ready: captchaReady } = useCaptchaSiteKey();
  const [who, setWho] = useState("");
  const [byEmail, setByEmail] = useState(false);
  const [sent, setSent] = useState(false);
  const [code, setCode] = useState("");
  const [pw, setPw] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const [tsKey, setTsKey] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function send() {
    setErr(null);
    setBusy(true);
    try {
      await api<void>("/auth/password/forgot", { method: "POST", auth: false, body: { identifier: who.trim(), captcha_token: token } });
      setSent(true);
    } catch (x) {
      setErr(message(x));
    } finally {
      setToken(null);
      setTsKey((k) => k + 1);
      setBusy(false);
    }
  }

  async function reset(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const r = await api<{ access_token: string }>("/auth/password/reset", {
        method: "POST",
        auth: false,
        body: { identifier: who.trim(), code, new_password: pw },
      });
      await acceptToken(r.access_token);
    } catch (x) {
      setErr(message(x));
    } finally {
      setBusy(false);
    }
  }

  const needToken = !captchaReady || (!!siteKey && !token);
  const validWho = byEmail ? who.includes("@") : who.replace(/\D/g, "").length >= 10;
  return (
    <Shell title="Reset your password" sub={byEmail ? "We send a code to the email on your account." : "We send a code on WhatsApp to the number on your account."}>
      <div className="card space-y-4 p-6">
        <IdentityField value={who} onChange={setWho} byEmail={byEmail} onToggle={() => { setByEmail(!byEmail); setWho(""); }} disabled={sent} />
        {!sent && (
          <>
            <Turnstile key={tsKey} siteKey={siteKey} onToken={setToken} />
            <button className="btn-primary w-full" disabled={busy || !validWho || needToken} onClick={() => void send()}>
              {busy ? "Sending…" : "Send code"}
            </button>
          </>
        )}
        {sent && (
          <form onSubmit={reset} className="space-y-4">
            <p className="meta">If that {byEmail ? "email" : "number"} has an account, a code is on its way.</p>
            <CodeField label="Code" value={code} onChange={setCode} autoFocus />
            <Field label="New password" hint={PASSWORD_HINT}>
              <input className="field" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} />
              {pw && passwordProblem(pw) && <p className="err">{passwordProblem(pw)}</p>}
            </Field>
            <button type="submit" className="btn-primary w-full" disabled={busy || code.length !== 6 || !!passwordProblem(pw)}>
              {busy ? "Please wait…" : "Set new password and sign in"}
            </button>
            <p className="meta">
              No code?{" "}
              <button type="button" className="text-accent hover:text-accent-dark" onClick={() => { setSent(false); setCode(""); }}>
                Send it again
              </button>
            </p>
          </form>
        )}
        {err && <p className="err" role="alert">{err}</p>}
      </div>
      <p className="mt-5 text-center text-sm">
        <button onClick={onBack} className="text-accent hover:text-accent-dark">← Back to sign in</button>
      </p>
    </Shell>
  );
}
