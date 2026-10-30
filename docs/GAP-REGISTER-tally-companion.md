# Tally companion: gap register

Opened 2026-10-30 after the architecture review. This is the single place every gap, decision and phase is tracked. A gap is closed only when its row says so here. Do not drop a gap because a newer topic is more interesting: add the new topic as a new row instead.

Evidence: **Code** = confirmed by reading the code. **Expect** = how Tally is expected to behave, not yet reproduced. **Live** = reproduced against a real Tally.

Status: Open / Decided / Building / Done / Parked.

Work items for every gap and decision are in [BACKLOG-tally-companion.md](BACKLOG-tally-companion.md); its traceability tables must stay complete.

## Target shape (decided in discussion, 2026-10-30)

```
Account (Fleek org)      owner, plan, WhatsApp, people, agent installs
  └─ Firm (tenant)       customers, items, catalog, orders, parties
       └─ Tally company x N, all active at once (GST, Estimate, branch, old FY)
```

- Firm is not the company. A firm has many live Tally companies, each with its own agent, GUID, ledger map, series and visibility.
- A document picks its company at draft: explicit choice, then party default, then document type, then firm default. Locked at finalize.
- Most common real case: GST company plus Estimate company for the same business. The design must also serve sister firms, branch companies and old/new FY.
- `tenant` stays the firm, so no `tenant_id` FK is rewritten.

## Gaps

### A. Company and shop identity
| ID | Gap | Sev | Evidence | Proposed resolution | Phase | Status |
|---|---|---|---|---|---|---|
| A1 | Sales and purchase push XML named no `SVCURRENTCOMPANY`; vouchers land in whichever company the gateway has, and the agent never checks | Critical | Live (2026-10-30) | Done in S0.2: company tag on every push. Live: Tally accepts the tag and refuses a company that process has not loaded (`Could not set 'SVCurrentCompany' to 'X'`, nothing posted). Still to do: agent verifies the company GUID (S0.3) and turns the cryptic refusal into plain text. Two companies inside ONE Tally process not reproduced (needs both loaded in one window) | P0 | Building |
| A2 | Company is a name string; rename or FY split breaks links silently; no company GUID or period stored | High | Code | Store company GUID and books-from; relink step remaps GUIDs; history of earlier companies | P0/P1 | Building: S0.4 built (uncommitted), GUID + books-from stored by "Verify company", rename/different warned; relink remap and history still open |
| A3 | A person cannot belong to two firms (`app_user.tenant_id` single FK, mobile unique); no switcher | High | Code | `membership(user, tenant, role)`; active firm in token; switcher | P3 | Open |
| A4 | One agent per tenant; shops run several PCs and installs | Med | Code | Account-level agent registry; agent reports the companies it sees; owner maps them | P3 | Open |
| A5 | The agent has one `GatewayUrl` (default `127.0.0.1:9000`). Companies open in separate Tally processes, each on its own port, are unreachable; the backend never says which port a company is on | High | Live (2026-10-30: Fleek on 9000, Fleek Test on 9001, one company each) | Gateway address per company (port or URL) stored on the company link, discovered by the agent scanning for gateways; agent routes each job by company | P1 | Open |

### B. Master mapping
| ID | Gap | Sev | Evidence | Proposed resolution | Phase | Status |
|---|---|---|---|---|---|---|
| B1 | Push matches by name; GUID stored but unused; `tally_guid` not unique; `tally_link` unique per (tenant, entity) so one GUID per entity | High | Code | `tally_link` unique per (company, entity); GUID is the key once linked, name for first link only; visible "name differs" state; retire legacy `tally_guid` columns | P1 | Open |
| B2 | Item identity has four sources: normalized name, firm code, supplier code, Tally name; catalog push can duplicate existing stock items | High | Code | Check-Tally-first before create; per-company stock item link with its own Tally name | P2/P6 | Open |
| B3 | Units not mapped; voucher push sends `uom or "Nos"` and does not create the unit. **Live: an unknown unit does not error. Tally posts the voucher with the quantity and rate blank and the amount kept, so stock is not reduced and nothing warns** | Critical | Live | Per-item Tally unit (`item.tally_unit`), sent in Tally's own spelling; readiness blocks an unknown or different unit. Live rule: a line in any unit other than the item's own posts blank, even if that unit exists (`pcs` on a Nos item, `kg`); `nos`/`NOS` match. Built in S0.5 (uncommitted); open: Postgres migration check, live agent push | P0 | Building |
| B4 | Party role and group collapsed: one `role`, two group names; same ledger can be customer and supplier | Med | Code | Roles as a set; group scoping per company | P2 | Open |
| B5 | `inward.TallyLedgerConfig` and `tally_company.ledger_map` overlap | Med | Code | One ledger map per company; migrate and delete the other | P1 | Open |

