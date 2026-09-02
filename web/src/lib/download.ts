/** Authenticated file download helpers. */

import { getToken } from "./api";

/** Pull `filename` out of a Content-Disposition header, if present. */
function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null;
  // RFC 5987 form first: filename*=UTF-8''<pct-encoded>
  const star = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(header);
  if (star) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^"|"$/g, ""));
    } catch {
      /* fall through */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1].trim() : null;
}

/**
 * Fetch `/api<path>` with the bearer token and save the body as a file,
 * honouring the server's Content-Disposition filename (falls back to
 * `fallbackName`). Used for streamed endpoints that need an auth header,
 * where a plain `<a href>` can't carry one.
 */
export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  const t = getToken();
  const res = await fetch(`/api${path}`, {
    headers: t ? { Authorization: `Bearer ${t}` } : {},
  });
  if (!res.ok) throw new Error(`download failed: ${res.status}`);
  const blob = await res.blob();
  const name =
    filenameFromDisposition(res.headers.get("Content-Disposition")) ?? fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Give the download a beat to start before revoking.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
