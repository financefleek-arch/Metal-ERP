"""Bulk party import from a Tally Prime masters XML.

Flow: upload -> parse into staging_tally_party -> review (match rules run
on read) -> per-row adjust -> commit the ready rows into party /
party_address. Completes the Parties CRUD slice.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.deps import SessionDep, WriteUser
from app.models import Party, PartyAddress, StagingTallyParty
from app.models._mixins import PartyRole, PartySource, PartyStatus
from app.reference import state_code_from_name, validate_gstin, validate_pan
from tools.tally_import.groups import GroupTree
from tools.tally_import.match import match_ledgers_bulk
from tools.tally_import.parser import TallyLedger, parse_masters

router = APIRouter(prefix="/api/parties/import", tags=["parties-import"])

# A full "All Masters" TallyPrime export (every ledger with its address /
# GST detail, often stock items too) runs well past 10 MB. Only LEDGER nodes
# are read, so the parse stays fast; the cap just needs headroom. Mirrors the
# items importer's 64 MB ceiling.
_MAX_BYTES = 64 * 1024 * 1024
_ALWAYS_GROUPS = {"sundry debtors", "sundry creditors"}
_EXACT = {"exact_gstin", "exact_pan"}

Outcome = Literal["new", "link", "flag", "skip"]


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------


class ImportGroup(BaseModel):
    name: str
    ledger_count: int
    always: bool
    implied_role: PartyRole | None


class ImportBatchOut(BaseModel):
    batch_id: str
    total: int
    groups: list[ImportGroup]


class StagedRowOut(BaseModel):
    id: str
    ledger_name: str
    parent_group: str | None
    gstin: str | None
    pan: str | None
    outcome: Outcome
    proposed_role: PartyRole
    role: PartyRole
    match_method: str
    match_party_id: str | None
    match_party_name: str | None
    decision: str
    edited_name: str | None
    flags: list[dict]
    missing: list[str]


class ReviewOut(BaseModel):
    batch_id: str
    counts: dict[str, int]
    rows: list[StagedRowOut]


class RowPatch(BaseModel):
    role_override: PartyRole | None = None
    decision: Literal["pending", "create", "link", "skip"] | None = None
    link_party_id: str | None = None
    edited_name: str | None = Field(default=None, max_length=200)


class CommitOut(BaseModel):
    created: int
    updated: int
    skipped: int
    still_flagged: int


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _effective_role(row: StagingTallyParty) -> PartyRole:
    return PartyRole(row.role_override) if row.role_override else row.proposed_role


def _effective_name(row: StagingTallyParty) -> str:
    return (row.edited_name or row.ledger_name).strip()


def _outcome(row: StagingTallyParty) -> Outcome:
    if row.decision == "skip":
        return "skip"
    blocking = [f for f in (row.flags_json or []) if f.get("code") != "name_near_match"]
    if row.decision == "pending" and (blocking or row.match_method == "name_fuzzy"):
        return "flag"
    pending_exact = row.decision == "pending" and row.match_method in _EXACT
    if row.decision == "link" or pending_exact:
        return "link"
    return "new"


def _missing(row: StagingTallyParty) -> list[str]:
    m: list[str] = []
    if not row.address_lines_json:
        m.append("address")
    if not (row.gstin or row.pan):
        m.append("gstin/pan")
    if not row.state_name:
        m.append("state")
    return m


def _fill_blank(party: Party, attr: str, value: str | None) -> None:
    if value and not getattr(party, attr):
        setattr(party, attr, value)


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------


@router.post("", response_model=ImportBatchOut, status_code=status.HTTP_201_CREATED)
async def upload(
    user: WriteUser,
    session: SessionDep,
    file: UploadFile = File(...),
) -> ImportBatchOut:
    raw = await file.read()
    if len(raw) > _MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is larger than {_MAX_BYTES // (1024 * 1024)} MB",
        )
    try:
        masters = parse_masters(raw)
    except Exception as e:  # noqa: BLE001 - surface any XML problem as a 422
        raise HTTPException(status_code=422, detail=f"Could not parse the Tally XML: {e}") from e

    if not masters.ledgers:
        raise HTTPException(status_code=422, detail="No ledgers found in the file")

    # One in-flight import per tenant: clear any earlier batch that was never
    # committed (an abandoned upload, or one that failed mid-commit). Committed
    # rows are kept as an audit trail.
    session.execute(
        delete(StagingTallyParty).where(
            StagingTallyParty.tenant_id == user.tenant_id,
            StagingTallyParty.committed_as.is_(None),
        )
    )

    tree = GroupTree(masters.groups)
    batch_id = str(uuid.uuid4())

    # Group summary for the scope picker.
    grp_counts: dict[str, int] = {}
    grp_role: dict[str, PartyRole | None] = {}
    for led in masters.ledgers:
        top = tree.top_group(led.parent) or (led.parent or "").strip().lower() or "(ungrouped)"
        grp_counts[top] = grp_counts.get(top, 0) + 1
        grp_role.setdefault(top, tree.role_for(led.parent))

    # Stage every ledger whose lineage resolves to a role (Debtors/Creditors).
    # Others are parsed into the group list but not staged unless re-uploaded
    # with an explicit scope (kept simple for this slice: role-bearing only).
    gstins_in_file: dict[str, int] = {}
    for led in masters.ledgers:
        if led.gstin:
            try:
                g = validate_gstin(led.gstin)
                if g:
                    gstins_in_file[g] = gstins_in_file.get(g, 0) + 1
            except ValueError:
                pass

    # Resolve role + dual-lineage per ledger, then match the whole file in one
    # pass (a single party prefetch instead of up to three queries per ledger).
    to_stage: list[tuple[TallyLedger, PartyRole, bool]] = []
    for led in masters.ledgers:
        role = tree.role_for(led.parent)
        if role is None:
            continue
        anc = tree.roots_of(led.parent)
        dual = bool(anc & {"sundry debtors"}) and bool(anc & {"sundry creditors"})
        to_stage.append((led, role, dual))

    matches = match_ledgers_bulk(
        session,
        user.tenant_id,
        to_stage,
        gstins_in_file=gstins_in_file,
    )

    # A LEDGER node is mostly unused address / GST boilerplate; nothing reads
    # raw_xml back, so keep only a debugging prefix rather than staging tens of
    # MB for a full "All Masters" import. Mirrors the items importer.
    _RAW_XML_KEEP = 2000

    staged = 0
    for (led, _role, _dual), mr in zip(to_stage, matches, strict=True):
        session.add(
            StagingTallyParty(
                tenant_id=user.tenant_id,
                batch_id=batch_id,
                tally_guid=led.guid,
                ledger_name=led.name,
                parent_group=led.parent,
                gstin=led.gstin,
                pan=led.pan,
                state_name=led.state_name,
                phone=led.phone,
                email=led.email,
                address_lines_json=led.address_lines or None,
                pincode=led.pincode,
                raw_xml=(led.raw_xml or "")[:_RAW_XML_KEEP] or None,
                proposed_role=mr.proposed_role,
                match_method=mr.method,
                match_party_id=mr.party_id,
                flags_json=mr.flags or None,
            )
        )
        staged += 1

    session.flush()

    groups = [
        ImportGroup(
            name=name,
            ledger_count=cnt,
            always=name in _ALWAYS_GROUPS,
            implied_role=grp_role.get(name),
        )
        for name, cnt in sorted(grp_counts.items(), key=lambda kv: -kv[1])
    ]
    return ImportBatchOut(batch_id=batch_id, total=staged, groups=groups)


def _rows(session: SessionDep, tenant_id: str, batch_id: str) -> list[StagingTallyParty]:
    rows = list(
        session.scalars(
            select(StagingTallyParty)
            .where(
                StagingTallyParty.tenant_id == tenant_id,
                StagingTallyParty.batch_id == batch_id,
            )
            .order_by(StagingTallyParty.ledger_name)
        ).all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Import batch not found")
    return rows


@router.get("/{batch_id}", response_model=ReviewOut)
def review(batch_id: str, user: WriteUser, session: SessionDep) -> ReviewOut:
    rows = _rows(session, user.tenant_id, batch_id)
    names = {
        p.id: p.legal_name
        for p in session.scalars(
            select(Party).where(
                Party.id.in_(
                    {r.match_party_id for r in rows if r.match_party_id}
                    | {r.link_party_id for r in rows if r.link_party_id}
                )
            )
        ).all()
    }

    out_rows: list[StagedRowOut] = []
    counts = {"new": 0, "link": 0, "flag": 0, "skip": 0}
    for r in rows:
        oc = _outcome(r)
        counts[oc] += 1
        pid = r.link_party_id or r.match_party_id
        out_rows.append(
            StagedRowOut(
                id=r.id,
                ledger_name=r.ledger_name,
                parent_group=r.parent_group,
                gstin=r.gstin,
                pan=r.pan,
                outcome=oc,
                proposed_role=r.proposed_role,
                role=_effective_role(r),
                match_method=r.match_method,
                match_party_id=pid,
                match_party_name=names.get(pid) if pid else None,
                decision=r.decision,
                edited_name=r.edited_name,
                flags=r.flags_json or [],
                missing=_missing(r),
            )
        )
    return ReviewOut(batch_id=batch_id, counts=counts, rows=out_rows)


@router.patch("/{batch_id}/rows/{row_id}", response_model=StagedRowOut)
def patch_row(
    batch_id: str,
    row_id: str,
    body: RowPatch,
    user: WriteUser,
    session: SessionDep,
) -> StagedRowOut:
    row = session.scalar(
        select(StagingTallyParty).where(
            StagingTallyParty.id == row_id,
            StagingTallyParty.batch_id == batch_id,
            StagingTallyParty.tenant_id == user.tenant_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Row not found")

    data = body.model_dump(exclude_unset=True)
    if "role_override" in data:
        row.role_override = data["role_override"].value if data["role_override"] else None
    if "decision" in data:
        row.decision = data["decision"]
    if "link_party_id" in data:
        row.link_party_id = data["link_party_id"]
        if data["link_party_id"]:
            row.decision = "link"
    if "edited_name" in data:
        row.edited_name = (data["edited_name"] or "").strip() or None
        # An edited name clears the name-shape flags.
        _name_flags = {"name_too_long", "name_bad_chars"}
        row.flags_json = [
            f for f in (row.flags_json or []) if f.get("code") not in _name_flags
        ] or None
    session.flush()

    pid = row.link_party_id or row.match_party_id
    name = None
    if pid:
        p = session.get(Party, pid)
        name = p.legal_name if p else None
    return StagedRowOut(
        id=row.id,
        ledger_name=row.ledger_name,
        parent_group=row.parent_group,
        gstin=row.gstin,
        pan=row.pan,
        outcome=_outcome(row),
        proposed_role=row.proposed_role,
        role=_effective_role(row),
        match_method=row.match_method,
        match_party_id=pid,
        match_party_name=name,
        decision=row.decision,
        edited_name=row.edited_name,
        flags=row.flags_json or [],
        missing=_missing(row),
    )


def _norm_name(s: str) -> str:
    return " ".join((s or "").lower().split())


@router.post("/{batch_id}/commit", response_model=CommitOut)
def commit(batch_id: str, user: WriteUser, session: SessionDep) -> CommitOut:
    rows = _rows(session, user.tenant_id, batch_id)
    created = updated = skipped = still_flagged = 0

    # --- identity pre-pass: one query for the tenant's existing parties, so a
    # created row that clashes with one already in the DB (or with an earlier
    # row in the same file) links + fills blanks instead of inserting a dup.
    # Mirrors the item-import commit batching. ---
    by_gstin: dict[str, str] = {}
    by_pan: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for pid, g, p, ln, st in session.execute(
        select(Party.id, Party.gstin, Party.pan, Party.legal_name, Party.status).where(
            Party.tenant_id == user.tenant_id
        )
    ).all():
        if st == PartyStatus.archived:
            continue
        if g:
            by_gstin.setdefault(g.strip().upper(), pid)
        if p:
            by_pan.setdefault(p.strip().upper(), pid)
        if ln:
            by_name.setdefault(_norm_name(ln), pid)

    new_parties: list[Party] = []
    # New parties created in this same commit are not yet flushed, so they have
    # no persistent identity and `session.get` won't find them — keep our own
    # id -> object map so a later same-name/GSTIN row can link + fill.
    pending_by_id: dict[str, Party] = {}

    def _resolve(pid: str) -> Party | None:
        obj = pending_by_id.get(pid)
        if obj is not None:
            return obj
        obj = session.get(Party, pid)
        return obj if obj is not None and obj.tenant_id == user.tenant_id else None

    def _fill_from_row(
        party: Party,
        row: StagingTallyParty,
        role: PartyRole,
        state_code: str | None,
        gstin: str | None,
        pan: str | None,
    ) -> None:
        _fill_blank(party, "gstin", gstin)
        _fill_blank(party, "pan", pan)
        _fill_blank(party, "phone", (row.phone or "").strip() or None)
        _fill_blank(party, "email", (row.email or "").strip() or None)
        _fill_blank(party, "default_state_code", state_code)
        if not party.tally_guid:
            party.tally_guid = row.tally_guid
        # Widen role, never narrow: any mismatch resolves to 'both'.
        if party.role != role:
            party.role = PartyRole.both
        if not party.addresses and row.address_lines_json:
            party.addresses.append(_address_from_row(row, state_code))

    for row in rows:
        if row.committed_as:
            continue
        oc = _outcome(row)
        if oc == "skip":
            skipped += 1
            continue
        if oc == "flag":
            still_flagged += 1
            continue

        role = _effective_role(row)
        state_code = state_code_from_name(row.state_name) or (
            row.gstin[:2] if row.gstin and row.gstin[:2].isdigit() else None
        )
        gstin = None
        if row.gstin:
            try:
                gstin = validate_gstin(row.gstin)
            except ValueError:
                gstin = None
        pan = None
        if row.pan:
            try:
                pan = validate_pan(row.pan)
            except ValueError:
                pan = None

        target_id = row.link_party_id or (
            row.match_party_id if row.match_method in {"exact_gstin", "exact_pan"} else None
        )

        if oc == "link" and target_id:
            party = session.get(Party, target_id)
            if party is None or party.tenant_id != user.tenant_id:
                still_flagged += 1
                continue
            _fill_from_row(party, row, role, state_code, gstin, pan)
            row.committed_as = party.id
            updated += 1
            continue

        # A "new" row — but a party with this GSTIN / PAN / name may already
        # exist (in the DB or created earlier in this same file). Link to it.
        name_key = _norm_name(_effective_name(row))
        clash_id = (
            (gstin and by_gstin.get(gstin.strip().upper()))
            or (not gstin and pan and by_pan.get(pan.strip().upper()))
            or (name_key and by_name.get(name_key))
        )
        if clash_id:
            party = _resolve(clash_id)
            if party is not None:
                _fill_from_row(party, row, role, state_code, gstin, pan)
                row.committed_as = party.id
                updated += 1
                continue

        party = Party(
            id=str(uuid.uuid4()),
            tenant_id=user.tenant_id,
            legal_name=_effective_name(row),
            role=role,
            gstin=gstin,
            pan=pan,
            phone=(row.phone or "").strip() or None,
            email=(row.email or "").strip() or None,
            default_state_code=state_code,
            status=PartyStatus.active,
            source=PartySource.tally_import,
            source_ref=row.tally_guid,
            tally_guid=row.tally_guid,
        )
        if row.address_lines_json:
            party.addresses.append(_address_from_row(row, state_code))
        # id set explicitly (column default only fires at flush), so committed_as
        # and the dedup maps can use it before the single add_all below.
        new_parties.append(party)
        pending_by_id[party.id] = party
        row.committed_as = party.id
        if gstin:
            by_gstin.setdefault(gstin.strip().upper(), party.id)
        if not gstin and pan:
            by_pan.setdefault(pan.strip().upper(), party.id)
        if name_key:
            by_name.setdefault(name_key, party.id)
        created += 1

    if new_parties:
        session.add_all(new_parties)
        session.flush()

    return CommitOut(
        created=created, updated=updated, skipped=skipped, still_flagged=still_flagged
    )


@router.delete("/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
def discard(batch_id: str, user: WriteUser, session: SessionDep) -> None:
    _rows(session, user.tenant_id, batch_id)  # 404 if the batch doesn't exist
    session.execute(
        delete(StagingTallyParty).where(
            StagingTallyParty.tenant_id == user.tenant_id,
            StagingTallyParty.batch_id == batch_id,
        )
    )


def _address_from_row(row: StagingTallyParty, state_code: str | None) -> PartyAddress:
    lines = list(row.address_lines_json or [])
    return PartyAddress(
        type="both",
        line1=lines[0][:120] if lines else None,
        line2=lines[1][:120] if len(lines) > 1 else None,
        line3=lines[2][:120] if len(lines) > 2 else None,
        city=(lines[-1][:60] if len(lines) > 3 else None),
        state_code=state_code,
        pincode=(row.pincode or "").strip()[:6] or None,
        is_default=True,
    )
