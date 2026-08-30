# Metal ERP

Billing and (eventually) inventory platform for a metal-trade business — steel, aluminium, iron sold by weight, plus MRP trader goods (utensils, hardware, fittings).

## Approach

**Non-GST billing first, GST as an additive Phase 2.** The system starts as a simple invoice generator whose item master *accretes* from what gets billed, and gradually matures toward a full inventory. Every GST / stock / barcode column exists in the schema from day one but stays dormant until its stage.

See:

- [`docs/EXECUTION-PLAN.md`](docs/EXECUTION-PLAN.md) — the plan to first printed bill (~8–9 weeks, one dev)
- [`docs/DESIGN.md`](docs/DESIGN.md) — full architecture, data model, and the maturity ladder (Stage 0 → 4)
- [`docs/visual-plan/`](docs/visual-plan/) — mid-fi screen mockups (16 artboards). Open `metal-billing-visual-plan.html` in a browser, or view the published canvas:
  **https://claude.ai/code/artifact/765d91ca-02c1-48a0-adac-0468aff631f8**

## Milestone 1 — First Printed Bill

A deployed web app where the shop can: onboard the business → add a party → add line items (typed, with type-ahead) → enter qty/rate → finalize → download a print-accurate A4 PDF matching the reference invoice layout (minus GST columns).

Out of scope for M1 (designed-for, dormant): GST/IRN/e-Way Bill, weighbridge integration, barcodes/scanning, Tally voucher push, mobile app, multi-touchpoint roles, stock tracking, payments.

## Stack (planned)

- **API**: Node 20 + TypeScript, Fastify, Prisma, PostgreSQL (row-level security per tenant)
- **Web**: React + Vite + TypeScript, TanStack Query, React Hook Form + Zod, Tailwind
- **PDF**: Puppeteer + headless Chromium rendering a print HTML template (same template feeds the live editor preview)
- **Shared**: `packages/tax-core` — pure invoice math (subtotal, discount, round-off, amount-in-words); consumed by both API and web

## Repo layout (target)

```
api/                 Fastify service
web/                 React app
packages/
  tax-core/          pure invoice calculation + tests
  tally-import/      Tally XML → staging (optional pre-seed)
docs/
  EXECUTION-PLAN.md
  DESIGN.md
  visual-plan/       .dc.html artboards + canvas.json + published html
```

## Status

Planning. No application code yet — this commit is the plan and the visual design.
