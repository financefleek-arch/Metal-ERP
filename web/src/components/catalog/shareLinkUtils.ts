/* Small helpers for catalog share links, kept out of the dialog file so it only exports a component. */
import type { ShareLink } from "./ShareLinkDialog";

/** How a link looks to customers and what they can order. */
export interface LinkOptions {
  columns: 1 | 2 | 3;
  show_code: boolean;
  show_pack_info: boolean;
  price_basis: "pack" | "piece";
  allow_pieces: boolean;
  piece_premium_pct: number;
}

export const DEFAULT_LINK_OPTIONS: LinkOptions = {
  columns: 2,
  show_code: true,
  show_pack_info: true,
  price_basis: "pack",
  allow_pieces: false,
  piece_premium_pct: 0,
};

export const optionsOf = (l: ShareLink): LinkOptions => ({
  columns: l.columns,
  show_code: l.show_code,
  show_pack_info: l.show_pack_info,
  price_basis: l.price_basis,
  allow_pieces: l.allow_pieces,
  piece_premium_pct: l.piece_premium_pct,
});

export const optionsBad = (o: LinkOptions) => !(o.piece_premium_pct >= 0 && o.piece_premium_pct <= 100);

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
