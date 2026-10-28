import { useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { ApiError, api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { PASSWORD_HINT, passwordProblem } from "../lib/password";
import { CodeField, ResendLink } from "../components/CodeField";
import { Turnstile } from "../components/Turnstile";
import { useCaptchaSiteKey } from "../lib/captcha";

// The policy of record (Section 12 covers Metal ERP); see pages/public/LegalPages.tsx.
const PRIVACY_POLICY = "https://fleekfintech.com/privacy-policy/";
const TERMS = "https://fleekfintech.com/terms/";

type Mode = "login" | "signup" | "verify" | "forgot";

interface Started {
  request_id: string;
  mobile_masked: string;
  email_masked: string;
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

export function AuthPage() {
  const [mode, setMode] = useState<Mode>("login");
  const [started, setStarted] = useState<Started | null>(null);
  const go = (m: Mode) => setMode(m);

  if (mode === "signup")
    return <SignupForm onStarted={(s) => { setStarted(s); go("verify"); }} onBack={() => go("login")} />;
  if (mode === "verify" && started)
    return <VerifyForm started={started} onBack={() => go("signup")} />;
  if (mode === "forgot") return <ForgotForm onBack={() => go("login")} />;
  return <LoginForm onSignup={() => go("signup")} onForgot={() => go("forgot")} />;
}

// ------------------------------------------------------------------------------------------ sign in

function LoginForm({ onSignup, onForgot }: { onSignup: () => void; onForgot: () => void }) {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await login(email.trim(), password);
    } catch (x) {
      setErr(message(x));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="Metal ERP" sub="Sign in to your firm">
      <form onSubmit={submit} className="card space-y-4 p-6">
        <Field label="Email">
          <input className="field" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </Field>
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
        <button type="submit" className="btn-primary w-full" disabled={busy}>
          {busy ? "Please wait…" : "Sign in"}
        </button>
      </form>
      <p className="mt-5 text-center text-sm text-muted">
        New to Metal ERP?{" "}
        <button onClick={onSignup} className="text-accent hover:text-accent-dark">
          Create a free account
        </button>
      </p>
    </Shell>
  );
}

// ------------------------------------------------------------------------------------------ sign up

function SignupForm({ onStarted, onBack }: { onStarted: (s: Started) => void; onBack: () => void }) {
  const [f, setF] = useState({ firm_name: "", owner_name: "", mobile: "", email: "", password: "" });
  const [terms, setTerms] = useState(false);
  const { siteKey, ready: captchaReady } = useCaptchaSiteKey();
  const [token, setToken] = useState<string | null>(null);
  const [tsKey, setTsKey] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });

  const pwProblem = f.password ? passwordProblem(f.password) : null;
  const ready =
    f.firm_name.trim().length >= 2 && f.owner_name.trim().length >= 2 && f.mobile.replace(/\D/g, "").length >= 10 &&
    f.email.includes("@") && !passwordProblem(f.password) && terms && captchaReady && (!siteKey || !!token);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const r = await api<Started>("/auth/signup/start", {
        method: "POST",
        auth: false,
        body: { ...f, firm_name: f.firm_name.trim(), owner_name: f.owner_name.trim(), email: f.email.trim(), accept_terms: terms, captcha_token: token },
      });
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
    <Shell title="Create your free account" sub="Invoices, customers, items and payments. Free, no card." step={1}>
      <form onSubmit={submit} className="card space-y-4 p-6">
        <Field label="Firm name">
          <input className="field" autoFocus value={f.firm_name} onChange={set("firm_name")} maxLength={200} required />
        </Field>
        <Field label="Your name">
          <input className="field" autoComplete="name" value={f.owner_name} onChange={set("owner_name")} maxLength={100} required />
        </Field>
        <Field label="Mobile (WhatsApp)" hint="We send a code here on WhatsApp.">
          <div className="flex gap-2">
            <span className="field flex w-14 flex-none items-center justify-center text-muted">+91</span>
            <input className="field" type="tel" inputMode="numeric" autoComplete="tel-national" value={f.mobile} onChange={set("mobile")} placeholder="98765 43210" required />
          </div>
        </Field>
        <Field label="Email" hint="We send a second code here.">
          <input className="field" type="email" autoComplete="email" value={f.email} onChange={set("email")} required />
        </Field>
        <Field label="Password" hint={PASSWORD_HINT}>
          <input className="field" type="password" autoComplete="new-password" value={f.password} onChange={set("password")} required />
          {pwProblem && <p className="err">{pwProblem}</p>}
        </Field>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1 h-4 w-4" checked={terms} onChange={(e) => setTerms(e.target.checked)} />
          <span>
            I agree to the{" "}
            <a className="text-accent underline" href={TERMS} target="_blank" rel="noreferrer">Terms</a> and have read the{" "}
            <a className="text-accent underline" href={PRIVACY_POLICY} target="_blank" rel="noreferrer">Privacy Policy</a>
          </span>
        </label>
        <Turnstile key={tsKey} siteKey={siteKey} onToken={setToken} />
        {err && <p className="err" role="alert">{err}</p>}
        <button type="submit" className="btn-primary w-full" disabled={busy || !ready}>
          {busy ? "Sending codes…" : "Send codes"}
        </button>
      </form>
      <p className="mt-5 text-center text-sm text-muted">
        Already have an account?{" "}
        <button onClick={onBack} className="text-accent hover:text-accent-dark">Sign in</button>
      </p>
    </Shell>
  );
}

