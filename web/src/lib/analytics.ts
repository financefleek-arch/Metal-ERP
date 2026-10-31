// Marketing measurement for the sign-up ad campaign. The Google tag itself is added to index.html once the Ads account ids exist;
// until then these calls do nothing, so the app never depends on it.
const JUST_SIGNED_UP = "metalerp.just-signed-up";

type Gtag = (...args: unknown[]) => void;

/** Remember, for this tab only, that the person has just created an account (the welcome page reads it once). */
export function noteSignup(): void {
  try {
    sessionStorage.setItem(JUST_SIGNED_UP, "1");
  } catch {
    /* blocked storage: the welcome page is simply skipped */
  }
}

/** True once for a brand-new account, then false: a refresh or a later visit to /welcome is not a sign-up. */
export function takeSignupFlag(): boolean {
  try {
    const on = sessionStorage.getItem(JUST_SIGNED_UP) === "1";
    sessionStorage.removeItem(JUST_SIGNED_UP);
    return on;
  } catch {
    return false;
  }
}

/** A named event in Umami (the tracker is the tag in index.html). A no-op when it is blocked or not loaded. */
export function track(event: string): void {
  try {
    (window as unknown as { umami?: { track?: (name: string) => void } }).umami?.track?.(event);
  } catch {
    /* analytics never breaks the app */
  }
}

/** Tell Google Ads a sign-up happened (a no-op until the tag is installed). */
export function reportSignup(): void {
  const g = (window as unknown as { gtag?: Gtag }).gtag;
  if (typeof g === "function") g("event", "sign_up", { method: "email_and_mobile" });
}

const AD_CLICK = "metalerp.ad-click";
const AD_CLICK_KEEP_MS = 7 * 24 * 3600 * 1000;
const AD_KINDS = ["gclid", "gbraid", "wbraid"] as const;

/** Keep the Google Ads click id from the landing URL for a week, so the sign-up can carry it (a later visit without one leaves it alone). */
export function rememberAdClick(search: string = window.location.search): void {
  try {
    const q = new URLSearchParams(search);
    for (const kind of AD_KINDS) {
      const id = q.get(kind);
      if (id && /^[A-Za-z0-9_-]{1,200}$/.test(id)) {
        localStorage.setItem(AD_CLICK, JSON.stringify({ kind, id, at: Date.now() }));
        return;
      }
    }
  } catch {
    /* blocked storage: the sign-up just carries no click id */
  }
}

const FIRST_TOUCH = "metalerp.first-touch";
const UTM_KEYS = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"] as const;

export type FirstTouch = Record<string, string>;

/** First visit's campaign tags, external referrer host, landing page and device, kept a week (same 7 days and keys as Fleek's own landing page). A later visit leaves it alone. */
export function rememberFirstTouch(search: string = window.location.search): void {
  try {
    const kept = JSON.parse(localStorage.getItem(FIRST_TOUCH) ?? "null") as { at?: number } | null;
    if (kept && typeof kept.at === "number" && Date.now() - kept.at < AD_CLICK_KEEP_MS) return;
    const t: FirstTouch = {};
    const q = new URLSearchParams(search);
    for (const k of UTM_KEYS) {
      const v = q.get(k);
      if (v) t[k] = v.slice(0, 80);
    }
    try {
      const ref = document.referrer ? new URL(document.referrer).host : "";
      if (ref && ref !== window.location.host) t.referrer_host = ref;
    } catch {
      /* no usable referrer */
    }
    t.device = window.matchMedia?.("(max-width: 767px)").matches ? "mobile" : window.matchMedia?.("(max-width: 1024px)").matches ? "tablet" : "desktop";
    t.landing = (window.location.pathname + window.location.hash).slice(0, 80); // path and #anchor (an ad sitelink); never the query string
    t.first_seen_at = new Date().toISOString();
    localStorage.setItem(FIRST_TOUCH, JSON.stringify({ at: Date.now(), t }));
  } catch {
    /* blocked storage: the sign-up simply carries no first-touch facts (the server still adds browser and os) */
  }
}

/** The remembered first-touch facts in the shape the sign-up request takes, or nothing. */
export function firstTouch(): { attribution?: FirstTouch } {
  try {
    const v = JSON.parse(localStorage.getItem(FIRST_TOUCH) ?? "null") as { at?: number; t?: FirstTouch } | null;
    if (v?.t && typeof v.at === "number" && Date.now() - v.at < AD_CLICK_KEEP_MS) return { attribution: v.t };
  } catch {
    /* fall through */
  }
  return {};
}

/** The remembered click id in the shape the sign-up request takes, or nothing. */
export function adClick(): { ad_click_kind?: string; ad_click_id?: string } {
  try {
    const v = JSON.parse(localStorage.getItem(AD_CLICK) ?? "null") as { kind?: string; id?: string; at?: number } | null;
    if (v?.kind && v.id && typeof v.at === "number" && Date.now() - v.at < AD_CLICK_KEEP_MS) return { ad_click_kind: v.kind, ad_click_id: v.id };
  } catch {
    /* fall through */
  }
  return {};
}
