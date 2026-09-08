# EXECUTION PLAN — Phone input polish + "save number to party"

Status: **BUILT 2026-09-08, UNCOMMITTED, not deployed.** Slice of the S1 /
F2‑finish work in `MASTER-tally-complement-saas.md`. No migration.
Backend suite green (385 pass / 1 skip, +9 tests); web `tsc` / `eslint` /
`vite build` clean. NOT visually reviewed, NOT run e2e against real Meta.

Scope decided with the user:

- **In:** paste‑friendly phone normalisation (spaces, `+91`, WhatsApp‑copied
  contacts), one shared normalise helper on web + server, and a prompt to save
  a typed "Other number" back onto the party from the WhatsApp send dialog.
- **Out:** `whatsapp_optin` / consent UI — **explicitly dropped.** DPDP‑style
  consent is not a concern for this customer base. Sends to a party will no
  longer gate on `whatsapp_optin` (see §4).

---

## 1. The problem

1. **Typing numbers is fiddly.** Operators copy a contact from WhatsApp — which
   pastes as `+91 98765 43210`, `+919876543210`, `919876543210`, or with a
   name prefix / trailing invisible chars. The current field just takes the raw
   string; the send dialog's client check is a loose `digits.length >= 10`
   ([InvoiceEditorPage.tsx:1125](../web/src/pages/invoices/InvoiceEditorPage.tsx#L1125)),
   so `919876543210` (12 digits) passes the client, then the server
   `validate_phone` keeps it as `+919876543210` — which is *correct* E.164, so
   that one actually works. But `+91 98765 43210` with a stray non‑breaking
   space, or `Ramesh +91 98765 43210`, slips through differently on client vs
   server and can 502 mid‑send‑loop.
2. **A typed "Other number" is thrown away.** The send dialog's "Other number"
   box ([lines 1188‑1202](../web/src/pages/invoices/InvoiceEditorPage.tsx#L1188-L1202))
   sends and forgets. Same customer next invoice → retype. There is **no UI
   anywhere** that writes `party.phone` from an invoice context, and no
   "save this number?" affordance.

## 2. The quick win — one normalisation rule, shared

A single function, mirrored on web (TS) and server (Py), applied everywhere a
phone is accepted (party forms + WhatsApp dialog):

```
normalizePhone(raw):
  1. trim; strip everything except digits and a leading '+'
     - also strip U+00A0 / U+200B / other whitespace (regex \s covers  ;
       add ​-‍﻿ explicitly)
  2. if starts with '+':
       digits = rest
       if digits starts '91' and len(digits) == 12:  ->  last 10   (India)
       else                                            ->  keep as '+' + digits (foreign, leave alone)
  3. else (no '+'):
       if len(digits) == 12 and digits.startswith('91'):  ->  last 10
       if len(digits) == 11 and digits.startswith('0'):   ->  last 10   (STD 0‑prefix)
       if len(digits) == 10:                              ->  digits
       else: invalid
  4. a clean 10‑digit result is stored as '+91' + digits (matches today's
     validate_phone normalisation); a foreign '+<cc><num>' is stored verbatim.
```

Rationale for "trim leading 91 → 10 digits": for this base, a 12‑digit
`91XXXXXXXXXX` is *always* an Indian number copied from WhatsApp, never a
genuine `+91 9...` where the 9 is a country code. Safe to collapse.

### Validation after normalisation

- `+91XXXXXXXXXX` (10 national digits) → valid.
- `+<cc>...` 7–15 digits total → valid (unchanged server rule).
- anything else → inline error, **before** any send.

## 3. Changes

### 3a. Server — `api/app/reference.py`

- Add `_PHONE_ZW_RE` (zero‑width / BOM strip) into the existing
  `_PHONE_STRIP_RE` pass, or a second `.sub`.
- In `validate_phone`, after `cleaned = _PHONE_STRIP_RE.sub(...)`:
  - if `cleaned` is `+` + 12 digits starting `91` → reduce to `+91` + last 10.
  - if `cleaned` is 12 digits starting `91` (no `+`) → `+91` + last 10.
  - if `cleaned` is 11 digits starting `0` → `+91` + last 10.
  - keep the existing "bare 10 → `+91`" branch.
  - keep the existing `_PHONE_SHAPE_RE` gate for everything else.
- Error text unchanged.
- **Tests** (`api/tests/test_reference.py` or wherever `validate_phone` is
  covered — grep first): table of
  `("+91 98765 43210", "+919876543210")`,
  `("919876543210", "+919876543210")`,
  `("+919876543210", "+919876543210")`,
  `("09876543210", "+919876543210")`,
  `("Ramesh 9876543210", raises)`  ← name prefix still rejected (letters),
  `("98765 43210", "+919876543210")`,
  `(" +91 9876543210", "+919876543210")`,
  `("+1 415 555 0100", "+14155550100")`  ← foreign untouched.

### 3b. Web — `web/src/lib/reference.ts`

- Add and export:
  ```ts
  /** Normalise a pasted phone for storage/send. Returns the canonical string
   *  ("+91XXXXXXXXXX" for India, "+<cc><digits>" for foreign) or null if it
   *  can't be made into a valid number. Mirrors validate_phone in
   *  api/app/reference.py — keep in lockstep. */
  export function normalizePhone(raw: string): string | null
  ```
- Reimplement `phoneError` to run `normalizePhone` and return the message when
  it yields `null` (keeps one source of truth).
- Optional helper `phoneDisplay(stored)` → `"98765 43210"` for read views
  (nice, not required for this slice).

### 3c. Web — party forms

`web/src/components/PartyForm.tsx` and `NewPartyForm.tsx` (and
`QuickCreatePartyDialog` if it has its own phone field — grep):

- On **blur** of the phone field: run `normalizePhone`; if non‑null, replace the
  field value with the canonical form (so the user sees `+91…` land). If null,
  show `phoneError` inline.
- Keep sending the raw value too is unnecessary — send the normalised value.
- Accept spaces/paste while typing (no `onChange` filtering that fights paste);
  only normalise on blur + on submit.

### 3d. Web — WhatsApp send dialog (`InvoiceEditorPage.tsx`, `WhatsappSendDialog`)

- Replace `const otherDigits = otherPhone.replace(/\D/g, "");
  const otherValid = otherDigits.length >= 10;` with:
  ```ts
  const otherNorm = normalizePhone(otherPhone);      // string | null
  const otherValid = otherNorm !== null;
  ```
- Use `otherNorm` (not `otherPhone.trim()`) as the target phone.
- Show `phoneError(otherPhone)` under the input once the field is non‑empty and
  blurred.
- **Save‑to‑party prompt.** After a successful send to the "Other number"
  target **and** `toParty`'s party exists **and** (`partyPhone` is empty **or**
  `normalizePhone(partyPhone) !== otherNorm`):
  - render an inline line: `Save +91XXXXXXXXXX to <partyName>?  [Save]`
  - `[Save]` → `api("/parties/<id>", { method: "PATCH", body: { phone: otherNorm } })`
    then show `Saved.` and, if the party had no phone, flip the "Send to party"
    checkbox context for next time (local only; dialog closes anyway).
  - needs the party **id** passed into the dialog — currently only
    `partyName` / `partyPhone` are passed
    ([InvoiceEditorPage.tsx:1085‑1087](../web/src/pages/invoices/InvoiceEditorPage.tsx#L1085-L1087)).
    Add `partyId={inv.party?.id ?? null}` and thread it through.
  - hide the whole prompt when `partyId` is null (no party on the invoice).

### 3e. Server — drop the opt‑in gate

`api/app/services/whatsapp.py` `send_invoice`, the `to_phone omitted` branch
([~line 374‑378](../api/app/services/whatsapp.py#L374-L378)):

- Remove the `if not party.whatsapp_optin: raise WhatsappError(...)` check.
- Keep `if not party.phone: raise WhatsappError("party has no phone on file")`.
- Update the docstring (the `to_phone omitted` paragraph) to drop the opt‑in
  requirement.
- `whatsapp_optin` column + schema field: **leave in place** (no migration, no
  churn) but stop reading it. Note in the model comment that it's currently
  unused. `parties.py` still returns it in `PartyOut`; harmless.
- **Tests**: `api/tests/test_whatsapp*.py` — the test that asserts a 4xx/error
  when the party isn't opted in must flip to asserting a **successful send**
  when the party has a phone. Grep `whatsapp_optin` across `api/tests/`.

## 4. Out of scope / deferred

- `phoneDisplay` grouping in list/detail read views — cosmetic, later.
- De‑dupe "this number already belongs to another party" — later; for now a
  save just sets `party.phone`.
- Multiple numbers per party — no. One `party.phone`.
- Delivery‑status badge + message log + `WHATSAPP_APP_SECRET` wiring — separate
  slice (`EXECUTION-PLAN-f2-whatsapp-finish.md`, not yet written).

## 5. Files touched

```
api/app/reference.py                         normalise 91‑prefix / 0‑prefix / zero‑width
api/app/services/whatsapp.py                 drop opt‑in gate, docstring
api/app/models/party.py                      comment: whatsapp_optin now unused
api/tests/test_reference.py (or equiv)       normalisation table
api/tests/test_whatsapp*.py                  opt‑in‑removed behaviour
web/src/lib/reference.ts                     normalizePhone(), phoneError rewrite
web/src/components/PartyForm.tsx              blur‑normalise + inline error
web/src/components/NewPartyForm.tsx           same
web/src/components/QuickCreatePartyDialog*    same (if separate phone field)
web/src/pages/invoices/InvoiceEditorPage.tsx  dialog: normalizePhone, save‑to‑party prompt, thread partyId
```

## 5a. What actually got built (2026-09-08)

- **`api/app/reference.py`** — `_PHONE_STRIP_RE` reverted to ASCII‑only; new
  `_PHONE_JUNK` translate‑table (NBSP + zero‑width + BOM code points) applied
  first in `validate_phone`. Added the `91`‑prefix‑12‑digit collapse and the
  `0`‑prefix‑11‑digit collapse *before* the shape check. "Bare 10 → +91"
  branch kept.
- **`api/app/services/whatsapp.py`** — removed the `if not party.whatsapp_optin`
  guard from `send_invoice`; docstring updated. `to_phone` path unchanged
  (still routes through `_phone_e164`, which returns digits‑only for Meta).
- **`api/app/models/party.py`** — `whatsapp_optin` comment: legacy, no longer
  read, column kept to avoid a migration.
- **`api/app/routers/whatsapp.py`** — `to_phone` field description updated.
- **`api/tests/test_field_validation.py`** — `test_phone_collapses_indian_paste_shapes`
  (6 params incl. an NBSP+ZWSP case), `test_phone_foreign_number_left_alone`,
  `test_phone_name_prefix_still_rejected`.
- **`api/tests/test_whatsapp_send.py`** (new) — party needs only a phone (no
  opt‑in); party without a phone is rejected; explicit `to_phone` bypasses the
  party. Meta HTTP layer monkeypatched.
- **`web/src/lib/reference.ts`** — `stripPhoneJunk()` (ASCII sep regex +
  per‑code‑point invisible strip, avoids the `no-misleading-character-class`
  lint), `normalizePhone(raw): string | null` mirroring the server,
  `phoneError` rewritten on top of it.
- **`web/src/components/PartyForm.tsx`**, **`NewPartyForm.tsx`** — phone field
  normalises on blur.
- **`web/src/pages/invoices/InvoiceEditorPage.tsx`** —
  `QuickCreatePartyDialog` phone normalises on blur + on submit + inline
  error; `WhatsappSendDialog` now takes `partyId` + `onPartyPhoneSaved`, uses
  `normalizePhone` for the "Other number" target, shows an inline error on a
  bad number, and after a successful send to a typed number that differs from
  the party's shows **"Save +91… to \<party\>? [Save]"** → `PATCH /parties/{id}`.

Deviations from the plan above: no separate `previewTotal`‑style helper; the
`_PHONE_JUNK` handling on the server is a `str.translate` table, not a second
regex. Everything else as specced.

## 6. Verification

- Backend: `pytest api/tests/test_reference.py api/tests/test_whatsapp*.py`
  (use an isolated DB URL per the conftest SQLite‑file gotcha in memory).
- Full backend suite green.
- Web: `tsc`, `eslint`, `vite build`.
- Manual: paste `+91 98765 43210`, `919876543210`, `Ramesh\n+91 98765 43210`
  (last should error) into the party form and the WA dialog; send to an "Other
  number", confirm the "Save to <party>?" line appears and the PATCH lands.
- Visual review of the dialog change (per the UI guardrail — no browser run of
  review HTML).
