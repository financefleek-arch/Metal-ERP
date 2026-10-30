# Design: the agent checks and routes by company (S0.3 + S1.10)

Status: DRAFT for review, 2026-10-30. Nothing built. Closes gaps A1, A2 (rest), A5. Replaces the two
backlog stories S0.3 and S1.10 with one agent release.

## 0. Update after the one-window test (2026-10-30)

Both dev companies were loaded in ONE Tally window (port 9000). Live results:
- The Company collection on 9000 lists **both** companies with their GUIDs, without naming a company.
- A sales voucher posted to `SVCURRENTCOMPANY` "Fleek" was created (voucher id 28). The same voucher for
  "Fleek Test" was evaluated in that company's books: Tally said `Ledger 'ZZTEST Customer' does not exist!`
  (the ledger exists only in "Fleek") and posted nothing. So one gateway routes by company name.

Consequence: **one window, one gateway is the normal case and needs no routing.** Phase the work:
- **S0.3 (first release):** the agent checks the named company, by GUID when known, against the single
  gateway's company list, and gives the plain messages in 2.1 (rows `wrong_company`, `company_renamed`,
  `company_not_open`). One gateway only; checkin reports the open companies.
- **S1.10 (later, only if shops need it):** several gateways, port scan and routing (2.1 items 1-2, the
  `company_ambiguous` row). Decisions 1 below waits until then; decisions 2-4 apply to S0.3.

Still not verified: separate Tally windows for two companies remain a supported-in-theory case only.

## 0.1 Decisions taken and how each works (2026-10-30)

- **Company not open: retry** for the 8-minute window (decision 2).
- **Reporting.**
  - Waiting: the agent pings status `company_not_open` on the job (the channel `no_company_loaded` uses), so
    the panel shows "waiting for X to be opened in Tally".
  - Window ends: the backend's stuck-job cancel names the company from that last status ("X is not open in
    Tally. Open now: A, B." the list comes from the checkin map).
  - Hard stops (`wrong_company`, `company_renamed`): the agent posts a job result `error` with the plain
    text, immediately, no retry. Nothing was sent to Tally, so `push.py` must NOT set `outcome_unknown` for
    these (today it does for any agent-reported failure, which would force a pointless check on retry).
- **Knowing a company was renamed.** Only possible once its GUID is stored. Rename = the stored GUID is open
  under another name. Proposed addition: the backend fills `company_guid` by itself from the checkin map when
  exactly one open company has our name, so the owner need not press Verify first. Before a GUID is stored a
  rename looks like "not open".
- **Relink.** The Tally page lists the open companies (checkin map) and the owner picks one.
  - Renamed: one button "Use the new name" sets `company_name` to Tally's current name and KEEPS the GUID.
    (Today a name change clears the GUID, so this needs its own endpoint.)
  - Different (same name, other GUID): pick the right company from the list, or confirm "this is the same
    shop's new books"; the GUID is replaced.
  - A new financial year is a new company: until EP1 (many companies per firm) it replaces the link; after
    EP1 it is added beside the old one.
  - Today the panel is the operator console (platform admin). An owner-facing version is a later story.
- Decisions 3 (renamed: stop with a message) and 4 (block push on `different`) follow the recommendations
  unless you say otherwise.

## 1. The problem, in what we saw live

- Dev Tally has two companies, each in its **own Tally process**: "Fleek" on port 9000, "Fleek Test" on
  9001. The agent has one `GatewayUrl`, so it can only ever reach one of them.
- S0.2 (built) now tells Tally which company a voucher is for (`SVCURRENTCOMPANY`). If that company is not
  open in the process the agent reaches, Tally refuses and posts nothing, with the cryptic
  `Could not set 'SVCurrentCompany' to 'X'`. Safe, but unreadable.
- Tally cannot catch a **different company that has the same name**. A name is text; the GUID (stored by
  S0.4) is what tells them apart.
- Today the agent never looks at which company is open. It is pure transport.

## 2. What changes

The agent learns to answer, before every job, one question: **which gateway serves this company, and is it
the right one?** Then it sends the job there, or stops with a plain message.

### 2.1 Agent

1. **Gateways.** Settings hold a list of gateway addresses instead of one. Default: scan
   `127.0.0.1` ports 9000-9009 once a minute. A Tally on another PC is added by URL as today. A port that
   does not answer is simply absent.
