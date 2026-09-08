# Execution plan — Party de-duplication, backend slice (steps 1–4)

**Status:** BUILT 2026-09-08 (backend **+ UI**), UNCOMMITTED, not deployed.
Backend suite 420 pass / 2 skip (was 402+1; +1 skip = PG-only fuzzy). Web:
`tsc -b` + `vite build` green, eslint clean on touched files. Not run in a
browser — `docs/visual-plan/party-dedupe-review.html` is the static review
(has BUILT banner).
**Date:** 2026-09-08

## UI slice (shipped in this same change)

- `web/src/lib/api.ts` — `ApiError` now carries `.detail` (raw body); a shared
  `parseError()` extracts a string `message` from string / validation-array /
  **object** (`{message}`) detail. All 3 throw sites use it. This is what lets
  the structured 409 survive to the caller.
- `web/src/lib/types.ts` — `PartyResolveMethod`, `PartyMatchRef`,
  `PartyResolveResult`, `PartyDuplicate409` + `isDup409()` type-guard.
- `web/src/components/SimilarParties.tsx` — **new** shared panel. Debounced
  parent passes `name` / `gstin` / `phone`; it POSTs `/parties/resolve`
  (`enabled` at ≥3 chars) and lists candidates with city / GSTIN / last-billed;
  `onUse(ref)` hands the picked party back. Warm-tan (`bg-[#f1e7d6]` +
  `text-warn`) per the design system, `btn-ghost` "Use this".
- `web/src/components/NewPartyForm.tsx` — debounced name/gstin/phone →
  `<SimilarParties>` inline; `create` mutation typed `<Party, unknown, boolean>`
  (`force` arg), `?force=true` on the "create as new" path; `onError` routes a
  409 with `isDup409(e.detail)` into a `dupWarn` panel (candidates + "Use this"
  + force link) instead of the error line; editing the name clears `dupWarn`
  and re-enables Create; `pickExisting(ref)` GETs the full party and calls
  `onCreated`.
- `web/src/pages/invoices/InvoiceEditorPage.tsx`
  - `QuickCreatePartyDialog` — same treatment (debounced `<SimilarParties>`,
    `force` mutation, structured-409 `dupWarn` panel, name-edit clears it).
  - `PartyPicker` — `useDebounced(q, 250)`, min 2 chars; **role filter
    dropped** (`/parties?q=` not `&role=customer`) so an existing *supplier*
    surfaces; picking a `role==="supplier"` row fires a
    `PATCH {role:"both"}` (fire-and-forget); rows now show a `supplier` badge +
    state + `lastSeenLabel(last_txn_at)`; the "+ Create" row is de-emphasised
    to a muted "None of these — add … as new".

### eslint gotcha
A helper named `useExisting` trips `react-hooks/rules-of-hooks` (the `use`
prefix). Renamed to `pickExisting`.

### Item-side parity — deferred (not in this PR)
`create_item` already 409s on the exact key and the invoice `LineRow` already
has `/api/items/resolve` type-ahead, so items are better defended than parties
were. Making `create_item`'s 409 structured + adding a fuzzy check + a
`SimilarItems` panel is real scope for marginal gain — tracked as follow-up.

## What shipped in this slice

- `party.legal_name_normalized` column + migration `0023_party_name_key.py`
  (two-phase; crude in-DB backfill so NOT NULL; btree `ix_party_tenant_namekey`
  + PG-only trgm GIN `ix_party_namekey_trgm` in an autocommit block). Chain is
  `0022 → 0023 → 0024` (0024 "backup_shop tenant unique" was already on disk
  Revises: 0023 — a parallel WIP; my 0023 fills the gap and history resolves
  linearly).
- `app/services/party_resolution.py` — `resolve_party()` ladder: GSTIN → phone
  → exact `legal_name_normalized` → pg_trgm fuzzy (`_FUZZY_FLOOR 0.45`,
  `_ACCEPT 0.72`, `_GAP 0.10`). SQLite skips step 4. `PartyMatch` /
  `PartyCandidate` dataclasses; `_reason()` message formatter.
- `phone_digits_col()` extracted into `app/services/parties.py` (was an inline
  `func.replace` chain in `apply_search`) — one phone-digit-strip impl, shared
  with the resolver's exact phone match.
- `app/schemas.py` — `PartyMatchRef`, `PartyDuplicate409`
  (`code: party_exists | party_maybe_exists`), `PartyResolveResult`.
