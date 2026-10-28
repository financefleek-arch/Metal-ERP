import { useEffect, useState } from "react";
import { api } from "./api";

// The CAPTCHA's public widget key comes from the server (which gets it from Fleek's platform secrets), so it is not baked
// into the build. null = no widget (not set, or the server could not ask): the server then decides whether to let it through.
let known: { key: string | null } | null = null;

export function useCaptchaSiteKey(): { siteKey: string | null; ready: boolean } {
  const [state, setState] = useState(known);
  useEffect(() => {
    if (known) return;
    let gone = false;
    api<{ captcha_site_key: string | null }>("/auth/config", { auth: false })
      .then((r) => ({ key: r.captcha_site_key }))
      .catch(() => ({ key: null }))
      .then((v) => {
        if (v.key) known = v; // do not remember "none": it may just be a slow start
        if (!gone) setState(v);
      });
    return () => {
      gone = true;
    };
  }, []);
  return { siteKey: state?.key ?? null, ready: state !== null };
}