### C. Vouchers and sync
| ID | Gap | Sev | Evidence | Proposed resolution | Phase | Status |
|---|---|---|---|---|---|---|
| C1 | One-way, create-only (`ACTION=Create`); edits or deletes in Tally invisible; voucher key falls back to `pushed:<no>` | High | Live | Key found: the Import response gives `LASTVCHID` = the voucher's master id, and the voucher GUID is the company GUID + "-" + 8-digit hex of that id (id 19 gives `...-00000013`). The code already reads `LASTVCHID` third in its list. Still to do: store it, then reconcile by comparing our reference to Tally (see F1 for how to read it back) | P4 | Open |
| C2 | No receipts or payments pushed, no credit notes, and receipts keyed in Tally never reach ageing (voucher import is at raw capture only) | High | Code | Voucher import whole (`PLAN-tally-voucher-import-and-party-prices.md`); receipt push; credit note flow | P4 | Open |
| C3 | **Live: Tally discards our `VOUCHERNUMBER` on an auto-numbered Sales type.** We sent 9101 to 9105 and got 8 to 11. Our invoice number and Tally's voucher number therefore differ today (the bill-wise reference still carries ours) | High | Live | D-7 (decided): own series per origin, which needs a voucher type with manual numbering created in Tally; until then store Tally's number back | P2/P4 | Open |
| C4 | GST hardcoded off (`v1-nongst`); a GST company cannot be served | High | Code | Real GST on the GST company: tax lines, HSN, place of supply, GST ledgers | P5 | Open |
| C5 | One staging batch and one "pull in flight" per tenant | Med | Code | Per company | P1 | Open |

### D. Inward bills, catalog, orders
| ID | Gap | Sev | Evidence | Proposed resolution | Phase | Status |
|---|---|---|---|---|---|---|
| D1 | Inward bill purchase push auto-creates supplier and items in Tally without checking Tally's list; duplicates possible | High | Code | Reuse the catalog push "check Tally" step; route the bill to a company (buyer GSTIN to company) | P2 | Open |
| D2 | Customer catalog, links and orders are tenant-only; which company a resulting invoice goes to is implicit | Med | Code | Party default company; firm default; order carries a company | P2 | Open |
| D3 | `supplier_price_point` has no company dimension | Low | Code | Add when stock moves to per-company | P6 | Open |
| D4 | Three catalog layers (`SupplierCatalogItem`, `CatalogProduct`, `Item`) with no stated owner for Tally stock item creation | Med | Code | State one relationship; one creator of Tally stock items | P6 | Open |

### E. Stock and barcodes
| ID | Gap | Sev | Evidence | Proposed resolution | Phase | Status |
|---|---|---|---|---|---|---|
| E1 | Barcode stops at the label: one internal code, no EAN/supplier aliases, no pack or carton code, `group_code` dormant, no scan path in billing or receiving | Med | Code | Accepted as scope by D-10 (internal code only). Revisit trigger: a pilot shop asks for scan-at-counter or EAN packs; then aliases with a unit multiplier, scan-in at billing first | P6 | Parked |
| E2 | Stock dormant. **Live: negative stock is allowed by default**; three sales of 2 took a ZZTEST item to -6 Nos with no error. No failure by default, but the shop gets no warning either | Med | Live | Read Tally closing stock per company; warn only (D-9) | P6 | Open |
| E3 | Customer catalog "in stock" is unrelated to Tally stock | Med | Code | Decide after E2 | P6 | Open |
| E4 | Item code counter is per firm; the same product gets different barcodes across sister firms | Med | Code | Counter at account level (new codes only; printed codes stay valid) | P3 | Open |

### F. Operations
| ID | Gap | Sev | Evidence | Proposed resolution | Phase | Status |
|---|---|---|---|---|---|---|
| F1 | **Live: posting the same voucher twice (same reference, same number) creates two vouchers.** Tally does not reject duplicates. Also, the `METALERP_REF` UDF cannot be read back (absent from `*.*` and from a named fetch), so it is no use for detecting a retry | High | Live | Built in S0.6 (uncommitted): our reference goes in the standard `REFERENCE` field (live: accepted, found by a filtered export); a push with an unknown outcome is flagged and its retry checks Tally first, completing the earlier push if found. Open: Postgres migration 0071, an end-to-end run through a real agent | P0 | Building |

