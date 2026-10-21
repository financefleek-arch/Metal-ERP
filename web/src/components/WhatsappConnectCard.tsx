import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import { almostReady, type TemplateCounts } from "../lib/whatsappSetup";
import { WhatsappSetupChecklist } from "./WhatsappSetupChecklist";

/**
 * The owner's WhatsApp page, ported from Fleek's advisor page (fleek-frontend WhatsAppIntegration.vue) so a
 * shop connects exactly the way an advisor does: Meta's Embedded Signup popup, a PIN for a new number or none
 * for a number that stays on the WhatsApp Business app, retry registration, disconnect. Every call goes to
 * /api/whatsapp-integrations, which hands it to Fleek; the answers shown here are Fleek's. Left out because they
 * belong to Fleek's CRM: the business-profile card, "what happens next", the shared-with-colleague number.
 * See docs/PARITY-whatsapp-connect.md.
 */

interface Status {
  connected: boolean;
  needs_reconnect: boolean;
  needs_registration: boolean;
  display_phone_number: string | null;
  business_name: string | null;
  connection_mode?: "cloud" | "coexistence" | null;
  /** how many of the shop's messages WhatsApp has approved (Fleek's answer) */
  templates?: TemplateCounts | null;
}

interface Reply {
  status?: string;
  error?: string;
  message?: string;
}

interface FbLoginResponse {
  authResponse?: { code?: string };
}
interface FbSdk {
  init: (o: { appId: string; cookie: boolean; xfbml: boolean; version: string }) => void;
  login: (cb: (r: FbLoginResponse) => void, opts: Record<string, unknown>) => void;
}
type FbWindow = Window & { FB?: FbSdk; fbAsyncInit?: () => void };

// Public ids, baked in at build time exactly as Fleek's page does (infra/metalerp/web.Dockerfile).
const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env;
const FB_APP_ID = env?.VITE_WHATSAPP_APP_ID || "";
const WA_CONFIG_ID = env?.VITE_WHATSAPP_CONFIG_ID || "";

// Meta ends the popup with one of several completion messages; any of them carries the new account's ids.
// ERROR / CANCEL are not completions.
const FINISH_EVENTS = [
  "FINISH",
  "FINISH_ONLY_WABA",
  "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING",
  "FINISH_OBO_MIGRATION",
  "FINISH_GRANT_ONLY_API_ACCESS",
];

interface SignupPayload {
  event?: string;
  waba_id?: string;
  phone_number_id?: string;
  business_name?: string;
}

// Meta's SDK is loaded on demand: this is the only page that needs it.
function loadFacebookSdk(): Promise<void> {
  return new Promise((resolve, reject) => {
    const w = window as FbWindow;
    if (w.FB) {
      resolve();
      return;
    }
    const timeout = setTimeout(() => reject(new Error("Facebook SDK load timed out")), 10000);
    w.fbAsyncInit = () => {
      clearTimeout(timeout);
      w.FB?.init({ appId: FB_APP_ID, cookie: true, xfbml: false, version: "v25.0" });
      resolve();
    };
    const js = document.createElement("script");
    js.src = "https://connect.facebook.net/en_US/sdk.js";
    js.async = true;
    js.defer = true;
    js.onerror = () => {
      clearTimeout(timeout);
      reject(new Error("Facebook SDK failed to load"));
    };
    document.body.appendChild(js);
  });
}

async function call(path: string, method: "GET" | "POST", body?: unknown): Promise<Reply> {
  try {
    return await api<Reply>(`/whatsapp-integrations${path}`, { method, body });
  } catch (e) {
    // Fleek's refusals come back as { error, message }; ApiError carries the message.
    return { error: e instanceof ApiError ? e.message : "Could not reach the server." };
  }
}

type Dialog = null | "choice" | "explainer" | "pin";

