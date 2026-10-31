# Parity table: Tally company management (S1.4)

Status: DECIDED 2026-10-31, owner routes BUILT (see the end). Theme from the owner: avoid unnecessary
centralization; users manage their own things unless it costs us.

Answers to the four open questions: (1) yes, "install the agent first" with a clear message; (2) no limit
on companies, since a company costs us nothing (the agent record does, so provisioning, key and version
stay with Fleek); (3) verify and "use the company that is open" are open to the owner and accountant;
(4) the owner adds, edits, makes default and switches off; the accountant keeps the link healthy (verify,
relink, ledger names, pull masters) and sees only the companies open to their role. The Fleek-admin proxy
is for support and is read-only to start with.

Decisions behind it (2026-10-31):
- The owner adds, edits, makes default, and switches off Tally companies in their own Metal app.
- Metal admin is being retired: nothing new is built there. Operator actions go through Fleek admin, using the control-plane proxy (`routers/control_plane_firm.py`).
- Logic lives in services (`services/tally/companies.py`, `company_verify.py`, `visibility.py`), so the owner routes and the Fleek proxy call the same code. No second copy.

## The table

"Fleek admin today" = what `control_plane_firm.py` forwards now: firm settings, users, slip usage, and the agent record (`tally-shop`: provision, rotate key, target version, installer). It forwards nothing about companies.

| # | Action | Owner in Metal | Metal admin today (to retire) | Fleek admin needs | Notes |
|---|---|---|---|---|---|
| 1 | See the firm's companies, state, series, visibility | NEW `GET /api/tally/companies` (visible ones only, S1.7) | `GET /api/admin/firms/{id}/tally/company` (default only) | proxy `GET .../tally/companies` (all, no filter) | One list endpoint, one service call |
| 2 | Add a company | NEW `POST /api/tally/companies {company_name, kind}` | `POST .../tally/company` (upsert default) | proxy `POST .../tally/companies` | Owner may add (decided). Attaches to the firm's own agent. Plan limit not built (S3.5) |
| 3 | Edit name, kind, bill title, number prefix, reminders switch, who can see | NEW `PATCH /api/tally/companies/{id}` | name only | proxy `PATCH` | Prefix must be unique in the firm. Name change clears the stored GUID and verdict (existing rule) |
| 4 | Make default | NEW `POST .../{id}/default` | none | proxy | `companies.set_default` exists |
| 5 | Switch off / on | NEW `POST .../{id}/status` | none | proxy | Default cannot be switched off. Nothing deleted |
| 6 | Check it matches what is open in Tally | NEW `POST .../{id}/verify` and `GET .../open` | `POST /company/verify`, `GET /company/open` | proxy both | `company_verify` exists |
| 7 | Fix a rename: "use the company that is open" | NEW `POST .../{id}/relink {guid}` | `POST /company/relink` | proxy | Needed by the owner now that they add companies |
| 8 | Ledger names (sales, cash, bank, purchase, GST) | NEW `PUT .../{id}/ledger-map` | `PUT /company/ledger-map` | proxy | Per company (S1.5 done) |
| 9 | Pull masters, see jobs | NEW `POST .../{id}/pull-masters`, `GET /api/tally/jobs` | `POST /pull-masters`, `GET /sync-jobs` | proxy | Per company guards done (S1.6) |
| 10 | Link next year's company | NEW guided flow over 2, 6, 9 | none | proxy for support | S1.8 core verified; screen is new |
| 11 | Provision the agent, rotate its key, set target version, installer | owner already downloads the installer (`/api/tally/installer`); the rest NOT given to owners | `tally-shop` routes | ALREADY proxied | Stays operator-only |
| 12 | Gateway address per company (several Tally windows) | not shown | none | later, with S1.10 | Deferred |

## What is new versus what Fleek admin already has

- New routes the owner app needs and Fleek admin does not have: rows 1 to 10. Each is a thin route over an existing service function.
- The Fleek-admin proxy gets the same rows with `fleek_org_id` instead of the signed-in firm. Same service calls, same response shapes, so the two screens cannot drift.
- Nothing is added that has no counterpart on both sides, except row 11 (operator-only by design) and row 12 (deferred).

## Things to confirm

1. Row 2: an owner adding a company needs the firm to have an agent record already. If none exists, the owner sees "Install the Tally agent first" with the installer link, not an error.
2. Row 2: no limit on how many companies until the plan work (S3.5). Acceptable for now?
3. Rows 6 and 7 are open to the owner, not only the operator. They were operator-only; this widens them. Acceptable?
4. Row 3: should an accountant be allowed to edit, or only the owner? Proposal: only the owner edits and adds; the accountant can see and use the companies they are allowed to see.

## Order of work once agreed

1. Services first: add `add_company` limit hook, prefix uniqueness, `set_status`, and the owner-facing list that applies `visible_companies`.
2. Owner routes (rows 1 to 9) and tests, per-role.
3. Fleek-admin proxy rows 1 to 9 over the same services.
4. Owner screens 1 to 3 from the mockups, then the invoice-editor switch (S2.3).
5. The old Metal admin Tally panel is left as is and removed when Fleek admin has the rows.

## Built 2026-10-31 (owner side)

`routers/tally_companies.py`, prefix `/api/tally/companies`: `GET` (visible companies, owner also sees
`visible_roles`), `POST` (owner; 409 `agent_missing` when no agent), `PATCH /{id}` (owner), `POST /{id}/default`
and `/{id}/active` (owner), `POST /{id}/verify`, `GET /open/now`, `POST /{id}/relink`, `PUT /{id}/ledger-map`,
`POST /{id}/pull-masters` (owner or accountant). Services: `companies.update_company`, `set_active`,
`check_unique` (name and prefix unique per firm, "Sales" reserved for the default). Tests:
`tests/test_owner_companies.py` (per role, other firm, duplicates, rename). Not built yet: the web screens
and the Fleek-admin read proxy.

## Built 2026-10-31 (Fleek admin, read only)

`routers/control_plane_firm.py` now carries three signed, read-only routes under
`/api/internal/control-plane/firms/{fleek_org_id}`: `GET /tally-companies` (every company including switched-off
ones, with who may see each, as the owner sees it), `GET /tally-companies/{id}/refill` (what the company holds from
Tally and where the latest refresh stands) and `GET /tally-companies/{id}/jobs` (its latest Tally jobs, newest
first). They call the owner routes' own functions (`company_out`, `refill_summary`, `recent_jobs`), so the two
screens cannot drift; another firm's company is a 404. No write route exists from Fleek (tested), which keeps to
the decision that owners manage their own companies. Rows 1 and 9 (list, jobs) are covered; rows 2 to 8 and 10 stay
owner-only until there is a reason for support to act on a company, which would be a new decision. Tests:
`tests/test_control_plane_companies.py`. The Fleek admin SCREEN (in fleek-frontend) that shows these is not built.

