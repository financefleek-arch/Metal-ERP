# Visual Plan

Mid-fi screen mockups for Metal ERP — 16 artboards across 6 pages.

## Viewing

- **Published canvas (pan/zoom, page tabs):** https://claude.ai/code/artifact/765d91ca-02c1-48a0-adac-0468aff631f8
- **Local:** open `metal-billing-visual-plan.html` in a browser.
- The individual `.dc.html` files are the source artboards; `canvas.json` is the layout manifest (positions, pages, launch view).

## Pages

| Page | Artboards |
|---|---|
| **1 · Core billing loop** | Onboarding, Dashboard, Parties, Purchase entry, Invoice editor (type-ahead + scan + weight), Item catalogue (merge / group / size numbers / label), Printed invoice A4, Items-sold-this-month report |
| **2 · Data normalization** | The six-layer approach to keeping data sane without slowing billing |
| **3 · Multi-touchpoint & weighbridge** | Bill lifecycle (DRAFT → AWAITING WEIGHT → AWAITING RATE → READY → FINALIZED); weighbridge capture (RS-232 → bridge agent → weighment queue → attach) |
| **4 · Barcode & mobile** | Stack-of-sizes barcode → "size #?" number entry; scan-mode billing + mobile touchpoints |
| **5 · Tally integration** | Tally → our software (import masters); our software → Tally (push Sales vouchers) |
| **6 · Roadmap** | Phase 2 GST detail + the full Stage 0 → 4 maturity ladder |

## Status of this design

Mid-fi. Colour (warm neutral + one steel-blue accent) and typography (Fraunces / IBM Plex Sans) are a first pass, not a committed brand — react to structure and flow. Data shown in the mockups is illustrative.

## Milestone 1 slice

Only part of Page 1 is in scope for the first printed bill: Onboarding, Parties (basic), Item CRUD, Invoice editor (type-ahead only — no scan/weight), Finalize, Printed invoice A4 (minus GST), a basic invoice list. See [`../EXECUTION-PLAN.md`](../EXECUTION-PLAN.md).