- `app/routers/parties.py` — `create_party` / `update_party` now compute the
  key, call `resolve_party`, and raise the structured 409 via `_dup_409()`.
  `?force=true` on both bypasses a *fuzzy* (`party_maybe_exists`) match only,
  never hard-key/exact. New `POST /api/parties/resolve` (CurrentUser, no side
  effects). `_match_ref_for_party()` helper.
- `app/routers/parties_import.py` — `_norm_name` → `_name_key(s, syn)` using
  `normalize_name` + tenant synonyms; `legal_name_normalized` stamped on the
  `Party(...)` constructor. In-file + against-DB name dedupe now uses the shared
  key.
- `app/seed.py` — `PARTY_SUFFIX_SYNONYMS` (7 rows: pvt/private/ltd/limited/llp
  → "", and → ""). Deliberately NOT ss/still/steel (those are item vocabulary).
- `tools/backfill_party_namekey.py` — re-keys existing parties through the real
  pipeline + reseeds `seed_synonyms` per tenant; dry-run default, `--apply`,
  `--tenant`; prints existing-duplicate clusters (does NOT merge).
- Tests: `tests/test_party_resolution.py` (11 cases), +7 in
  `test_parties_crud.py` (structured 409, force semantics, PG-only fuzzy behind
  a skipif), +1 in `test_tally_party_import.py` (spelling-variant dedupe).

## Deploy (post-merge)

1. push-webhook → `alembic upgrade head` runs `0023` automatically.
2. **Manual, once per env, right after:**
   `docker compose exec -T metalerp-api python -m tools.backfill_party_namekey --apply`
   Until it runs the key is the crude migration value — exact dedupe works,
   fuzzy candidate quality is lower.

## Learnings / deviations from the plan

- `normalize_name` turns `.` into a *space*, not nothing: `R.K.` → `r k`, so
  `RK` and `R.K.` do NOT share a key. Left as-is (not this slice's job).
- Dropped the `and → &` idea — `&` survives punctuation stripping (synonyms run
  after), leaving `&` as key noise. `and → ""` instead.
- Did not add `co → ""` — `co` appears mid-name too often (`M M Co` → `m m`).
- Test GSTIN must pass the check-digit validator: use `19BHBPK1450P1Z3`
  (already used across inward tests), not a hand-typed one.

---
(original plan below — kept for reference)
**Scope:** backend only. Structured 409 gate + `resolve_party` ladder + normalized dedupe
key on `party`, mirroring what `item` already does. The UI slice (PartyPicker /
QuickCreate / shared SimilarRecords panel) is a **separate** follow-up plan.

---

## Problem

Operators free-type party names. `still` / `steel` / `SS`, `Pvt Ltd` vs `Pvt. Ltd.`,
double spaces, trailing punctuation — every variant creates a **new** `party` row.
Same business re-entered as `customer` and again as `supplier`. Same GSTIN under two
spellings. The catalogue (`item`) already defends against this with
`item.name_normalized` + `resolve_item` + a structured-ish 409. `party` has almost
nothing: `create_party` only does `func.lower(legal_name) == exact`.

## What "done" means for this slice

1. `party.legal_name_normalized` column exists, backfilled, indexed (btree + trgm GIN).
2. `resolve_party()` service — GSTIN → phone → exact-key → fuzzy ladder, Postgres +
   SQLite paths, unit-tested.
3. `POST /api/parties` and `PATCH /api/parties/{id}` return a **structured 409** with
   the matched party / candidate list when a likely duplicate is detected; a
   `?force=true` query param lets a *fuzzy* (not hard-key) match through.
4. `POST /api/parties/resolve` type-ahead endpoint (shape mirrors
   `POST /api/items/resolve`).
5. `parties_import.py` commit path uses the same `normalize_name` key for its in-file
   / against-DB dedupe pre-pass, and stamps `legal_name_normalized` on inserted rows.
6. Full backend suite green; new tests for every ladder rung + the 409 shapes.

Explicitly **NOT** in this slice: any React change, a party-alias table, a party
merge tool, LLM disambiguation, a DB unique constraint on the key.

---

## Reference: how `item` already does it (the pattern we copy)

- `app/domain/normalize.py :: normalize_name(name, synonyms)` — casefold → unify
  `x`/`*` pack separators → strip non-alnum → collapse spaces → apply tenant synonym
  map (phrase keys longest-first, then token-wise; empty tokens dropped). **Generic —
  works for party names unchanged.**
- `app/services/item_resolution.py :: resolve_item()` — ladder: exact
  `name_normalized` → `ItemAlias` → pg_trgm `similarity()` with `_FUZZY_FLOOR 0.55`,
  `_FUZZY_ACCEPT 0.72`, `_RUNNER_UP_GAP 0.10`; SQLite skips the fuzzy rung and returns
  `method=None`.
- `app/routers/items.py :: create_item()` — computes the key, 409s on an **exact**
  key clash (`"Looks like an existing item: {name}"`), starts the row
  `status=unconfirmed`.
- `POST /api/items/resolve` — hydrates candidates to full list-item shape + score for
  the invoice-editor type-ahead.

Divergences we deliberately introduce for parties:

| Concern | item | party (this slice) |
|---|---|---|
| Hard unique keys | none | **GSTIN, phone** checked before name |
| 409 body | bare string | **structured dict** (`code`, `message`, `match`/`candidates`) |
| Fuzzy override | n/a | `?force=true` bypasses *fuzzy* 409, never the hard-key 409 |
| create-path fuzzy check | exact only | exact **and** fuzzy (weak → 409 with candidates) |

---

## Step 1 — Migration + normalized-key column

### 1.1 Model change — `app/models/party.py`

Add to `class Party`:

```python
# The dedupe key — normalize_name(legal_name, tenant synonym map). Mirrors
# item.name_normalized. Maintained by the router on every name write; the
# 0023 migration backfills it and tools/backfill_party_namekey.py re-does it
# through the real pipeline + synonyms.
legal_name_normalized: Mapped[str] = mapped_column(String(200), nullable=False)
```

(SQLAlchemy default not needed — the router always sets it. Tests that build a
`Party(...)` directly will need to pass it; add a small helper in `tests/conftest.py`
or a factory — see Step-4 tests note.)

### 1.2 Alembic `0023_party_name_key.py`

`down_revision = "0022"`.

Two-phase, because the `NOT NULL` needs a value for existing rows and
`CREATE INDEX CONCURRENTLY` can't run in the migration transaction (follow the
`0015` autocommit-block pattern exactly).

