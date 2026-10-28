import { useEffect, useRef } from "react";

// Cloudflare Turnstile, the check in front of sign-up, "send code" and forgot-password. The site key is public and
// baked in at build time (VITE_TURNSTILE_SITE_KEY, same three places as the WhatsApp ids). With no key (development)
// nothing is shown and no token is sent; the server then skips the check outside production.
const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env;
const SITE_KEY = env?.VITE_TURNSTILE_SITE_KEY || "";
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
export function Turnstile({ onToken }: { onToken: (token: string | null) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const cb = useRef(onToken);
  cb.current = onToken;

  useEffect(() => {
    if (!SITE_KEY) {
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
          sitekey: SITE_KEY,
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
  }, []);

  if (!SITE_KEY) return null;
  return <div ref={box} className="min-h-[65px]" />;
}

export const CAPTCHA_ON = Boolean(SITE_KEY);
