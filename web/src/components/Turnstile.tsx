import { useEffect, useRef } from "react";

// Cloudflare Turnstile, the check in front of sign-up, "send code" and forgot-password. The public site key comes from the
// server (lib/captcha.ts), which gets it from Fleek's platform secrets. With no key nothing is shown and no token is
// sent; the server then skips the check outside production and refuses in production.
const SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

interface TurnstileApi {
  render: (el: HTMLElement, o: { sitekey: string; callback: (t: string) => void; "expired-callback": () => void; "error-callback": () => void }) => string;
  remove: (id: string) => void;
}
type TsWindow = Window & { turnstile?: TurnstileApi };

function loadScript(): Promise<void> {
  return new Promise((resolve, reject) => {
    if ((window as TsWindow).turnstile) return resolve();
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${SCRIPT}"]`);
    const s = existing ?? Object.assign(document.createElement("script"), { src: SCRIPT, async: true });
    s.addEventListener("load", () => resolve());
    s.addEventListener("error", () => reject(new Error("Could not load the check")));
    if (!existing) document.head.appendChild(s);
  });
}

/** Calls `onToken` with a fresh token when the person passes, and with null when it expires or fails. Remount it
 *  (change `key`) after a submit: a token works once. */
export function Turnstile({ siteKey, onToken }: { siteKey: string | null; onToken: (token: string | null) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const cb = useRef(onToken);
  cb.current = onToken;

  useEffect(() => {
    if (!siteKey) {
      cb.current(null);
      return;
    }
    let id: string | undefined;
    let gone = false;
    loadScript()
      .then(() => {
        const api = (window as TsWindow).turnstile;
        if (gone || !api || !box.current) return;
        id = api.render(box.current, {
          sitekey: siteKey,
          callback: (t) => cb.current(t),
          "expired-callback": () => cb.current(null),
          "error-callback": () => cb.current(null),
        });
      })
      .catch(() => cb.current(null));
    return () => {
      gone = true;
      if (id) (window as TsWindow).turnstile?.remove(id);
    };
  }, [siteKey]);

  if (!siteKey) return null;
  return <div ref={box} className="min-h-[65px]" />;
}

