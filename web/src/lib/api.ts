/**
 * Thin fetch wrapper. Same-origin in production (Caddy proxies /api),
 * Vite proxies /api to :8000 in dev. The bearer token lives in
 * localStorage and is attached to every request.
 */

const TOKEN_KEY = "metalerp.token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode — session-only */
  }
}

export class ApiError extends Error {
  status: number;
  /** Raw `detail` from the response body — a string for most errors, but an
   *  object for structured 409s (e.g. the party-dedupe `{code, message,
   *  match, candidates}` payload). Callers that need the structure read this. */
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

type Options = {
  method?: string;
  body?: unknown;
  auth?: boolean;
};

/** Turn a FastAPI error body into (human message, raw detail). `detail` may be
 *  a string, a validation-error array, or a structured object (e.g. the
 *  party-dedupe 409). The message is always a string; the object is preserved
 *  on `ApiError.detail` for callers that need `code` / `candidates`. */
function parseError(data: unknown, fallback: string): { message: string; detail: unknown } {
  const d = (data as { detail?: unknown } | undefined)?.detail;
  if (typeof d === "string") return { message: d, detail: d };
  if (Array.isArray(d))
    return { message: d.map((x: { msg?: string }) => x?.msg).filter(Boolean).join("; "), detail: d };
  if (d && typeof d === "object") {
    const msg = (d as { message?: string }).message;
    return { message: typeof msg === "string" ? msg : fallback, detail: d };
  }
  return { message: fallback, detail: undefined };
}

export async function api<T>(path: string, opts: Options = {}): Promise<T> {
  const { method = "GET", body, auth = true } = opts;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth) {
    const t = getToken();
    if (t) headers["Authorization"] = `Bearer ${t}`;
  }

  const res = await fetch(`/api${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (res.status === 204) return undefined as T;

  const text = await res.text();
  const data = text ? JSON.parse(text) : undefined;

  if (!res.ok) {
    if (res.status === 401) setToken(null);
    const { message, detail } = parseError(data, res.statusText);
    throw new ApiError(res.status, message, detail);
  }
  return data as T;
}

export type Page<T> = { data: T; nextCursor: string | null };

/**
 * GET that also surfaces the `X-Next-Cursor` response header the paginated
 * list endpoints set. `nextCursor` is null on the last page (or when the
 * endpoint isn't paginating — i.e. no `limit` was passed).
 */
export async function apiPage<T>(path: string, opts: Options = {}): Promise<Page<T>> {
  const { method = "GET", body, auth = true } = opts;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth) {
    const t = getToken();
    if (t) headers["Authorization"] = `Bearer ${t}`;
  }

  const res = await fetch(`/api${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  const text = await res.text();
  const data = text ? JSON.parse(text) : undefined;
  if (!res.ok) {
    if (res.status === 401) setToken(null);
    const { message, detail } = parseError(data, res.statusText);
    throw new ApiError(res.status, message, detail);
  }
  return { data: data as T, nextCursor: res.headers.get("X-Next-Cursor") };
}

/** multipart/form-data POST (file upload). Same auth + error handling as `api`. */
export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const headers: Record<string, string> = {};
  const t = getToken();
  if (t) headers["Authorization"] = `Bearer ${t}`;

  const res = await fetch(`/api${path}`, { method: "POST", headers, body: form });
  if (res.status === 204) return undefined as T;

  const text = await res.text();
  const data = text ? JSON.parse(text) : undefined;
  if (!res.ok) {
    if (res.status === 401) setToken(null);
    const { message, detail } = parseError(data, res.statusText);
    throw new ApiError(res.status, message, detail);
  }
  return data as T;
}