// ---------------------------------------------------------------------------------------- the 2 codes

function VerifyForm({ started, onBack }: { started: Started; onBack: () => void }) {
  const { acceptToken } = useAuth();
  const [wa, setWa] = useState("");
  const [em, setEm] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const resend = (channel: "whatsapp" | "email") => async () => {
    setErr(null);
    try {
      await api<void>("/auth/signup/resend", { method: "POST", auth: false, body: { request_id: started.request_id, channel } });
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
        body: { request_id: started.request_id, whatsapp_code: wa, email_code: em },
      });
      await acceptToken(r.access_token);
    } catch (x) {
      setErr(message(x));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="Confirm it is you" sub="Your account is created only after both codes are right." step={2}>
      <form onSubmit={submit} className="card space-y-5 p-6">
        <div className="space-y-1">
          <CodeField label="WhatsApp code" hint={`sent to ${started.mobile_masked}`} value={wa} onChange={setWa} autoFocus />
          <ResendLink onResend={resend("whatsapp")} />
        </div>
        <div className="space-y-1">
          <CodeField label="Email code" hint={`sent to ${started.email_masked}`} value={em} onChange={setEm} />
          <ResendLink onResend={resend("email")} />
        </div>
        {err && <p className="err" role="alert">{err}</p>}
        <button type="submit" className="btn-primary w-full" disabled={busy || wa.length !== 6 || em.length !== 6}>
          {busy ? "Creating your account…" : "Create my account"}
        </button>
      </form>
      <p className="mt-5 text-center text-sm">
        <button onClick={onBack} className="text-accent hover:text-accent-dark">← Change my details</button>
      </p>
    </Shell>
  );
}

// ---------------------------------------------------------------------------------- forgot password

function ForgotForm({ onBack }: { onBack: () => void }) {
  const { acceptToken } = useAuth();
  const { siteKey, ready: captchaReady } = useCaptchaSiteKey();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [code, setCode] = useState("");
  const [pw, setPw] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const [tsKey, setTsKey] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function send(channel: "email" | "whatsapp") {
    setErr(null);
    setBusy(true);
    try {
      await api<void>("/auth/password/forgot", { method: "POST", auth: false, body: { email: email.trim(), channel, captcha_token: token } });
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
        body: { email: email.trim(), code, new_password: pw },
      });
      await acceptToken(r.access_token);
    } catch (x) {
      setErr(message(x));
    } finally {
      setBusy(false);
    }
  }

  const needToken = !captchaReady || (!!siteKey && !token);
  return (
    <Shell title="Reset your password" sub="We send a code to the email on your account.">
      <div className="card space-y-4 p-6">
        <Field label="Email">
          <input className="field" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} disabled={sent} />
        </Field>
        {!sent && (
          <>
            <Turnstile key={tsKey} siteKey={siteKey} onToken={setToken} />
            <button className="btn-primary w-full" disabled={busy || !email.includes("@") || needToken} onClick={() => void send("email")}>
              {busy ? "Sending…" : "Send code"}
            </button>
            <p className="meta">
              Have a verified mobile?{" "}
              <button className="text-accent hover:text-accent-dark" disabled={busy || !email.includes("@") || needToken} onClick={() => void send("whatsapp")}>
                Send it on WhatsApp instead
              </button>
            </p>
          </>
        )}
        {sent && (
          <form onSubmit={reset} className="space-y-4">
            <p className="meta">If that email has an account, a code is on its way.</p>
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
