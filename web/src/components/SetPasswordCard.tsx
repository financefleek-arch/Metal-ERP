import { useState } from "react";
import type { FormEvent } from "react";
import { ApiError, api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { PASSWORD_HINT, passwordProblem } from "../lib/password";

/** A short sign-up has no password yet. Shown until the owner chooses one (they can always reset by WhatsApp code meanwhile).
 *  The email is optional contact detail: it is never verified and never needed to sign in. */
export function SetPasswordCard() {
  const { me, acceptToken } = useAuth();
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState("");
  const [email, setEmail] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!me || me.password_set !== false) return null;

  const problem = pw ? passwordProblem(pw) : null;
  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const r = await api<{ access_token: string }>("/auth/set-password", {
        method: "POST",
        body: { new_password: pw, email: email.trim() || null },
      });
      await acceptToken(r.access_token); // the old token ended; me reloads with password_set true and this card goes away
    } catch (x) {
      setErr(x instanceof ApiError ? x.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-[#ecdca7] bg-brass-soft px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="font-semibold">Choose a password</div>
          <div className="meta">So you can sign in next time with your mobile number and password.</div>
        </div>
        {!open && (
          <button className="btn-brass !h-9 !px-3 md:!h-8" onClick={() => setOpen(true)}>
            Choose a password
          </button>
        )}
      </div>
      {open && (
        <form onSubmit={submit} className="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="sp-pw">Password</label>
            <input id="sp-pw" className="field" type="password" autoComplete="new-password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)} />
            <p className="meta mt-1">{PASSWORD_HINT}</p>
            {problem && <p className="err">{problem}</p>}
          </div>
          <div>
            <label className="label" htmlFor="sp-email">Email (optional)</label>
            <input id="sp-email" className="field" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            <p className="meta mt-1">For receipts and news. Never needed to sign in.</p>
          </div>
          {err && <p className="err sm:col-span-2" role="alert">{err}</p>}
          <div className="sm:col-span-2">
            <button type="submit" className="btn-brass" disabled={busy || !pw || !!problem}>
              {busy ? "Saving…" : "Save password"}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