```python
def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # 1. add nullable
    op.add_column("party", sa.Column("legal_name_normalized", sa.String(200)))

    # 2. crude in-DB backfill so NOT NULL can be set now. This is a LOWER-QUALITY
    #    key (no tenant synonyms, no unaccent unless available) — good enough to
    #    satisfy the constraint; tools/backfill_party_namekey.py replaces it with
    #    the real normalize_name() output right after deploy.
    if is_pg:
        op.execute(r"""
            UPDATE party SET legal_name_normalized =
              btrim(regexp_replace(lower(legal_name), '[^a-z0-9]+', ' ', 'g'))
        """)
    else:  # sqlite (tests) — good enough; tests set the column explicitly anyway
        op.execute(
            "UPDATE party SET legal_name_normalized = "
            "lower(trim(legal_name)) WHERE legal_name_normalized IS NULL"
        )
    op.execute(
        "UPDATE party SET legal_name_normalized = '' "
        "WHERE legal_name_normalized IS NULL"
    )

    # 3. NOT NULL
    op.alter_column("party", "legal_name_normalized", nullable=False)

    # 4. plain btree for the exact-key lookup (in-txn is fine, it's small-ish;
    #    if prod party count argues otherwise, move into the autocommit block
    #    as CONCURRENTLY IF NOT EXISTS like the trgm one)
    op.create_index(
        "ix_party_tenant_namekey", "party",
        ["tenant_id", "legal_name_normalized"],
    )

    # 5. trgm GIN for the fuzzy rung — CONCURRENTLY, autocommit block, PG only
    if is_pg:
        with op.get_context().autocommit_block():
            op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "ix_party_namekey_trgm ON party "
                "USING gin (legal_name_normalized gin_trgm_ops)"
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_party_namekey_trgm")
    op.drop_index("ix_party_tenant_namekey", table_name="party")
    op.drop_column("party", "legal_name_normalized")
```

**Do NOT** add `UniqueConstraint(tenant_id, legal_name_normalized)` — prod has many
existing dupes; creation would fail, and we want the app (structured 409 + candidate
picker), not a raw `IntegrityError`, to own the decision. `item` has no such
constraint either.

**`unaccent`**: only `pg_trgm` is guaranteed on the metalerp DB (infra repo grants it;
migration `0002`). Don't depend on `unaccent` in the crude backfill. `normalize_name`
does its own NFKD-strip in Python (`_casefold_ascii`), so the real backfill script
handles accents correctly regardless.

