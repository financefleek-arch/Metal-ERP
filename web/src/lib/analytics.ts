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

/** Tell Google Ads a sign-up happened (a no-op until the tag is installed). */
export function reportSignup(): void {
  const g = (window as unknown as { gtag?: Gtag }).gtag;
  if (typeof g === "function") g("event", "sign_up", { method: "email_and_mobile" });
}
