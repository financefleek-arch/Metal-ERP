"""Push promoted catalog items to Tally as stock items.

The rules come from the live probes in the execution plan (section 3):

  - `ACTION="Create"` on an existing master *alters* it. So we only ever create a group or an
    item that the last "check Tally" showed to be missing, and an item whose name is already in
    Tally is a blocker, never overwritten. The check must be recent (`STALE_AFTER`).
  - Items go in as `NAME`, `PARENT` (stock group), `BASEUNITS`, `PARTNO` (our code). Never an
    alias: aliases fail with a bare "Duplicate Entry!" and share one namespace with codes.
  - One bad item poisons its whole import: Tally keeps the rejects as exception masters that
    block the same name and part number on retry, and reports one error for all of them. So a
    push goes in small batches, one `tally_sync_job` each, strictly one after the other, and the
    first batch with an exception stops the run before anything else is sent.
  - XML is built with lxml (an unescaped quote makes Tally reject the whole request).

Group mapping: our group (item category) goes to the Tally stock group of the same name (found
case-insensitively); `stock_group_map` overrides that per group. A group Tally lacks is created
under `stock_group_root` (Primary if unset) when the policy is `create_missing`, else it blocks.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from lxml import etree
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import (
    AgentOutboxItem,
    BackupShop,
    CatalogProduct,
    Item,
    ItemCategory,
    SupplierCatalog,
    SupplierCatalogItem,
    TallyCompany,
    TallyLink,
    TallySyncJob,
)
from app.services.tally.agent_health import agent_online, assert_tally_reachable
from app.services.tally.jobs import complete_job_error
from tools.tally_import.parser import parse_stock_items

log = logging.getLogger("tally.items")

BATCH_SIZE = 50
STALE_AFTER = timedelta(minutes=30)
NAME_MAX = 99  # Tally's own limit on a master name
KIND = "push_items"
ENTITY = "supplier_catalog"
PRIMARY = "Primary"
POLICIES = ("create_missing", "existing_only")
_NON_TERMINAL = ("queued", "sent", "running")


# --------------------------------------------------------------------------- data


@dataclass
class Check:
    code: str
    ok: bool
    message: str
    blocking: bool = True


@dataclass
class GroupPlan:
    category_id: str | None
    our_name: str
    tally_name: str
    status: str  # existing | create | missing
    item_count: int


@dataclass
class Collision:
    item_id: str
    code: str
    name: str


@dataclass
class Preflight:
    checks: list[Check] = field(default_factory=list)
    groups: list[GroupPlan] = field(default_factory=list)
    collisions: list[Collision] = field(default_factory=list)
    total: int = 0
    to_push: int = 0
    already_synced: int = 0
    not_promoted: int = 0
    batches: int = 0
    root: str | None = None
    checked_at: datetime | None = None

    @property
    def blockers(self) -> list[Check]:
        return [c for c in self.checks if c.blocking and not c.ok]

    @property
    def ok(self) -> bool:
        return not self.blockers and self.to_push > 0


# --------------------------------------------------------------------------- settings


def get_company(session: Session, tenant_id: str) -> TallyCompany | None:
    return session.scalar(select(TallyCompany).where(TallyCompany.tenant_id == tenant_id))


def _norm(name: str) -> str:
    return " ".join((name or "").lower().split())


def known_groups(company: TallyCompany) -> dict[str, dict[str, str | None]]:
    """lower-case name -> {name, parent} of the groups seen at the last check."""
    return {
        _norm(g["name"]): g for g in (company.known_stock_groups or []) if g.get("name")
    }


def check_is_fresh(company: TallyCompany, now: datetime | None = None) -> bool:
    at = company.known_stock_at
    if at is None:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - at <= STALE_AFTER


# --------------------------------------------------------------------------- preflight


@dataclass
class _Row:
    row: SupplierCatalogItem
    item: Item | None
    product: CatalogProduct | None


def load_rows(session: Session, rows: Sequence[SupplierCatalogItem]) -> list[_Row]:
    item_ids = [r.item_id for r in rows if r.item_id]
    prod_ids = [r.product_id for r in rows if r.product_id]
    items: dict[str, Item] = {}
    prods: dict[str, CatalogProduct] = {}
    for i in range(0, len(item_ids), 500):
        for it in session.scalars(select(Item).where(Item.id.in_(item_ids[i : i + 500]))):
            items[it.id] = it
    for i in range(0, len(prod_ids), 500):
        for p in session.scalars(
            select(CatalogProduct).where(CatalogProduct.id.in_(prod_ids[i : i + 500]))
        ):
            prods[p.id] = p
    return [
        _Row(r, items.get(r.item_id or ""), prods.get(r.product_id or "")) for r in rows
    ]


def _category_names(session: Session, tenant_id: str) -> dict[str, str]:
    return dict(
        session.execute(
            select(ItemCategory.id, ItemCategory.name).where(ItemCategory.tenant_id == tenant_id)
        ).all()
    )


def plan_groups(
    company: TallyCompany,
    cats: dict[str, str],
    rows: Sequence[_Row],
) -> tuple[list[GroupPlan], list[tuple[str, str | None]]]:
    """(per-group plan, the group creates to send: [(name, parent or None)] parents first)."""
    seen = known_groups(company)
    root = (company.stock_group_root or "").strip() or None
    mapping: dict[str, str] = company.stock_group_map or {}
    counts: dict[str | None, int] = {}
    for r in rows:
        counts[r.row.category_id] = counts.get(r.row.category_id, 0) + 1

    plans: list[GroupPlan] = []
    creates: list[tuple[str, str | None]] = []
    created_names: set[str] = set()

    def _create(name: str, parent: str | None) -> None:
        if _norm(name) not in created_names:
            created_names.add(_norm(name))
            creates.append((name, parent))

    allow_new = company.tally_group_create_policy == "create_missing"
    root_ok = root is None or _norm(root) in seen or allow_new
    if root and _norm(root) not in seen and allow_new:
        _create(root, None)

    for cid, n in counts.items():
        if cid is None:
            # ungrouped: straight under the root (or Primary)
            if root is None:
                plans.append(GroupPlan(None, "No group", PRIMARY, "existing", n))
            elif _norm(root) in seen:
                canonical = str(seen[_norm(root)]["name"])
                plans.append(GroupPlan(None, "No group", canonical, "existing", n))
            else:
                status = "create" if allow_new else "missing"
                plans.append(GroupPlan(None, "No group", root, status, n))
            continue
        ours = cats.get(cid, "")
        wanted = (mapping.get(cid) or ours).strip()
        found = seen.get(_norm(wanted))
        if found is not None:
            plans.append(GroupPlan(cid, ours, str(found["name"]), "existing", n))
        elif allow_new and root_ok:
            plans.append(GroupPlan(cid, ours, wanted, "create", n))
            _create(wanted, root)
        else:
            plans.append(GroupPlan(cid, ours, wanted, "missing", n))
    plans.sort(key=lambda g: (g.category_id is None, g.our_name.lower()))
    return plans, creates


def preflight(
    session: Session,
    tenant_id: str,
    catalog: SupplierCatalog,
    rows: Sequence[SupplierCatalogItem],
    *,
    include_synced: bool = False,
    now: datetime | None = None,
) -> tuple[Preflight, list[_Row], list[tuple[str, str | None]]]:
    """Everything that could go wrong, found before anything is sent."""
    pf = Preflight(total=len(rows))
    loaded = load_rows(session, rows)
    company = get_company(session, tenant_id)

    # --- company + agent
    if company is None or company.shop_id is None:
        pf.checks.append(
            Check("no_company", False, "Tally is not connected for this firm yet. Ask Fleek to "
                  "link your Tally company and install the agent.")
        )
        pf.checks.append(Check("no_rows", bool(rows), "Select at least one item.", blocking=False))
        return pf, loaded, []
    pf.checks.append(Check("no_company", True, f"Tally company: {company.company_name}"))
    pf.root = (company.stock_group_root or "").strip() or None
    pf.checked_at = company.known_stock_at

    shop = session.get(BackupShop, company.shop_id)
    try:
        if shop is None:
            raise HTTPException(status_code=409, detail="The Tally agent is not installed.")
        assert_tally_reachable(session, company)
        online = agent_online(shop)
        pf.checks.append(
            Check(
                "agent",
                online,
                "The Tally agent is online and Tally is reachable."
                if online
                else "The shop's agent has not checked in recently.",
            )
        )
    except HTTPException as exc:
        pf.checks.append(Check("agent", False, str(exc.detail)))

    # --- freshness of what we know about Tally
    fresh = check_is_fresh(company, now)
    pf.checks.append(
        Check(
            "check_needed",
            fresh,
            "Tally was checked a moment ago."
            if fresh
            else "Check Tally first: we only create what is missing, so we need to see what "
            "Tally has right now.",
        )
    )

    # --- what is selected
    not_promoted = [r for r in loaded if r.item is None]
    pf.not_promoted = len(not_promoted)
    pf.checks.append(
        Check(
            "not_promoted",
            not not_promoted,
            "All selected products are in your item list."
            if not not_promoted
            else f"{len(not_promoted)} selected products are not in your item list yet. "
            "Add them to your items first.",
        )
    )
    ready = [r for r in loaded if r.item is not None]
    pf.already_synced = sum(1 for r in ready if r.row.tally_status == "synced")
    todo = [r for r in ready if include_synced or r.row.tally_status != "synced"]
    pf.to_push = len(todo)
    pf.batches = -(-len(todo) // BATCH_SIZE) if todo else 0
    if todo:
        pf.checks.append(Check("nothing_to_push", True, f"{len(todo)} items will be sent."))
    else:
        pf.checks.append(
            Check(
                "nothing_to_push",
                False,
                "Everything selected is already in Tally."
                if pf.already_synced
                else "There is nothing to send.",
            )
        )

    # --- names
    too_long = [r for r in todo if r.item and len(r.item.name) > NAME_MAX]
    pf.checks.append(
        Check(
            "name_too_long",
            not too_long,
            "All item names fit Tally's limit."
            if not too_long
            else f"{len(too_long)} item names are over {NAME_MAX} characters (for example "
            f"“{too_long[0].item.name[:40]}…”). Shorten them in your item list.",
        )
    )
    known_items = {n for n in (company.known_stock_items or [])}
    collisions = [
        Collision(r.item.id, r.product.code if r.product else "", r.item.name)
        for r in todo
        if r.item and r.row.tally_status != "synced" and _norm(r.item.name) in known_items
    ]
    pf.collisions = collisions
    pf.checks.append(
        Check(
            "name_collision",
            not collisions,
            "No item name already exists in Tally."
            if not collisions
            else f"{len(collisions)} items already exist in Tally under the same name (for "
            f"example “{collisions[0].name}”). They would be overwritten. Rename them, or "
            "leave them out.",
        )
    )

    # --- groups
    plans, creates = plan_groups(company, _category_names(session, tenant_id), todo)
    pf.groups = plans
    missing = [g for g in plans if g.status == "missing"]
    pf.checks.append(
        Check(
            "group_missing",
            not missing,
            "Every group has a place in Tally."
            if not missing
            else "These groups are not in Tally and new groups are not allowed: "
            + ", ".join(g.tally_name for g in missing[:5])
            + ". Map them to an existing Tally group, or allow new groups.",
        )
    )

    # --- another push already running for this catalog
    running = run_in_flight(session, tenant_id, catalog.id)
    pf.checks.append(
        Check(
            "in_flight",
            not running,
            "No other push is running." if not running else "A push to Tally is already running.",
        )
    )
    return pf, todo, creates


def run_in_flight(session: Session, tenant_id: str, catalog_id: str) -> bool:
    return (
        session.scalar(
            select(TallySyncJob.id).where(
                TallySyncJob.tenant_id == tenant_id,
                TallySyncJob.kind == KIND,
                TallySyncJob.entity_type == ENTITY,
                TallySyncJob.entity_id == catalog_id,
                TallySyncJob.status.in_(_NON_TERMINAL),
            )
        )
        is not None
    )


# --------------------------------------------------------------------------- the XML


def _sub(parent: etree._Element, tag: str, text: str | None = None) -> etree._Element:
    el = etree.SubElement(parent, tag)
    if text is not None:
        el.text = text
    return el


def build_envelope(
    company_name: str,
    *,
    unit: str = "Nos",
    groups: Sequence[tuple[str, str | None]] = (),
    items: Sequence[tuple[str, str, str]] = (),
) -> bytes:
    """Import-Data envelope: one UNIT, STOCKGROUPs (parents first), then the STOCKITEMs.

    `items` are (name, stock group, part number). Returns UTF-8 bytes.
    """
    env = etree.Element("ENVELOPE", nsmap={"UDF": "TallyUDF"})
    header = _sub(env, "HEADER")
    _sub(header, "TALLYREQUEST", "Import Data")
    body = _sub(env, "BODY")
    importdata = _sub(body, "IMPORTDATA")
    reqdesc = _sub(importdata, "REQUESTDESC")
    _sub(reqdesc, "REPORTNAME", "All Masters")
    static = _sub(reqdesc, "STATICVARIABLES")
    _sub(static, "SVCURRENTCOMPANY", company_name)
    reqdata = _sub(importdata, "REQUESTDATA")

    def message() -> etree._Element:
        return _sub(reqdata, "TALLYMESSAGE")

    if items:
        u = etree.SubElement(message(), "UNIT", NAME=unit, ACTION="Create")
        _sub(u, "NAME", unit)
        _sub(u, "ISSIMPLEUNIT", "Yes")
        _sub(u, "DECIMALPLACES", "0")
    for name, parent in groups:
        g = etree.SubElement(message(), "STOCKGROUP", NAME=name, ACTION="Create")
        _sub(g, "NAME", name)
        _sub(g, "PARENT", parent or "")
    for name, group, part_no in items:
        si = etree.SubElement(message(), "STOCKITEM", NAME=name, ACTION="Create")
        _sub(si, "NAME", name)
        _sub(si, "PARENT", group)
        _sub(si, "BASEUNITS", unit)
        _sub(si, "PARTNO", part_no)
    return etree.tostring(env, encoding="UTF-8", xml_declaration=False)


def _item_tuples(
    rows: Sequence[_Row], groups: Sequence[GroupPlan], root: str | None
) -> list[tuple[str, str, str]]:
    by_cat = {g.category_id: g.tally_name for g in groups}
    out: list[tuple[str, str, str]] = []
    for r in rows:
        assert r.item is not None
        group = by_cat.get(r.row.category_id) or root or PRIMARY
        code = (r.product.code if r.product else None) or r.item.barcode or ""
        out.append((r.item.name, group, code))
    return out


# --------------------------------------------------------------------------- the run


def start_run(
    session: Session,
    company: TallyCompany,
    catalog: SupplierCatalog,
    todo: Sequence[_Row],
    creates: Sequence[tuple[str, str | None]],
    groups: Sequence[GroupPlan],
) -> TallySyncJob:
    """Queue the first batch; the rest follow one by one as each batch reports back."""
    ids = [r.row.id for r in todo]
    run_id = str(uuid.uuid4())
    batches = [ids[i : i + BATCH_SIZE] for i in range(0, len(ids), BATCH_SIZE)]
    session.execute(
        update(SupplierCatalogItem)
        .where(SupplierCatalogItem.id.in_(ids))
        .values(tally_status="queued")
    )
    state = {
        "run": run_id,
        "batches": len(batches),
        "total_items": len(ids),
        "group_creates": [list(c) for c in creates],
        "groups": [
            {"category_id": g.category_id, "tally_name": g.tally_name} for g in groups
        ],
        "root": company.stock_group_root,
    }
    return _enqueue_batch(session, company, catalog, state, 1, ids[:BATCH_SIZE], ids[BATCH_SIZE:])


def _enqueue_batch(
    session: Session,
    company: TallyCompany,
    catalog: SupplierCatalog,
    state: dict,
    batch_no: int,
    batch_ids: list[str],
    rest: list[str],
) -> TallySyncJob:
    rows = list(
        session.scalars(select(SupplierCatalogItem).where(SupplierCatalogItem.id.in_(batch_ids)))
    )
    order = {rid: i for i, rid in enumerate(batch_ids)}
    rows.sort(key=lambda r: order[r.id])
    loaded = load_rows(session, rows)
    groups = [
        GroupPlan(g["category_id"], "", g["tally_name"], "existing", 0) for g in state["groups"]
    ]
    items = _item_tuples(loaded, groups, state.get("root") and str(state["root"]) or None)
    creates = [(c[0], c[1]) for c in state["group_creates"]] if batch_no == 1 else []
    xml = build_envelope(company.company_name, groups=creates, items=items)

    job = TallySyncJob(
        tenant_id=company.tenant_id,
        company_id=company.id,
        direction="out",
        kind=KIND,
        entity_type=ENTITY,
        entity_id=catalog.id,
        status="queued",
        counts={
            **{k: state[k] for k in ("run", "batches", "total_items")},
            "batch": batch_no,
            "item_ids": batch_ids,
            "rest": rest,
            "expected": len(items),
            "state": state,
        },
    )
    session.add(job)
    session.flush()
    outbox = AgentOutboxItem(
        shop_id=company.shop_id,
        module="tally",
        payload={
            "job_id": job.id,
            "action": KIND,
            "company_name": company.company_name,
            "voucher_xml": xml.decode("utf-8"),
        },
        status="queued",
    )
    session.add(outbox)
    session.flush()
    job.outbox_item_id = outbox.id
    session.flush()
    return job


def _tag_int(root: etree._Element, tag: str) -> int:
    el = root.find(f".//{tag}")
    try:
        return int((el.text or "0").strip()) if el is not None else 0
    except ValueError:
        return 0


def _set_rows(session: Session, ids: Sequence[str], value: str) -> None:
    for i in range(0, len(ids), 500):
        session.execute(
            update(SupplierCatalogItem)
            .where(SupplierCatalogItem.id.in_(list(ids[i : i + 500])))
            .values(tally_status=value)
        )


def _finish(job: TallySyncJob, status: str, counts: dict, error: str | None = None) -> None:
    job.status = status
    job.counts = counts
    job.error = error[:2000] if error else None
    job.completed_at = datetime.now(UTC)


def process_items_result(
    session: Session,
    job: TallySyncJob,
    *,
    ok: bool,
    tally_response: str | None,
    agent_error: str | None,
) -> None:
    """A `push_items` batch reported back: mark its rows, then send the next batch or stop."""
    if job.status in ("ok", "error"):
        return
    counts = dict(job.counts or {})
    batch_ids: list[str] = list(counts.get("item_ids") or [])
    rest: list[str] = list(counts.get("rest") or [])

    def fail(message: str) -> None:
        _set_rows(session, batch_ids, "error")
        _set_rows(session, rest, "none")  # never sent: back to unsent
        counts["rest"] = []
        counts["sent"] = 0
        complete_job_error(session, job, error=message)
        job.counts = counts

    if not ok:
        fail(agent_error or "The Tally agent reported a failure.")
        return
    if not tally_response:
        fail("The agent sent no response from Tally.")
        return
    try:
        root = etree.fromstring(tally_response.encode("utf-8"))
    except etree.XMLSyntaxError as exc:
        fail(f"Could not read Tally's reply: {exc}")
        return

    line_error_el = root.find(".//LINEERROR")
    line_error = (line_error_el.text or "").strip() if line_error_el is not None else ""
    created, altered = _tag_int(root, "CREATED"), _tag_int(root, "ALTERED")
    errors, exceptions = _tag_int(root, "ERRORS"), _tag_int(root, "EXCEPTIONS")
    expected = int(counts.get("expected") or 0)

    if line_error or errors or exceptions or created + altered < expected:
        detail = line_error or "Tally did not accept every item."
        if exceptions or "Duplicate" in line_error:
            detail += (
                " Tally keeps rejected items as exceptions that block the same names and "
                "codes next time: in Tally open Display > Exceptions (Data > All Exceptions) "
                "and clear them, then send again. Nothing after this batch was sent."
            )
        counts.update(created=created, altered=altered, exceptions=exceptions, errors=errors)
        fail(detail)
        return

    _set_rows(session, batch_ids, "synced")
    counts.update(created=created, altered=altered, rest=[], sent=len(batch_ids))
    state = counts.get("state") or {}
    company = session.get(TallyCompany, job.company_id)
    catalog = session.get(SupplierCatalog, job.entity_id) if job.entity_id else None
    _finish(job, "ok", counts)
    session.flush()

    if rest and company is not None and catalog is not None:
        try:
            assert_tally_reachable(session, company)
            _enqueue_batch(
                session,
                company,
                catalog,
                state,
                int(counts["batch"]) + 1,
                rest[:BATCH_SIZE],
                rest[BATCH_SIZE:],
            )
        except Exception as exc:  # noqa: BLE001 - never 500 at the agent
            log.warning("could not queue the next items batch: %s", exc)
            _set_rows(session, rest, "none")
            counts["stopped"] = (
                exc.detail if isinstance(exc, HTTPException) else "Could not send the next batch."
            )
            job.counts = counts
    elif company is not None and not rest:
        try:  # read the new items' Tally ids back, so they can be used on sales invoices
            enqueue_stock_check(session, company, reason="reconcile")
        except Exception as exc:  # noqa: BLE001
            log.info("no reconcile check queued: %s", exc)
    session.flush()


# --------------------------------------------------------------------------- run status


@dataclass
class RunStatus:
    run_id: str | None = None
    state: str = "none"  # none | running | done | error | stopped
    batches: int = 0
    batches_done: int = 0
    total_items: int = 0
    synced: int = 0
    error: str | None = None
    updated_at: datetime | None = None


def latest_run(session: Session, tenant_id: str, catalog_id: str) -> RunStatus:
    jobs = list(
        session.scalars(
            select(TallySyncJob)
            .where(
                TallySyncJob.tenant_id == tenant_id,
                TallySyncJob.kind == KIND,
                TallySyncJob.entity_type == ENTITY,
                TallySyncJob.entity_id == catalog_id,
            )
            .order_by(TallySyncJob.created_at.desc())
            .limit(400)
        )
    )
    if not jobs:
        return RunStatus()
    run_id = (jobs[0].counts or {}).get("run")
    mine = sorted(
        (j for j in jobs if (j.counts or {}).get("run") == run_id),
        key=lambda j: int((j.counts or {}).get("batch") or 0),
    )
    first = mine[0].counts or {}
    last = mine[-1]
    synced = sum(int((j.counts or {}).get("sent") or 0) for j in mine if j.status == "ok")
    out = RunStatus(
        run_id=run_id,
        batches=int(first.get("batches") or len(mine)),
        batches_done=sum(1 for j in mine if j.status == "ok"),
        total_items=int(first.get("total_items") or 0),
        synced=synced,
        updated_at=last.completed_at or last.created_at,
    )
    lc = last.counts or {}
    if last.status in _NON_TERMINAL:
        out.state = "running"
    elif last.status == "error":
        out.state, out.error = "error", last.error
    elif lc.get("stopped"):
        out.state, out.error = "stopped", str(lc["stopped"])
    elif out.batches_done >= out.batches:
        out.state = "done"
    else:
        out.state = "stopped"
    return out


# --------------------------------------------------------------------------- check Tally


def enqueue_stock_check(
    session: Session, company: TallyCompany, *, reason: str = "check"
) -> TallySyncJob:
    """Ask the agent for Tally's masters, only to read stock groups and item names (nothing is
    staged). Reuses the masters-pull transport: the backend recognises the job by
    `entity_type='stock_check'`."""
    if company.shop_id is None:
        raise HTTPException(status_code=422, detail="Link a Tally agent to this company first.")
    busy = session.scalar(
        select(TallySyncJob.id).where(
            TallySyncJob.tenant_id == company.tenant_id,
            TallySyncJob.kind == "pull_masters",
            TallySyncJob.status.in_(_NON_TERMINAL),
        )
    )
    if busy is not None:
        raise HTTPException(
            status_code=409, detail="Tally is already being read. Wait for it to finish."
        )
    job = TallySyncJob(
        tenant_id=company.tenant_id,
        company_id=company.id,
        direction="in",
        kind="pull_masters",
        entity_type="stock_check",
        entity_id=reason,
        status="queued",
    )
    session.add(job)
    session.flush()
    outbox = AgentOutboxItem(
        shop_id=company.shop_id,
        module="tally",
        payload={
            "job_id": job.id,
            "action": "pull_masters",
            "company_name": company.company_name,
        },
        status="queued",
    )
    session.add(outbox)
    session.flush()
    job.outbox_item_id = outbox.id
    session.flush()
    return job


def latest_check(session: Session, tenant_id: str) -> TallySyncJob | None:
    return session.scalar(
        select(TallySyncJob)
        .where(
            TallySyncJob.tenant_id == tenant_id,
            TallySyncJob.kind == "pull_masters",
            TallySyncJob.entity_type == "stock_check",
        )
        .order_by(TallySyncJob.created_at.desc())
        .limit(1)
    )


def apply_stock_check(session: Session, company: TallyCompany, raw: bytes) -> dict[str, int]:
    """Record what Tally has (stock groups, item names) and, for items we pushed, store the
    Tally id so they can be used on sales invoices."""
    stock = parse_stock_items(raw)
    company.known_stock_groups = [
        {"name": g.name, "parent": g.parent or None} for g in stock.groups
    ]
    company.known_stock_items = sorted({_norm(i.name) for i in stock.items})
    company.known_stock_at = datetime.now(UTC)

    guid_by_name = {_norm(i.name): i.guid for i in stock.items if i.guid}
    linked = 0
    if guid_by_name:
        pushed = session.scalars(
            select(Item)
            .join(SupplierCatalogItem, SupplierCatalogItem.item_id == Item.id)
            .where(
                Item.tenant_id == company.tenant_id,
                Item.tally_guid.is_(None),
                SupplierCatalogItem.tally_status == "synced",
            )
            .distinct()
        )
        now = datetime.now(UTC)
        for item in pushed:
            guid = guid_by_name.get(_norm(item.name))
            if not guid:
                continue
            taken = session.scalar(
                select(TallyLink.id).where(
                    TallyLink.tenant_id == company.tenant_id,
                    TallyLink.entity_type == "item",
                    TallyLink.tally_guid == guid,
                )
            )
            if taken is not None:
                continue
            item.tally_guid = guid
            session.add(
                TallyLink(
                    tenant_id=company.tenant_id,
                    entity_type="item",
                    entity_id=item.id,
                    tally_guid=guid,
                    last_pulled_at=now,
                )
            )
            linked += 1
    session.flush()
    return {
        "groups": len(stock.groups),
        "items": len(stock.items),
        "linked": linked,
    }
