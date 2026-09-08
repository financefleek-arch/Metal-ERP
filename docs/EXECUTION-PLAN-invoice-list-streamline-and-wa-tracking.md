# EXECUTION PLAN — Invoice list streamline + WhatsApp send-status UI

Status: **BUILT 2026-09-08, UNCOMMITTED, not deployed.** No migration.
Third slice of S1 / F2-finish in `MASTER-tally-complement-saas.md`. Backend
suite green (391 pass / 1 skip, +5 tests); web `tsc` / `eslint` / `vite
build` clean. NOT visually reviewed in the running app, NOT run e2e vs Meta.

Depends on the webhook fan-in slice
(`EXECUTION-PLAN-whatsapp-delivery-tracking.md`) for the statuses to ever
advance past `sent` in production — but the UI degrades fine without it
(everything just shows `Sent`).

## Why

The invoice list row (per the screenshot the user shared) stacked
`FINAL` over `UNPAID` badges + 3–4 always-visible buttons, with no room for
a WhatsApp indicator. Decided in `docs/visual-plan/invoice-list-streamline-review.html`:

- WhatsApp column = **glyph + word** (`✓✓ Delivered`, colour-coded, tooltip).
- **PDF stays a visible row button**; Open / Duplicate / Delete move into one **⋯** menu.
- Payment = **dot + word in the meta line** under the party (near the money).
- **"FINAL" dropped** — a numbered row is final; only Draft / Cancelled get a coloured word.
- Full send history lives on the **invoice detail**, not the list.

## Backend (`api/`)

- **`app/schemas_invoice.py`**
  - `WhatsappStatusLabel = Literal["pending","sent","delivered","read","failed"]`.
  - `InvoiceListItem` gains `whatsapp_status: WhatsappStatusLabel | None` — latest send, `None` if never sent.
  - New `InvoiceWhatsappMessageOut` (id, to_phone, template_name, status, error, wa_message_id, sent_at, delivered_at, read_at, created_at).
- **`app/routers/invoices.py`**
  - `list_invoices`: a windowed subquery (`row_number() over (partition by invoice_id order by created_at desc, <status-rank> desc, id desc)`) picks one WhatsApp status per invoice in the same round trip as the paid-amount aggregate. Status-rank tiebreak (`failed/read > delivered > sent > pending`) so a same-timestamp "send to two numbers" batch never under-reports. `outerjoin`ed; column is `None` for invoices with no messages.
  - New `GET /api/invoices/{id}/whatsapp` → `list[InvoiceWhatsappMessageOut]`, newest first, tenant-scoped (reuses `_load` for the 404).
- **`api/tests/test_whatsapp_send.py`** +5: log endpoint (order + empty), tenant-scoping 404, list column null-then-latest. (Existing send + webhook tests unchanged.)
  - Test gotcha handled: `whatsapp_message.created_at` has second-ish resolution and the PK is a random UUID, so tests that assert ordering set `created_at` explicitly (`_add_msg(..., created=...)`) rather than relying on insert order.

## Frontend (`web/`)

- **`src/lib/types.ts`** — `WhatsappStatus` union; `InvoiceListItem.whatsapp_status`; `InvoiceWhatsappMessage`.
- **`src/components/WhatsappStatus.tsx`** (new)
  - `WhatsappBadge({status})` — one glyph + word for the list column. `null` → grey "Not sent". `delivered`/`read` green, `failed` red (alert glyph).
  - `WhatsappLog({invoiceId, onResend})` — the detail-page card. `useQuery(['invoice-whatsapp', id])`, renders nothing until ≥1 message, one line per attempt (recipient · timeline / error · status). `refetchInterval` = 15s **only while a row is `pending`/`sent`**, else off. "Send again" button calls `onResend`.
- **`src/pages/invoices/InvoiceListPage.tsx`** — rewritten row:
  - grid `48px | party(1fr) | amount 128px | whatsapp 150px | actions 84px` on desktop; a 3-col stack on mobile.
  - `stateLabel()` → only Draft/Cancelled; `<PaymentDot>` → coloured dot + word; date + payment in the meta line.
  - `WhatsappBadge` shown only once `status === "final"`.
  - Visible **PDF** button (final only) + new **`<RowMenu>`** (self-contained, click-outside to close): Open / Download PDF / Duplicate / Delete.
- **`src/pages/invoices/InvoiceEditorPage.tsx`**
  - `<WhatsappLog invoiceId onResend={() => setWaOpen(true)} />` rendered below the save/PDF notices, `finalized` only.
  - `WhatsappSendDialog` gains its own `useQueryClient`; after a successful send it invalidates `['invoice-whatsapp', invoiceId]` and `['invoices']` so the log + list badge refresh immediately (webhooks then advance them on their own).

## Not in this slice

- Badge on the invoice **list** for `pending`/`sent` polling — the list query
  is not on an interval; a manual refresh or navigating back updates it.
  (The detail log does poll.) Add list polling later if wanted.
- Mobile card visual polish beyond the stacked grid.
- Any change to `InvoiceListPage`'s filters or search.

## Verification done

- `pytest` full suite: 391 pass / 1 skip.
- `tsc --noEmit`, `eslint`, `vite build`: clean.
- Review mock updated with the locked decisions + a working ⋯ toggle.

## Verification still needed

- Run the app, look at the list + a finalized invoice's detail.
- After the fan-in slice deploys: send a real invoice, watch the badge go
  `Sent → Delivered → Read`, and a bad number go `Failed` with Meta's reason.
