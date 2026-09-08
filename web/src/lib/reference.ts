import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export interface StateOption {
  code: string;
  name: string;
}

export function useStates() {
  return useQuery({
    queryKey: ["reference", "states"],
    queryFn: () => api<StateOption[]>("/reference/states", { auth: true }),
    staleTime: Infinity,
    gcTime: Infinity,
  });
}

/** A fixed vocabulary list. `uoms` is the wide list (secondary / purchase
 *  unit); `uoms_primary` is the strict billing set — nos / kg / doz / gross —
 *  used for an item's primary unit and every invoice-line unit picker. */
export function useVocab(
  kind:
    | "uoms"
    | "uoms_primary"
    | "categories"
    | "shapes"
    | "metals"
    | "finishes",
) {
  const path = kind === "uoms_primary" ? "uoms/primary" : kind;
  return useQuery({
    queryKey: ["reference", kind],
    queryFn: () => api<string[]>(`/reference/${path}`, { auth: true }),
    staleTime: Infinity,
    gcTime: Infinity,
  });
}

// The canonical unit lists come from `units.generated.ts` (built from
// `api/app/domain/units.json`, same source the API uses). Re-exported here so
// existing importers of "../lib/reference" keep working.
export { PRIMARY_UOMS, ALL_UOMS } from "./units.generated";

// Client-side hints — the backend (app/reference.py) is the real guarantee (422).
// Keep these in lockstep with the server regexes.

export const PAN_RE = /^[A-Z]{5}[0-9]{4}[A-Z]$/;
export const GSTIN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;
export const PINCODE_RE = /^[1-9][0-9]{5}$/;
const PHONE_SHAPE_RE = /^\+?[0-9]{7,15}$/;
const LEGAL_NAME_ALLOWED_RE = /^[A-Za-z0-9 &.,\-/()'@]+$/;
const HAS_LETTER_RE = /[A-Za-z]/;
const CITY_ALLOWED_RE = /^[A-Za-z .\-']+$/;

export const MAXLEN = {
  legalName: 140,
  addressLine: 120,
  city: 60,
  phone: 20,
  email: 200,
  pan: 10,
  gstin: 15,
  pincode: 6,
} as const;

const GSTIN_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";

function gstinCheckChar(first14: string): string {
  let total = 0;
  for (let i = 0; i < first14.length; i++) {
    const v = GSTIN_ALPHABET.indexOf(first14[i]);
    const p = v * (i % 2 ? 2 : 1);
    total += Math.floor(p / 36) + (p % 36);
  }
  return GSTIN_ALPHABET[(36 - (total % 36)) % 36];
}

export function panError(v: string): string | undefined {
  if (!v) return undefined;
  return PAN_RE.test(v.toUpperCase()) ? undefined : "PAN must look like AAAAA9999A";
}

export function gstinError(v: string): string | undefined {
  if (!v) return undefined;
  const u = v.toUpperCase();
  if (!GSTIN_RE.test(u)) return "GSTIN must be 15 chars: 99AAAAA9999A9Z9";
  if (gstinCheckChar(u.slice(0, 14)) !== u[14]) return "GSTIN check digit is invalid";
  return undefined;
}

// Whitespace + phone punctuation to drop from a pasted number.
const PHONE_SEP_RE = /[\s\-().]+/g;
// NBSP + zero-width / word-joiner / BOM code points that ride along when a
// contact is copied from WhatsApp or a browser. Listed one per code point so
// no "joined character sequence" lint fires on a character class.
const PHONE_INVISIBLE = [0xa0, 0x200b, 0x200c, 0x200d, 0x2060, 0xfeff].map((c) =>
  String.fromCharCode(c),
);

function stripPhoneJunk(raw: string): string {
  let out = raw.replace(PHONE_SEP_RE, "");
  for (const ch of PHONE_INVISIBLE) out = out.split(ch).join("");
  return out;
}

/** Normalise a pasted phone to its canonical stored form, or null if it
 *  cannot be made valid. Mirrors `validate_phone` in api/app/reference.py
 *  — keep the two in lockstep.
 *
 *  - "+91XXXXXXXXXX" / "91XXXXXXXXXX" (12 digits, "91"-prefixed) -> "+91" + last 10
 *    (a WhatsApp / phonebook copy of an Indian number; "+91 9..." with 9
 *    itself a country code does not exist)
 *  - "0XXXXXXXXXX" (STD trunk-0)  -> "+91" + last 10
 *  - bare 10 digits             -> "+91XXXXXXXXXX"
 *  - other "+<cc>..." 7-15 digits -> "+<digits>" (left alone)
 */
export function normalizePhone(raw: string): string | null {
  if (!raw) return null;
  let cleaned = stripPhoneJunk(raw);
  let hasPlus = cleaned.startsWith("+");
  let body = cleaned.replace(/^\++/, "");

  if (body.length === 12 && body.startsWith("91")) {
    body = body.slice(2);
    cleaned = body;
    hasPlus = false;
  } else if (!hasPlus && body.length === 11 && body.startsWith("0")) {
    body = body.slice(1);
    cleaned = body;
  }

  if (!PHONE_SHAPE_RE.test(cleaned)) return null;
  if (body.length === 10 && !hasPlus) return `+91${body}`;
  return cleaned;
}

export function phoneError(v: string): string | undefined {
  if (!v) return undefined;
  return normalizePhone(v)
    ? undefined
    : "Enter a valid phone (10-digit mobile, or +<country code><number>)";
}

export function pincodeError(v: string): string | undefined {
  if (!v) return undefined;
  return PINCODE_RE.test(v) ? undefined : "PIN must be 6 digits and not start with 0";
}

export function emailError(v: string): string | undefined {
  if (!v) return undefined;
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v) ? undefined : "Enter a valid email address";
}

export function legalNameError(v: string): string | undefined {
  const t = v.trim().replace(/\s{2,}/g, " ");
  if (!t) return "Name is required";
  if (t.length < 2) return "Name must be at least 2 characters";
  if (t.length > MAXLEN.legalName) return `Name must be at most ${MAXLEN.legalName} characters`;
  if (!HAS_LETTER_RE.test(t)) return "Name must contain at least one letter";
  if (!LEGAL_NAME_ALLOWED_RE.test(t))
    return "Name may use letters, digits, spaces and & . , - / ( ) ' @ only";
  return undefined;
}

export function addressLineError(v: string): string | undefined {
  if (!v) return undefined;
  if (v.trim().length > MAXLEN.addressLine)
    return `Address line must be at most ${MAXLEN.addressLine} characters`;
  return undefined;
}

export function cityError(v: string): string | undefined {
  if (!v) return undefined;
  const t = v.trim();
  if (t.length > MAXLEN.city) return `City must be at most ${MAXLEN.city} characters`;
  if (!CITY_ALLOWED_RE.test(t)) return "City may use letters, spaces and . - ' only";
  return undefined;
}
