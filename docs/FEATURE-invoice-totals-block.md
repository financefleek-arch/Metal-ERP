# Invoice PDF — totals block anomaly fix

**Status:** IMPLEMENTED, uncommitted (2026-09-07). Template-only change. 82 backend
tests pass (finalize / pdf-payment / weighment / crud / tax-vectors / payments).
No migration, no `tax.py`, no `pdf.py`, no FE change.

---

Visual before/after: `docs/visual-plan/invoice-totals-block-review.html`
(open in a browser; carries an IMPLEMENTED banner).

## The anomaly (what was reported)

On a finalized invoice with per-line discounts, the totals box read:

```
Subtotal        10,306.03      <- = sum of the AMOUNT column (already net of line discounts)
Discount        - 1,102.40     <- looks like a second subtraction, but…
Round Off           -0.03
Grand Total     10,306.00      <- barely moves
```

`invoice.subtotal` is **Σ line_total = Σ (gross − line_discount)** — it is *already*
net of every per-line discount. Printing "Discount − 1,102.40" beneath it makes the
discount look either double-counted or ignored, with two near-identical 10,306
figures bracketing a 1,102 discount that appears to do nothing. Reads as a bug to
a customer.

Also reported: the discount total effectively living under the AMOUNT column is
confusing — it belongs under DISC.

## The fix (display only)

All changes in `api/app/templates/invoice_v1_nongst.html`.

### 1. Disc. column — per line

When the operator entered the discount as a **percentage**, the cell now shows that
percentage in brackets:

```
40% (143.60)
```

When entered as a **₹ amount** (`invoice_line.discount_pct` is NULL), the cell shows
just the amount, as before — no bracket. **Nothing is recalculated** — `discount_pct`
is the operator's entered value, persisted as a "UI hint only" column
(`Numeric(5,2)`, nullable) and rendered verbatim, rounded to a whole number:
`{{ ln.discount_pct|float|round|int }}%`.

Consequence: invoices created before the operator started using the % toggle, or
any where ₹ was typed, show the amount with no %. Intended.

### 2. Line-table footer row

A `<tfoot>` row after the last line, **only when `invoice.discount_total > 0`**:

```
        Total — gross before discount        1,102.40    11,408.43
```

- label spans the first 6 columns (`colspan="6"`, right-aligned, bold)
- discount sum in the **Disc.** cell
- gross (`subtotal + discount_total`) in the **Amount** cell

This is what puts the discount total under the DISC. column as requested.

### 3. Totals box — relabelled + reordered to a clean subtraction

```
Gross Amount     11,408.43     <- subtotal + discount_total  (derived in template)
Less: Discount   - 1,102.40    <- discount_total
Net Amount       10,306.03     <- invoice.taxable_total  (frozen at finalize)
Round Off           -0.03
Grand Total      10,306.00
```

- **"Net Amount"**, deliberately not "Taxable Value" — this shop's invoice is
  non-GST, plain wording reads easier for the customer.
- `Net Amount` maps to `invoice.taxable_total`, which the non-GST finalize path
  already freezes (`computed.taxable_total = subtotal − invoice_discount`, clamped
  ≥ 0) — see `api/app/services/invoices/finalize.py:156`. Template falls back to
  `invoice.subtotal` if `taxable_total` is somehow NULL.
- `Gross Amount` is derived in the template as `(subtotal or 0) + (discount_total or 0)`
  — **no new backend field**.
- **No-discount invoices**: the Gross / Less: Discount rows and the whole `<tfoot>`
  are skipped. Box collapses to `Net Amount / Round Off / Grand Total`. Nothing new
  appears on the common (no-discount) case.

### 4. Spacing / CSS

- `.totrow td` padding `2pt 4pt` → `4pt 6pt`
- `.totbox td.r { min-width: 78pt }` so the figures column aligns
- `.totbox tr.net td` — bold, `border-top: 0.5pt solid #999`, `padding-top: 5pt`
  (rule above Net Amount)
- `.grand td` — `border-top: 1pt solid #000; border-bottom: 2.5pt double #000;
  padding: 5pt 6pt` (double underline under Grand Total; previously the grand row
  had full cell boxes)
- `.lines tfoot .lines-foot td { background: #f6f6f6 }`

---

## Guardrail respected

The Unit column still renders through the existing `uom` Jinja filter
(`{{ ln.uom|uom }}`), so the canonical `nos` prints as `pcs`. Not touched — see
memory `uom-nos-vs-pcs-display-only`.

---

## Verification done

- `python -m pytest tests/test_invoice_finalize.py tests/test_invoice_pdf_payment.py
  tests/test_invoice_weighment.py tests/test_invoices_crud.py tests/test_tax_vectors.py
  tests/test_payments.py -p no:randomly` → **82 passed**
  (use `DATABASE_URL=sqlite:///./_mytest_*.db` for isolation — see
  [[invoice-party-optional-slice]] conftest gotcha).
- Template rendered as HTML with representative data for both branches:
  - discount branch: `40% (143.60)` / `10.00` (abs) / footer row / Gross→…→Grand box
  - no-discount branch: no Gross/Discount rows, no `<tfoot>`, box = Net/RoundOff/Grand
- No golden/snapshot test pins the template HTML, so no fixture to regenerate.
- **WeasyPrint PDF render NOT exercised locally** — GTK/Pango native libs absent on
  the Windows dev box (known; `pdf.py` imports weasyprint lazily for this reason).
  Renders on prod (Linux/Docker). HTML output is clean.

---

## Pending

- **Uncommitted** — user does check-ins. Single file:
  `api/app/templates/invoice_v1_nongst.html`.
- **Not deployed.** Deploy is push-webhook; no migration so nothing extra to run.
- **On-screen preview not updated** — `web/src/lib/previewTotal.ts` /
  `InvoiceEditorPage.tsx` still show the old "Subtotal / Discount / …" wording in the
  live editor rail. The PDF and the editor preview now differ in labelling. Small
  follow-up if the mismatch matters.
- **Bracket format** — shipped as `40% (143.60)` (%-first). User was offered
  `143.60 (40%)` as an alternative and did not object; easy to flip in
  `invoice_v1_nongst.html` if wanted.
- Pre-existing unrelated uncommitted work in the same tree (NOT part of this change):
  `pdf.py` (`_fmt_uom` filter), `invoices.py`, `InvoiceEditorPage.tsx`,
  `test_invoice_finalize.py` — the earlier `pcs`/uom-display + default-rate
  alignment slice (`5f49ce6` neighbourhood).
