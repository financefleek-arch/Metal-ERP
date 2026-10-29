const SEEN_KEY = "metalerp.landing-seen";

/** Where a visitor should start: the landing page for a first visit in a browser, sign-in for the installed app and for
 *  anyone who has been here before. Wrapped in try/catch: storage can be blocked, and then the landing simply shows. */
export function shouldShowLanding(): boolean {
  try {
    const q = new URLSearchParams(window.location.search);
    const installed =
      window.matchMedia?.("(display-mode: standalone)").matches ||
      document.referrer.startsWith("android-app://") ||
      q.get("source") === "pwa";
    if (installed || q.get("signin") === "1") return false;
    // An ad click or a link to a section (#invoices, #catalog) is a request to see the page, even on a repeat visit.
    if (["gclid", "gbraid", "wbraid"].some((k) => q.has(k)) || ["#invoices", "#catalog"].includes(window.location.hash)) return true;
    return localStorage.getItem(SEEN_KEY) !== "1";
  } catch {
    return true;
  }
}

export function markLandingSeen(): void {
  try {
    localStorage.setItem(SEEN_KEY, "1");
  } catch {
    /* private mode: the landing shows again next visit, nothing breaks */
  }
}