### 1.3 `tools/backfill_party_namekey.py`

Standalone, idempotent, run once per env right after the deploy runs
`alembic upgrade head`. Mirrors `tools/reclassify_items` / `tools/backfill_bartan.py`
ergonomics (dry-run default, `--apply`, per-tenant).

```
python -m tools.backfill_party_namekey            # dry run: report rows that would change
python -m tools.backfill_party_namekey --apply    # write the real keys
```

Logic: for each tenant, `load_synonym_map(session, tenant_id)`, then for every party
`key = normalize_name(p.legal_name, syn)`; update where different. Batch the commits
(1k rows) — see `tally_party_import_hardening` memory for the batched-flush gotchas.
Print a summary + any rows whose key **collides** with another party's key in the same
tenant (these are your existing dupes — feed them to the future merge tool; do NOT
merge here).

### 1.4 Deploy note (add to the infra/runbook)

Deploy order is unchanged (push-webhook, `alembic upgrade head` auto-runs). After it:

```
docker compose exec -T metalerp-api python -m tools.backfill_party_namekey --apply
```

Until that runs, `legal_name_normalized` holds the crude key — exact-match dedupe
still works, it's just slightly less spelling-tolerant than it will be. Safe to defer
a few minutes; not safe to skip (fuzzy candidate quality depends on the real key).

---

## Step 2 — `app/services/party_resolution.py`

New module. Pure-ish (one session in, no HTTP). Mirrors `item_resolution.py`
structure.

```python
"""Resolve a free-text party name (+ optional GSTIN / phone) to an existing
`party`. The gate behind create_party / update_party / the import commit and
the /parties/resolve type-ahead.

Ladder, stop at first hit:
  1. gstin   exact on party.gstin (normalised upper)          method="gstin"  1.00
  2. phone   exact on digits(party.phone)                     method="phone"  0.97
  3. exact   normalize_name(name) == party.legal_name_normalized  "exact"     1.00
  4. fuzzy   pg_trgm similarity(legal_name_normalized, key)   "fuzzy"   <score>
             accept top only if >= _ACCEPT and beats runner-up by _GAP;
             else weak=True + candidates[] for the caller/UI.
Postgres only for step 4. SQLite (tests): steps 1-3 work, step 4 skipped.
"""
```

### Constants (tune against a real Tally export in Step 4 review)

```python
_FUZZY_FLOOR   = 0.45   # party names are longer/wordier than item names;
                        # similarity() runs lower than word_similarity(). 0.45
                        # keeps "steel tradrs"~"steel traders" (~0.75) and
                        # "steel traders"~"steel traders co" while rejecting junk
_FUZZY_ACCEPT  = 0.72   # auto-"this IS the same party" — same as item
_RUNNER_UP_GAP = 0.10   # ambiguity guard — same as item
_CANDIDATE_N   = 5
```

### Dataclasses

```python
@dataclass
class PartyCandidate:
    party_id: str
    legal_name: str
    gstin: str | None
    phone: str | None
    city: str | None           # default address city — recognition cue for the UI
    last_txn_at: datetime | None
    status: str                # so the UI can grey an archived match
    score: float

@dataclass
class PartyMatch:
    party_id: str | None
    method: Literal["gstin", "phone", "exact", "fuzzy"] | None
    confidence: float | None
    candidates: list[PartyCandidate] = field(default_factory=list)
    weak: bool = False          # step 4 ran, found rows, none confident enough
```

### Signature

```python
def resolve_party(
    session: Session,
    tenant_id: str,
    name: str,
    *,
    gstin: str | None = None,
    phone: str | None = None,
    exclude_id: str | None = None,
    synonyms: dict[str, str] | None = None,
) -> PartyMatch:
```

### Behaviour details

- `synonyms` defaults to `load_synonym_map(session, tenant_id)` (same lazy pattern as
  `resolve_item`).
- `key = normalize_name(name, synonyms)`. If `not key` → `PartyMatch(None, None, None)`
  (caller 422s).