### G. New, from the hierarchy discussion
| ID | Gap | Sev | Evidence | Proposed resolution | Phase | Status |
|---|---|---|---|---|---|---|
| G1 | No account layer above the firm: `tenant.fleek_org_id` unique, plan and WhatsApp bound to one tenant | High | Code | `account` table; many tenants per account; plan and WhatsApp at account | P3 | Open |
| G2 | One company per firm (`uq_tally_company_tenant`, `uq_backup_shop_tenant`); shops run several at once | Critical | Code | Many companies per firm with `kind`, `is_default`, agent, GUID, series, GST flag | P1 | Open |
| G3 | No per-company visibility; an owner may keep the estimate book from counter staff or the accountant | High | Expect | `visible_roles` per company, honoured by lists, reports, switcher and agent jobs. Cannot be retrofitted safely | P1 | Open |
| G4 | Routing rule: where a bill goes, and the billing switch | High | Design | Order: explicit, party default, document type, firm default; `party.default_company_id`; `invoice.tally_company_id` locked at finalize | P2 | Open |
| G5 | Invoice numbering and document label live on the tenant, not the company | Med | Code | Move series and label to the company | P2 | Open |
| G6 | "Shop" means both the business and the agent install (`BackupShop`) | Low | Code | Rename the install to agent; keep "shop" for the business | P3 | Open |

## Phases (every gap sits in exactly one)

| Phase | Theme | Gaps |
|---|---|---|
| P0 | Safety, small and urgent | A1, A2 (GUID store and check), B3, F1 |
| P1 | Company model | G2, G3, A2 (relink, history), A5, B1, B5, C5 |
| P2 | Routing and documents | G4, G5, D1, D2, B2 (check-first), B4, C3 |
| P3 | Account and people | G1, A3, A4, E4, G6 |
| P4 | Voucher truth | C1, C2, C3 (store back) |
| P5 | GST | C4 |
| P6 | Stock, barcode, catalog | E1, E2, E3, D3, D4, B2 (rest) |

P0 should land before P1 because two companies open at once makes A1 worse, not better.

## Decisions

