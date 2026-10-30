import jsQR from "jsqr";

export interface UpiQr {
  /** The `upi://pay?...` text the QR encodes. This, never the picture, is what we keep. */
  payload: string;
  /** Who it pays, e.g. `shop@okicici`. */
  upiId: string;
}

/** The shape of a UPI ID, `name@bank`. */
export const UPI_RE = /^[\w.-]{2,}@[A-Za-z]{2,}$/;

const MAX_SIDE = 1100;

/** Read a UPI payment QR out of a photo or screenshot. A phone shot often has a lot around the code, so try the
 *  whole picture first, then a smaller copy (which can read a code that was too fine at full size). */
export async function readUpiQr(file: File): Promise<UpiQr | null> {
  const bitmap = await createImageBitmap(file);
  try {
    for (const side of [MAX_SIDE, 600]) {
      const k = Math.min(1, side / Math.max(bitmap.width, bitmap.height));
      const w = Math.max(1, Math.round(bitmap.width * k));
      const h = Math.max(1, Math.round(bitmap.height * k));
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) return null;
      ctx.drawImage(bitmap, 0, 0, w, h);
      const hit = jsQR(ctx.getImageData(0, 0, w, h).data, w, h, { inversionAttempts: "attemptBoth" });
      const parsed = hit ? parseUpi(hit.data) : null;
      if (parsed) return parsed;
    }
    return null;
  } finally {
    bitmap.close();
  }
}

export function parseUpi(text: string): UpiQr | null {
  const t = text.trim();
  if (!t.toLowerCase().startsWith("upi://pay")) return null;
  const query = t.includes("?") ? t.slice(t.indexOf("?") + 1) : "";
  const upiId = new URLSearchParams(query).get("pa")?.trim();
  if (!upiId || t.length > 500) return null;
  return { payload: t, upiId };
}

/** The handles real UPI apps and banks issue. An unknown one is only worth a nudge: new handles appear. */
const HANDLES = new Set(
  "upi ybl ibl axl okaxis oksbi okhdfcbank okicici paytm apl airtel jio sbi hdfcbank icici axisbank kotak pnb boi bob cnrb unionbank idfcbank indus yesbank federal rbl aubank fbl postbank dbs sc ikwik freecharge slice jupiter fam pingpay waaxis wasbi wahdfcbank waicici pthdfc ptyes ptsbi ptaxis".split(
    " ",
  ),
);

export function upiHandleUnknown(upiId: string): boolean {
  const handle = upiId.split("@")[1]?.toLowerCase();
  return Boolean(handle) && !HANDLES.has(handle!);
}