2. **Open companies per gateway.** One read-only Company-collection call per gateway (verified live; it
   returns name, GUID, books-from and needs no company selected). Cached for 30 seconds within a round.
3. **Before any job** that names a company (`company_name`, and `company_guid` when the backend knows it):

   | Situation | Agent does | Backend sees |
   |---|---|---|
   | Exactly one gateway has this GUID (or, when no GUID is stored, this name) | Sends the job to that gateway | Normal result |
   | A company with this name is open but its GUID differs | Stops. Does not retry | Job error `wrong_company`: "Tally has a company called X open, but it is not the one linked to this firm." |
   | Open under another name, same GUID | Stops. Does not retry | `company_renamed`: "Linked company is now called Y in Tally. Update the name here." |
   | Not open on any gateway | Keeps the job and retries for the 8-minute window, as for "Tally closed" | Status `company_not_open`; if the window ends: "X is not open. Open now: A, B." |
   | Open on two gateways | Stops | `company_ambiguous` |

4. **Same guard on pull, capture, check and push**, so a masters pull can never stage another company's
   ledgers. A job with no company named (old backend, or a firm that left the name blank) behaves exactly
   as today: the one default gateway, whatever is open.
5. **Checkin reports the map**: for each gateway, the companies open (name, GUID). Nothing else changes in
   the heartbeat.
6. The agent stays transport: it compares identities, it does not read vouchers.

### 2.2 Backend

- Outbox payloads add `company_guid` next to `company_name` (push sales, push purchase, push items, pull
  masters, check, capture). Null until S0.4's Verify has run; the agent then falls back to the name.
- Status vocabulary adds `company_not_open`; errors add `wrong_company`, `company_renamed`,
  `company_ambiguous`. They are treated like "Tally not ready" only for `company_not_open`.
- `backup_shop` stores the last reported map (JSON), so the Tally page can **list the open companies and let
  the owner pick one to link** instead of typing a name or a port.
- "Verify company" (S0.4) can then answer from that stored map, with no job to the agent.
- Push readiness gains a blocker when `verify_state = different`.
- Old agents ignore the new fields: they keep working by name through Tally's own refusal (S0.2).

### 2.3 What is dropped

The backlog's `gateway_url` per company is **not needed**. The owner never types a port, and the link is
the company's identity, not an address. Ports can change at the next Tally start without breaking
anything. S1.10 shrinks to the pick-from-open-companies screen and the routing above.

## 3. Decisions for you

1. **Port discovery.** Scan 9000-9009 on this PC by default (recommended), or use only ports listed in
   settings. Scanning means no setup for the 2-window case; the cost is ten quick local connection attempts
   a minute.
2. **Company not open.** Retry for the 8-minute window, like "Tally closed" (recommended), or fail at once.
   Retrying fits an owner who opens the company right after clicking; failing at once is clearer.
3. **Renamed company.** Stop with a message (recommended), or let the backend rebuild the voucher with the
   new name. Stopping is simpler and keeps the agent pure; the owner fixes the name once.
4. **Push blocker on `different`.** Yes, block until the owner re-verifies or relinks (recommended).

## 4. Tests

- Agent (xUnit, fake gateways on two ports): match by GUID; match by name when no GUID; same name with
  another GUID; renamed; not open then opened within the window; open on both; no company named; gateway
  list with a dead port; checkin carries the map; the old single-gateway setting still works.
- Backend: payload carries the GUID; each new status and error is classified correctly; push blocker;
  stored map and the pick-a-company screen.
- Live, on your two windows: push to each company by name; close one and see `company_not_open`; open a
  same-named company from another file to see `wrong_company`.

## 5. Release

New agent version: build, sign, **pin one shop as canary**, then promote, as in the delivery-hardening flow.
The backend ships first and is safe with old agents.

## 6. Not verified yet

- Whether two Tally processes can open the **same** company file at once (assumed locked, so
  `company_ambiguous` is rare).
- Whether every TallyPrime build returns the GUID in the Company collection; we saw it on the two dev
  instances only.
- Port discovery on a shop PC with a strict firewall or a Tally on a server.

## 7. Size

Agent L (routing, scan, map, tests, release), backend M, web S. Order: backend payload and statuses first,
then the agent, then the pick screen.