export function WhatsappConnectCard() {
  const [st, setSt] = useState<Status | null>(null);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [pin, setPin] = useState("");
  const [retryPin, setRetryPin] = useState("");
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const signup = useRef<SignupPayload>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSt(await api<Status>("/whatsapp-integrations"));
      setUnavailable(null);
    } catch (e) {
      setSt(null);
      setUnavailable(e instanceof ApiError ? e.message : "WhatsApp is not available right now.");
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
    const onMessage = (event: MessageEvent) => {
      // Meta's docs check endsWith('facebook.com'): the real FINISH message can arrive from a subdomain
      // (business.facebook.com), so strict equality would silently drop it.
      if (!event.origin.endsWith("facebook.com")) return;
      try {
        const data = JSON.parse(event.data as string);
        if (data.type === "WA_EMBEDDED_SIGNUP" && FINISH_EVENTS.includes(data.event)) {
          signup.current = {
            event: data.event,
            waba_id: data.data?.waba_id,
            phone_number_id: data.data?.phone_number_id,
            business_name: data.data?.business_name,
          };
        }
      } catch {
        /* not a Meta embedded-signup message */
      }
    };
    window.addEventListener("message", onMessage);
    // Pre-loaded so FB.login() runs synchronously inside the click: a popup opened after an await is blocked.
    if (FB_APP_ID && WA_CONFIG_ID) loadFacebookSdk().catch(() => undefined);
    return () => window.removeEventListener("message", onMessage);
  }, [load]);

  const startConnect = (viaBusinessApp: boolean) => {
    const w = window as FbWindow;
    if (!FB_APP_ID || !WA_CONFIG_ID) {
      setNote({ ok: false, text: "WhatsApp connect is not configured yet." });
      return;
    }
    if (!w.FB) {
      setNote({
        ok: false,
        text: "Facebook SDK is still loading. Try again in a moment, or check for an ad-blocker blocking connect.facebook.net.",
      });
      return;
    }
    if (!viaBusinessApp && !/^\d{6}$/.test(pin)) {
      setNote({ ok: false, text: "Enter a 6-digit PIN." });
      return;
    }
    const chosenPin = pin;
    setDialog(null);
    setConnecting(true);
    setNote(null);
    signup.current = {};
    // FB.login() requires a plain (non-async) callback; the async work runs in an inner function.
    w.FB.login(
      (response) => {
        setConnecting(false);
        const code = response?.authResponse?.code;
        const s = signup.current;
        if (!code || !s.waba_id || !s.phone_number_id) {
          setNote({ ok: false, text: "WhatsApp connection was not completed." });
          return;
        }
        void (async () => {
          const res = await call("/connect", "POST", {
            code,
            waba_id: s.waba_id,
            phone_number_id: s.phone_number_id,
            business_name: s.business_name,
            ...(viaBusinessApp ? { onboarding: "business_app" } : { pin: chosenPin }),
            finish_event: s.event,
          });
          if (res.status === "success") setNote({ ok: true, text: "WhatsApp connected." });
          else if (res.status === "connected_needs_registration")
            setNote({
              ok: false,
              text:
                res.message ||
                "Connected, but WhatsApp has not registered the number yet. You can retry here.",
            });
          else setNote({ ok: false, text: res.message || res.error || "Could not complete WhatsApp connection." });
          setPin("");
          await load();
        })();
      },
      {
        config_id: WA_CONFIG_ID,
        response_type: "code",
        override_default_response_type: true,
        // version v4 matches Meta's own hosted-signup link; featureType is Meta's coexistence flow.
        extras: {
          setup: {},
          featureType: viaBusinessApp ? "whatsapp_business_app_onboarding" : "",
          sessionInfoVersion: "3",
          version: "v4",
        },
      },
    );
  };

  const retryRegistration = async () => {
    if (retryPin && !/^\d{6}$/.test(retryPin)) {
      setNote({ ok: false, text: "PIN must be 6 digits." });
      return;
    }
    setRetrying(true);
    const res = await call("/retry-registration", "POST", retryPin ? { pin: retryPin } : {});
    setRetrying(false);
    if (res.status === "success") {
      setNote({ ok: true, text: "WhatsApp registration completed." });
      setRetryPin("");
      await load();
    } else {
      setNote({
        ok: false,
        text: res.message || res.error || "Registration is still not completing. Ask Fleek and we will look into it.",
      });
    }
  };

  const disconnect = async () => {
    if (!window.confirm("Disconnect WhatsApp?\n\nMessages to customers over WhatsApp will stop until this is reconnected.")) return;
    await call("/disconnect", "POST", {});
    setNote({ ok: true, text: "WhatsApp disconnected." });
    await load();
  };

  if (loading && !st && !unavailable) return null;

  if (unavailable) {
    return (
      <div className="card mb-5 p-4 sm:p-5">
        <div className="flex items-baseline justify-between gap-3">
          <h3 className="text-sm font-semibold">WhatsApp Business</h3>
          <span className="rounded-full bg-warn/15 px-2 py-0.5 text-[11px] font-semibold text-warn">Not available yet</span>
        </div>
        <p className="mt-2 max-w-prose text-xs text-muted">{unavailable}</p>
      </div>
    );
  }
  if (!st) return null;

  const connected = st.connected && !st.needs_reconnect;
  const canConnect = !st.connected || st.needs_reconnect;

  return (
    <div className="card mb-5 p-4 sm:p-5">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold">WhatsApp Business</h3>
        <span
          className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
            st.needs_reconnect || st.needs_registration || (connected && almostReady(st.templates))
              ? "bg-warn/15 text-warn"
              : connected
                ? "bg-ok/15 text-ok"
                : "bg-line text-muted"
          }`}
        >
          {st.needs_reconnect
            ? "Needs reconnect"
            : st.needs_registration
              ? "Registration pending"
              : connected
                ? almostReady(st.templates)
                  ? "Almost ready"
                  : "Connected"
                : "Not connected"}
        </span>
      </div>
      <p className="mt-2 max-w-prose text-xs text-muted">
        Connect your own WhatsApp Business number so customer messages send from your number, not a shared one.
      </p>

      {canConnect && !loading && (
        <div className="mt-3 max-w-prose rounded-md border border-line bg-ground p-3 text-xs text-muted">
          <p className="font-semibold text-ink">Before you press Connect</p>
          <ul className="mt-1.5 list-disc space-y-1 pl-4">
            <li>
              <strong>The number.</strong> Use a number that is not currently active on the normal WhatsApp or WhatsApp
              Business app (or be ready to move it), and keep the phone nearby: WhatsApp sends a verification code by SMS
              or call.
            </li>
            <li>
              <strong>Your Facebook login.</strong> Sign in with the Facebook account that is an admin of your business. If
              you do not have a Meta business yet, the pop-up will help you create one.
            </li>
            <li>
              <strong>Your business name.</strong> WhatsApp reviews the name customers will see; approval can take from
              minutes to a day or two.
            </li>
            <li>
              <strong>Payment.</strong> WhatsApp charges for messages directly to you. Add a payment method to your
              WhatsApp account when the pop-up asks.
            </li>
            <li>
              <strong>Your daily limit.</strong> A new business can start conversations with up to 250 people a day. Once
              WhatsApp verifies your business, this rises (to 1,000, then 2,000 and more). Verification is done inside
              Meta Business Settings.
            </li>
            <li>
              <strong>The 6-digit PIN.</strong> You choose it in the next step. It protects the number. Write it down: we
              ask for it only if registration needs a retry.
            </li>
          </ul>
          <p className="mt-2">
            Takes about 15 to 30 minutes. If anything looks different from this list, ask Fleek before you continue.
          </p>
        </div>
      )}

      {connected && (
        <div className="mt-3 text-sm">
          <div className="font-semibold">{st.display_phone_number || "Your WhatsApp number"}</div>
          {st.business_name && <div className="text-xs text-muted">{st.business_name}</div>}
          {st.connection_mode === "coexistence" && (
            <div className="text-xs text-ok">Also works on your phone's WhatsApp Business app</div>
          )}
          <WhatsappSetupChecklist registered={!st.needs_registration} templates={st.templates} />
        </div>
      )}

      {st.needs_registration && (
        <div className="mt-3 max-w-prose rounded-md border border-warn/30 bg-warn/5 p-3 text-xs">
          <p>
            Meta connected but couldn't finish registering your number. Messages won't send until this completes. This
            usually resolves on retry; if Meta rejects the PIN on file, enter the correct one below before retrying.
            Still stuck? Ask Fleek.
          </p>
          <input
            className="field mt-2 max-w-[11rem]"
            inputMode="numeric"
            maxLength={6}
            placeholder="6-digit PIN (optional)"
            value={retryPin}
            onChange={(e) => setRetryPin(e.target.value.replace(/\D/g, ""))}
          />
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3">
        {canConnect && (
          <button className="btn-primary" disabled={connecting} onClick={() => setDialog("choice")}>
            {connecting ? "Connecting…" : "Connect"}
          </button>
        )}
        {!canConnect && st.needs_registration && (
          <button className="btn-primary" disabled={retrying} onClick={() => void retryRegistration()}>
            {retrying ? "Retrying…" : "Retry registration"}
          </button>
        )}
        {!canConnect && !st.needs_registration && (
          <button className="btn-ghost" onClick={() => void disconnect()}>
            Disconnect
          </button>
        )}
        {note && <span className={note.ok ? "text-xs text-ok" : "err mt-0"}>{note.text}</span>}
      </div>

      {dialog && (
        <div
          className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/40 p-4"
          onClick={() => setDialog(null)}
        >
          <div
            role="dialog"
            aria-modal="true"
            className="card w-full max-w-lg bg-card p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            {dialog === "choice" && (
              <>
                <h4 className="text-sm font-semibold">Which number are you connecting?</h4>
                <p className="mt-1 text-xs text-muted">Pick the one that matches how you use WhatsApp today.</p>
                <button
                  type="button"
                  className="card mt-3 block w-full p-3 text-left hover:bg-ground"
                  onClick={() => setDialog("explainer")}
                >
                  <span className="text-[11px] font-semibold text-ok">Keeps your chats</span>
                  <strong className="block text-sm">The number I already use on the WhatsApp Business app</strong>
                  <span className="block text-xs text-muted">
                    Keep chatting on your phone as you do now. Messages from Metal ERP go out from the same number.
                  </span>
                </button>
                <button
                  type="button"
                  className="card mt-2 block w-full p-3 text-left hover:bg-ground"
                  onClick={() => {
                    setPin("");
                    setDialog("pin");
                  }}
                >
                  <strong className="block text-sm">A new number, just for Metal ERP</strong>
                  <span className="block text-xs text-muted">
                    The number is used only through Metal ERP. It can't be on WhatsApp or the Business app at the same
                    time, and you'll set a 6-digit PIN.
                  </span>
                </button>
                <p className="mt-3 text-xs text-warn">
                  A number on the regular WhatsApp app (not WhatsApp Business) can't be connected as it is. Switch that
                  number to the WhatsApp Business app first.
                </p>
                <details className="mt-2 text-xs text-muted">
                  <summary className="cursor-pointer">What changes if I keep using the Business app?</summary>
                  <ul className="mt-1 list-disc space-y-1 pl-4">
                    <li>Open the WhatsApp Business app on your phone at least every two weeks, or the connection pauses.</li>
                    <li>
                      Group chats and broadcast lists don't come across, and disappearing or view-once messages aren't
                      supported.
                    </li>
                    <li>
                      You'll be asked whether to share your past chats (the last six months of one-to-one chats).
                    </li>
                    <li>Needs the latest WhatsApp Business app.</li>
                  </ul>
                </details>
                <div className="mt-4 flex justify-end">
                  <button className="btn-ghost" onClick={() => setDialog(null)}>
                    Cancel
                  </button>
                </div>
              </>
            )}
            {dialog === "explainer" && (
              <>
                <h4 className="text-sm font-semibold">Connect with WhatsApp</h4>
                <p className="mt-1 text-xs text-muted">A WhatsApp window opens. You'll:</p>
                <ol className="mt-2 list-decimal space-y-1 pl-5 text-xs">
                  <li>Enter your number and copy the code WhatsApp shows.</li>
                  <li>
                    Open your WhatsApp Business app and tap <strong>Connect</strong> when WhatsApp asks.
                  </li>
                  <li>Choose whether to share your past chats, then finish.</li>
                </ol>
                <p className="mt-2 text-xs text-muted">
                  Keep your phone nearby and the WhatsApp Business app open until it finishes.
                </p>
                <div className="mt-4 flex justify-end gap-2">
                  <button className="btn-ghost" onClick={() => setDialog(null)}>
                    Cancel
                  </button>
                  <button className="btn-primary" onClick={() => startConnect(true)}>
                    Open WhatsApp
                  </button>
                </div>
              </>
            )}
            {dialog === "pin" && (
              <>
                <h4 className="text-sm font-semibold">Set a WhatsApp PIN</h4>
                <p className="mt-1 text-xs text-muted">
                  Choose a 6-digit two-step-verification PIN for this number. Meta requires it to register the number
                  for messaging, and you'll need to remember it if registration ever needs a retry.
                </p>
                <input
                  className="field mt-3 max-w-[11rem]"
                  inputMode="numeric"
                  maxLength={6}
                  placeholder="6-digit PIN"
                  autoFocus
                  value={pin}
                  onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))}
                />
                <div className="mt-4 flex justify-end gap-2">
                  <button className="btn-ghost" onClick={() => setDialog(null)}>
                    Cancel
                  </button>
                  <button className="btn-primary" disabled={!/^\d{6}$/.test(pin)} onClick={() => startConnect(false)}>
                    Continue
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
