/* Small helpers for catalog share links, kept out of the dialog file so it only exports a component. */
import type { ShareLink } from "./ShareLinkDialog";

export const linkUrl = (l: Pick<ShareLink, "path">) => `${window.location.origin}${l.path}`;

/** A WhatsApp click-to-chat link carrying the catalog URL (opens the shop's own WhatsApp). */
export const whatsappShare = (l: ShareLink, firm?: string) =>
  `https://wa.me/?text=${encodeURIComponent(
    `${firm ? `${firm}: ` : ""}${l.title}\n${linkUrl(l)}`,
  )}`;

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