- **GSTIN rung**: only if `gstin` non-empty. Normalise `gstin.strip().upper()`. Do NOT
  re-validate here (caller's schema already did / import path already tried) — just
  match. Exclude `exclude_id`. Status filter: **include archived** for the hard-key
  rungs (you want to know a dup exists even if it was archived — the UI can offer
  "un-archive & use"), but see the consistency note below — if you'd rather match
  `item` and exclude archived everywhere, that's an acceptable simplification; pick
  one and note it.
- **Phone rung**: only if `phone` non-empty. Compare on a digits-only expression.
  Reuse the `func.replace(...)` digit-strip chain that `parties.apply_search` already
  builds ([services/parties.py](../api/app/services/parties.py) ~line 107) — extract
  it into a shared `_phone_digits_col()` helper in `services/parties.py` and import
  it here so there's one implementation. Match on
  `_phone_digits_col() == digits(phone)` (full match, not substring — substring is for
  search, not identity).
- **Exact rung**: `legal_name_normalized == key`, tenant-scoped, `!= exclude_id`.
- **Fuzzy rung**: `_is_postgres(session)` guard (copy the helper). `sim =
  func.similarity(Party.legal_name_normalized, key)`; `where sim >= _FUZZY_FLOOR`,
  `order_by sim.desc()`, `limit 8`. Build `PartyCandidate`s (join the default
  `PartyAddress` for `city` — one extra query keyed by the returned ids is fine, don't
  N+1). Sort by score desc. If `top.score >= _FUZZY_ACCEPT and (top.score -
  runner_up) >= _RUNNER_UP_GAP` → `PartyMatch(top.id, "fuzzy", round(min(score,
  0.99), 3), candidates=cands)`. Else `PartyMatch(None, None, None,
  candidates=cands, weak=True)`.
- **SQLite**: after the exact rung, `if not _is_postgres: return PartyMatch(None,
  None, None)`.

### Tests hook

Add a tiny `_reason(match)` formatter here too (used by the router for the 409
`message`): `"A party with GSTIN 09AAA… already exists: Steel Traders"` /
`"A party with phone +91 98… already exists: Steel Traders"` /
`"A party named 'Steel Traders' already exists"`.

---

## Step 3 — Router: structured 409 + `?force` + `/parties/resolve`

File: `app/routers/parties.py`. Also `app/schemas.py` for the new response models.

### 3.1 Schemas (`app/schemas.py`)

```python
class PartyMatchRef(BaseModel):
    id: str
    legal_name: str
    gstin: str | None = None
    phone: str | None = None
    city: str | None = None
    last_txn_at: datetime | None = None
    status: str
    score: float | None = None

class PartyDuplicate409(BaseModel):
    code: Literal["party_exists", "party_maybe_exists"]
    message: str
    match: PartyMatchRef | None = None          # set for party_exists
    candidates: list[PartyMatchRef] = []        # set for party_maybe_exists

class PartyResolveResult(BaseModel):
    method: str | None
    confidence: float | None
    weak: bool
    candidates: list[PartyMatchRef]
```

Helper `_match_ref(session, PartyCandidate | (Party, score)) -> PartyMatchRef`.

### 3.2 `create_party` — replace lines 172–190

```python
@router.post("", response_model=PartyOut, status_code=status.HTTP_201_CREATED)
def create_party(
    body: PartyCreate,
    user: WriteUser,
    session: SessionDep,
    force: bool = Query(
        default=False,
        description="proceed past a *fuzzy* duplicate warning; ignored for "
        "exact-name / GSTIN / phone matches",
    ),
) -> PartyOut:
    syn = load_synonym_map(session, user.tenant_id)
    key = normalize_name(body.legal_name, syn)
    if not key:
        raise HTTPException(status_code=422, detail="Party name normalises to nothing")

    m = resolve_party(
        session, user.tenant_id, body.legal_name,
        gstin=body.gstin, phone=body.phone, synonyms=syn,
    )
    if m.method in ("gstin", "phone", "exact"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=PartyDuplicate409(
                code="party_exists",
                message=_reason(m),
                match=_match_ref_from_id(session, m.party_id, m.confidence),
            ).model_dump(mode="json"),
        )
    if m.candidates and not force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=PartyDuplicate409(
                code="party_maybe_exists",
                message="Similar parties already exist — pick one or confirm new",
                candidates=[_match_ref(session, c) for c in m.candidates],
            ).model_dump(mode="json"),
        )

    data = body.model_dump(exclude={"addresses"})
    party = Party(tenant_id=user.tenant_id, legal_name_normalized=key, **data)
    _apply_addresses(party, body.addresses)
    session.add(party)
    session.flush()
    return _out(session, party)
```

Notes:
- `HTTPException(detail=...)` accepts a dict — FastAPI serialises it as
  `{"detail": {...}}`. Frontend reads `err.detail.code`.
- `force` only bypasses `party_maybe_exists`. `party_exists` (hard key + exact name)
  is never bypassable via this endpoint — an operator who truly has two businesses
  with byte-identical names is a real edge case; handle it later with an explicit
  admin action, don't punch a hole in the common path.
- Keep the OpenAPI clean: add `responses={409: {"model": PartyDuplicate409}}` to the
  decorator.

### 3.3 `update_party` — the `"legal_name" in patch` branch (lines 206–219)

```python
if "legal_name" in patch:
    new_name = patch["legal_name"].strip()
    key = normalize_name(new_name, load_synonym_map(session, user.tenant_id))
    if not key:
        raise HTTPException(status_code=422, detail="Party name normalises to nothing")
    m = resolve_party(
        session, user.tenant_id, new_name,
        gstin=patch.get("gstin", party.gstin),
        phone=patch.get("phone", party.phone),
        exclude_id=party.id,
    )
    if m.method in ("gstin", "phone", "exact"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=PartyDuplicate409(
                code="party_exists", message=_reason(m),
                match=_match_ref_from_id(session, m.party_id, m.confidence),
            ).model_dump(mode="json"),
        )
    # fuzzy on *rename* — warn only if the client didn't pass ?force=true
    if m.candidates and not force:
        raise HTTPException(... code="party_maybe_exists" ...)
    patch["legal_name"] = new_name
    party.legal_name_normalized = key       # <-- keep the key in sync
```

Add the same `force: bool = Query(default=False)` param to `update_party`.
Leave the opening-balance lock logic below it untouched.

### 3.4 `POST /api/parties/resolve`

```python
@router.post("/resolve", response_model=PartyResolveResult)
def resolve_party_endpoint(
    user: CurrentUser,
    session: SessionDep,
    name: str = Query(..., min_length=1),
    gstin: str | None = Query(default=None),
    phone: str | None = Query(default=None),
) -> PartyResolveResult:
    m = resolve_party(session, user.tenant_id, name, gstin=gstin, phone=phone)
    refs: list[PartyMatchRef] = []
    if m.party_id is not None and not m.candidates:
        refs = [_match_ref_from_id(session, m.party_id, m.confidence)]
    else:
        refs = [_match_ref(session, c) for c in m.candidates[:5]]
    return PartyResolveResult(
        method=m.method, confidence=m.confidence, weak=m.weak, candidates=refs,
    )
```

Uses `CurrentUser` (read) not `WriteUser` — it's a lookup.
Register nothing new; it's on the existing `router`.

### 3.5 Backward-compat sweep

`grep -rn "already exists" api/ web/` — anything parsing the old bare-string 409
`detail` needs to handle a dict now. Known call sites:
- `web/src/pages/invoices/InvoiceEditorPage.tsx` `QuickCreatePartyDialog.onError`
  (`e.message`) — degrades gracefully (message key still present) but the UI slice
  will replace it.
- any `src/pages/parties/` create form — same.
- backend: `parties_import.py` does **not** call `create_party` (it inserts `Party`
  directly), so it's unaffected here; Step 4 covers it separately.

Because the message is still human-readable inside `detail.message`, old string
consumers that do `String(err.detail)` will show `[object Object]` — so this IS a
breaking change for any such consumer. Fix them in this slice (small) or gate the
structured body behind an `Accept`/header and default to the string (ugly — prefer
just fixing the 2-3 call sites).

---

## Step 4 — `parties_import.py` commit dedupe uses the shared key

File: `app/routers/parties_import.py`. The commit already has a solid identity
pre-pass (`by_gstin` / `by_pan` / `by_name`) — it just uses a weaker name key.

### 4.1 Replace `_norm_name`

```python
# was: def _norm_name(s): return " ".join((s or "").lower().split())
from app.domain.normalize import load_synonym_map, normalize_name

def _name_key(s: str, syn: dict[str, str]) -> str:
    return normalize_name(s or "", syn)
```

In `commit()`:
- load `syn = load_synonym_map(session, user.tenant_id)` once near the top.
- `by_name` pre-pass (lines ~386-387): key with `_name_key(ln, syn)`.
- the per-row `name_key = _norm_name(_effective_name(row))` (line ~467): use
  `_name_key(_effective_name(row), syn)`.
- when constructing the new `Party(...)` (line ~481): add
  `legal_name_normalized=_name_key(_effective_name(row), syn)`.
- the `by_name.setdefault(name_key, party.id)` for pending rows (line ~510): unchanged
  logic, new key value.

### 4.2 Also stamp the key in the staging→party path for the `link` branch?

No — the `link`/`update` branch reuses an existing `party` and only fills blanks; its
`legal_name_normalized` is already correct (set at its own creation or by the
backfill). Don't touch it.

### 4.3 `stage_masters_xml` / match rules

`app/services/tally/staging.py` computes `match_method` (`exact_gstin` / `exact_pan` /
`name_fuzzy` / …) on stage. That already surfaces near-matches in the review UI and is
**out of scope** for this slice — leave it. (A later pass could route it through
`resolve_party` for consistency, but the staging matcher has extra Tally-specific
signals — parent group, PAN — that `resolve_party` doesn't model. Note and defer.)

---

## Tests

New file `api/tests/test_party_resolution.py` + additions to
`api/tests/test_parties.py` and `api/tests/test_tally_party_import.py`.

### conftest / factory

`Party(...)` now needs `legal_name_normalized`. Add a helper:

```python
def make_party(session, tenant_id, legal_name, **kw):
    from app.domain.normalize import normalize_name
    p = Party(tenant_id=tenant_id, legal_name=legal_name,
              legal_name_normalized=normalize_name(legal_name), **kw)
    session.add(p); session.flush()
    return p
```

Put it wherever the existing test helpers live; replace raw `Party(...)` builds in
touched tests.

### `test_party_resolution.py`

| # | Case | Expect |
|---|---|---|
| 1 | `normalize_name("Steel Traders Pvt. Ltd.")` vs `"STEEL TRADERS PVT LTD"` **with suffix synonyms seeded** | equal keys |
| 2 | `normalize_name("Steel   Traders")` vs `"steel traders"` | equal keys |
| 3 | exact-key rung: existing `steel traders` → `resolve_party("Steel  Traders")` | `method="exact"`, `confidence=1.0` |
| 4 | GSTIN rung: party A has GSTIN `G1`, name `"Alpha"`; `resolve_party("Totally Different", gstin="G1")` | `method="gstin"` → A |
| 5 | phone rung: party A phone `+919812345678`; `resolve_party("X", phone="9812345678")` | `method="phone"` → A |
| 6 | `exclude_id` skips self on rename | no match |
| 7 | **[pg]** fuzzy accept: `"Steel Traders"` exists → `resolve_party("Steel Tradrs")` | `method="fuzzy"`, score ≥ 0.72 |
| 8 | **[pg]** fuzzy weak/ambiguous: two parties `"Steel Traders"` + `"Steel Traders Co"` → `resolve_party("Steel Trader")` | `party_id=None`, `weak=True`, `len(candidates)>=2` |
| 9 | **[sqlite]** no pg_trgm → non-exact/non-key query | `PartyMatch(None, None, None)`, no crash |
| 10 | empty / punctuation-only name | `PartyMatch(None,None,None)` (caller 422s) |

Mark 7/8 with the project's postgres-only marker (grep how the item fuzzy tests do
it — `@pytest.mark.skipif`/a `pg` fixture).

### `test_parties.py` additions

| # | Case | Expect |
|---|---|---|
| 11 | `POST /parties` name = exact-key variant of existing | 409, `detail.code == "party_exists"`, `detail.match.id` set |
| 12 | `POST /parties` GSTIN of existing, different name | 409 `party_exists`, `detail.message` mentions GSTIN |
| 13 | `POST /parties` phone of existing | 409 `party_exists` |
| 14 | **[pg]** `POST /parties` fuzzy-similar name, no `force` | 409 `party_maybe_exists`, `detail.candidates` non-empty |
| 15 | **[pg]** same as 14 with `?force=true` | 201, party created |
| 16 | `?force=true` does **not** bypass `party_exists` (exact-key) | still 409 |
| 17 | `POST /parties` clean new name | 201, `legal_name_normalized` persisted correctly |
| 18 | `PATCH /parties/{id}` rename onto an existing key | 409 `party_exists` |
| 19 | `PATCH` rename to a genuinely new name | 200, key updated |
| 20 | `POST /parties/resolve?name=…` exact | `method="exact"`, one candidate |
| 21 | `POST /parties/resolve` gibberish | `method=None`, `candidates=[]` |

### `test_tally_party_import.py` additions

| # | Case | Expect |
|---|---|---|
| 22 | file with two rows `"ABC Steel"` and `"ABC  Steel "` (both "new") | commit creates **1** party, second row `updated` |
| 23 | file row name-key-matches an existing DB party | links, fills blanks, no new row |
| 24 | committed new party has non-empty `legal_name_normalized` | assert |

### Gotchas from memory to respect

- `conftest.py` SQLite-FILE gotcha: run with
  `DATABASE_URL=sqlite:///./_mytest.db` for isolation; kill zombie pytest procs.
- import tests need `-p no:randomly`.
- Don't rename the canonical `nos` unit or touch `uom` — unrelated.

---

## Step 5 — Party suffix synonyms (small, conservative)

`app/seed.py` already has `seed_synonyms(session, tenant_id)` reading a
`_SEED_SYNONYMS` list (bartan set). Parties want **legal-suffix drops**. Two options:

- **Add to the same list** — simplest. `Synonym.to_token` is `NOT NULL` but `""` is
  allowed and `normalize_name` line 64 drops falsy tokens. Risk: an item legitimately
  named "… Ltd" (rare in metal trade) loses the token too — acceptable.
- **Separate `_SEED_PARTY_SUFFIXES` + a `seed_party_synonyms()`** — cleaner intent,
  one more function to keep in lockstep.

**Recommendation:** add to the shared list, keep it to these rows, nothing more:

```
pvt      -> ""
private  -> ""
ltd      -> ""
limited  -> ""
llp      -> ""
company  -> ""
"and"    -> "&"
```

Do **not** seed `ss -> steel` / `still -> steel` for parties in this slice — parties
lean on the trgm rung + the (future) type-ahead. Revisit only if a real Tally export
shows operators actually writing "SS Traders" for "Steel Traders". Flag as a tunable
in `docs/FEATURE-party-dedupe.md`.

Wire: `register` / the tenant-provision path already calls `seed_synonyms`; existing
tenants get the new rows via a one-liner in `tools/backfill_party_namekey.py`
(call `seed_synonyms(session, t.id)` per tenant before computing keys) so the backfill
uses them.

---

## Build order within the slice

1. Step 1.1 + 1.2 (model + migration) — `alembic upgrade head` clean on a scratch DB,
   both dialects.
2. Step 5 seed rows + Step 1.3 backfill script — run dry-run against a prod dump if
   available; eyeball the collision report.
3. Step 2 `party_resolution.py` + `test_party_resolution.py` (1–10) — pure logic,
   fast feedback.
4. Step 3 schemas + `create_party` / `update_party` / `/resolve` + tests 11–21.
5. Step 4 import path + tests 22–24.
6. Full suite (`pytest` with the isolated DB URL), then `-p no:randomly` for the
   import file.
7. Update `docs/FEATURE-party-dedupe.md` (new doc) + the infra runbook with the
   post-deploy backfill command. Add a memory entry.

## Rollout

- **Backend + UI ship in one PR** (decided) — so the fuzzy `party_maybe_exists` 409
  is raised from day one; the UI's "create new anyway" button sends `?force=true` in
  the same release. No feature flag, no warn-only phase.
- Deploy is the usual push-webhook; `alembic upgrade head` runs automatically.
- **Manual, once per env, right after deploy:**
  `docker compose exec -T metalerp-api python -m tools.backfill_party_namekey --apply`
  Until it runs, `legal_name_normalized` holds the crude migration key — exact-match
  dedupe works, fuzzy candidate quality is slightly lower. Safe to defer minutes, not
  to skip.
- The 409 gate is strictly better than the current `lower(legal_name)` check; the only
  new operator-visible behaviour is the "similar parties — pick one or create anyway"
  prompt, which the UI slice in the same PR handles.

---

## Out of scope (tracked for later)

- Party **alias** table (so a merged-away name still resolves) — needed by the merge
  tool, not by this slice.
- Party **merge** tool + duplicate-review screen (consumes the backfill collision
  report + `merged_into_id` on `party`).
- LLM disambiguation on the `weak` branch (mirror item X3).
- Routing the Tally staging matcher through `resolve_party`.
- The UI slice: `PartyPicker` debounce + richer rows + supplier-role surfacing;
  `QuickCreatePartyDialog` live near-match panel + structured-409 handling; shared
  `<SimilarRecords>` component; item-side create-form parity.
- DB unique constraint on `(tenant_id, legal_name_normalized)` — only after the
  backlog is merged, and even then probably not (app-level 409 is friendlier).