| ID | Question | Lean | Status |
|---|---|---|---|
| D-1 | Firm count in the plan: one plan per account, with a firm count in the ladder? | **Decided 2026-10-30: yes.** One plan per account; the ladder carries a firm count; companies are not charged. How extra firms are handled (paid add-on vs upgrade) is left to the pricing decision, so the account layer is built with a settable firm limit. Existing tenants each become an account with one firm, so no current plan changes. Tier firm counts to be set in `pricing-ladder-2026-10`. | Decided (add-on open) |
| D-2 | WhatsApp number per account or per firm? | **Decided 2026-10-30: account default, per-firm override later.** One number per account now, firm name in the message. The firm-to-WhatsApp-connection link is designed so a firm can point at its own connection later; no screen and no Fleek change now. Reason to revisit: sister firms with different trade names, since the sender name customers see is the number's profile. Within one firm (GST + Estimate) one number is always right. | Decided |
| D-3 | Move the item code counter to account level now, before more labels print? | **Decided 2026-10-30: yes.** One counter per account for new codes; existing codes stay valid and untouched. Cheap today because every account has one firm; the account counter starts above the highest code across its firms. Items stay per firm (D-11), so two firms selling the same product still have two items with different codes; a shared product identity is a later question (E1/D4). Keep `catalog_product` unique per tenant and rely on the never-repeating counter. This pulls the counter move out of P3 so it can ship with the account table. | Decided |
| D-4 | Can an owner restrict a company to the owner role from day one? | **Decided 2026-10-30: yes, from day one.** Per-company visibility (everyone / owner only / chosen roles), default everyone so current shops see no change. One backend query-layer filter, never per screen. A hidden company is absent from invoice company switch, lists, ledgers, ageing, reminders, statements and agent job views. A user sees only their visible balance, with no hint of the rest. Protects what our app shows, not Tally itself. Check the full `UserRole` list when writing the plan. | Decided |
| D-5 | Does a bill ever post to two companies? | **Decided 2026-10-30: no.** One bill, one company, chosen at draft, locked at finalize. Wrong company after finalize = credit note in that company plus a fresh invoice in the right one (needs C2 credit notes; until then fixed in Tally). Later, not now: explicit "copy to other company" action that creates a separate draft, never a linked mirror. | Decided |
| D-6 | Reminders total both books or go per company? | **Decided 2026-10-30: combined, with a per-company "include in reminders" switch.** One message totalling the companies marked included, itemised by invoice number. A company restricted to owner-only is excluded from combined messages by default until the owner switches it on, so a restricted book never leaks by accident. The approve screen shows which companies each reminder covers. Imported Tally receipts count toward the totals (D-8). Sent from the account's number with the firm name (D-2). | Decided |
| D-7 | Who owns voucher numbering per company: us, Tally, or two-way? | **Decided 2026-10-30: separate series per origin.** We number our own sales in our own series per company (own voucher type or prefix, created in Tally through the import path the catalog push uses). Vouchers keyed in Tally and imported keep Tally's number and never touch our sequence. The sequence and document label move from the tenant to the company (G5). Onboarding must verify the target voucher type allows manual numbering and tell the shop what to switch. Same rule when receipts are pushed later (C2). Not yet reproduced live (C3). | Decided |
| D-8 | Billing-first or Tally-companion-first (which system is the record)? | **Decided 2026-10-30: C, the originator owns.** Records we create are ours; records created in Tally are imported read-only (`source=tally_import`). Masters split by field: Tally owns ledger identity (name, GUID, group) once linked, we own commercial fields (phone, consent, prices, credit days, photos). Our voucher deleted in Tally = flagged "missing in Tally", never auto-deleted; edited in Tally = flagged "changed in Tally", never silently overwritten; finalized invoices stay immutable, corrected by a credit note we push. | Decided |
| D-9 | Stock source of truth: Tally owns it and we read balances? | **Decided 2026-10-30: Tally owns, we read it.** Closing stock per item per company, via a new agent read report, on a schedule or on demand, shown with an "as of" time. Stale when the agent or PC is offline: show the age, never block billing. Negative-stock is a warning only, switchable off per shop. We never write or count stock; locations and scan-in (P6) use the number but never produce it. Whether the catalog "In stock" flag reads it (E3) stays a separate later call. The stock report shape needs a live check (see `tally-stksum-vs-masters`). | Decided |
| D-10 | Barcode: internal code plus aliases, and does a pack or carton get its own code? | **Decided 2026-10-30: internal code only, as now.** No aliases, no pack or carton codes, no scan input. The code stays the one account-wide number (D-3) printed on labels and pushed as the Tally part number. Known cost, accepted: shops whose packs already carry a manufacturer EAN must label with our code. Revisit when a pilot shop asks for scan-at-counter or EAN packs; the recommended shape then was aliases with a unit multiplier, scan-in at billing first. | Decided |
| D-11 | Sister firms: separate firms under one account (not one firm with companies)? | **Decided 2026-10-30: yes.** Rule: same GSTIN / same legal entity = one firm with many Tally companies (GST, Estimate, branch without own GSTIN, old FY). Different legal entity = its own firm under the same account. A branch with its own GSTIN is a separate firm. Cross-firm "this party is our firm X" link is later, not now. | Decided |

## Not verified yet

- Two companies loaded inside ONE Tally process (the original A1 case). Our test had one company per process, on ports 9000 and 9001.
- That Tally stores our `METALERP_REF` UDF at all (it is not exposed to a collection fetch).
- Whether a voucher type set to manual numbering keeps our number.
- A negative-stock company setting that blocks, since the dev companies allow it.
- The Fleek side (org to user membership) comes from `PLAN-fleek-control-plane.md`, not from fleek-backend code.

## Change log

- 2026-10-30: register opened; target shape agreed; all gaps Open.
- 2026-10-30: D-8 decided (C, originator owns). Consequences carried to D-5, D-6, D-7, D-9 below.
- 2026-10-30: D-11 decided (separate firms for different legal entities, own-GSTIN branch is a firm). `tally_company` therefore needs no GSTIN of its own: GSTIN stays on the firm.
- 2026-10-30: D-5 decided (one bill, one company).
- 2026-10-30: D-4 decided (per-company visibility from day one).
- 2026-10-30: D-1 decided (plan per account, firm count in ladder; extra-firm handling deferred to pricing).
- 2026-10-30: D-2 decided (WhatsApp at account, per-firm override later).
- 2026-10-30: D-3 decided (account-level code counter now).
- 2026-10-30: D-7 decided (separate series per origin). C3 resolution is now this plus a live check of manual numbering.
- 2026-10-30: D-9 decided (Tally owns stock, we read per company, warn only). E2 resolution is now the read report plus the warning.
- 2026-10-30: D-6 decided (combined reminders with a per-company include switch). New company field: `include_in_reminders`.
- 2026-10-30: D-10 decided (internal code only; E1 parked with a revisit trigger). All eleven decisions are now taken; D-1's extra-firm add-on waits for pricing.
