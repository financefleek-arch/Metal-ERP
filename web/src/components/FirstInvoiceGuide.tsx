import { useAuth } from "../lib/auth";
import { useSetup } from "../lib/setup";

/** Three plain lines above a firm's very first invoice. A new firm has no customers or items on file, and the pickers
 *  already add one on the spot, but nothing says so. Gone once the first invoice exists. */
export function FirstInvoiceGuide() {
  const { me } = useAuth();
  const { data } = useSetup(me?.role === "owner");
  const first = data?.steps.find((s) => s.key === "invoice");
  if (!first || first.done) return null;
  return (
    <div className="rounded-xl border border-[#cfe0e8] bg-accent-soft px-4 py-3 text-sm">
      <div className="font-semibold">Your first invoice</div>
      <ol className="mt-1 list-decimal space-y-0.5 pl-5">
        <li>Type your customer&apos;s name. If they are not on the list, tap <b>add as new</b>.</li>
        <li>Type the item, then its weight. The rate is remembered next time.</li>
        <li>Save, then send it to your customer on WhatsApp.</li>
      </ol>
    </div>
  );
}
