"""S5: promote catalog rows to items, push them to Tally (agent simulated by hand)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from lxml import etree
from sqlalchemy import select

import app.services.tally.pull as pull_mod
from app.db import SessionLocal
from app.models import (
    AgentOutboxItem,
    BackupShop,
    CatalogProduct,
    Item,
    SupplierCatalogItem,
    TallyCompany,
    TallySyncJob,
)
from app.services.catalog import tally_items as ti
from app.services.tally.pull import process_pull_result
from app.services.tally.push import process_push_result
from tests.catalog.pdfs import TestCell, make_pdf
from tests.catalog.test_api_import import CatalogEnv, _items, _upload
from tests.catalog.test_products import _party

OK_XML = (
    "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER><BODY><DATA>"
    "<IMPORTRESULT><CREATED>{c}</CREATED><ALTERED>{a}</ALTERED><ERRORS>0</ERRORS>"
    "<EXCEPTIONS>0</EXCEPTIONS></IMPORTRESULT></DATA></BODY></ENVELOPE>"
)
EXC_XML = (
    "<ENVELOPE><BODY><DATA><LINEERROR>Duplicate Entry!</LINEERROR>"
    "<IMPORTRESULT><CREATED>0</CREATED><ERRORS>0</ERRORS><EXCEPTIONS>2</EXCEPTIONS>"
    "</IMPORTRESULT></DATA></BODY></ENVELOPE>"
)


def _masters_xml(groups: list[tuple[str, str]], items: list[tuple[str, str, str]]) -> bytes:
    """A Tally masters export: groups (name, parent), items (name, parent, guid)."""
    g = "".join(
        f'<TALLYMESSAGE><STOCKGROUP NAME="{n}"><PARENT>{p}</PARENT></STOCKGROUP></TALLYMESSAGE>'
        for n, p in groups
    )
    i = "".join(
        f'<TALLYMESSAGE><STOCKITEM NAME="{n}"><PARENT>{p}</PARENT><GUID>{gid}</GUID>'
        f"<BASEUNITS>Nos</BASEUNITS></STOCKITEM></TALLYMESSAGE>"
        for n, p, gid in items
    )
    return f"<ENVELOPE><BODY><DATA>{g}{i}</DATA></BODY></ENVELOPE>".encode()


def _cells() -> bytes:
    return make_pdf(
        [
            [
                TestCell(code="B1", name_lines=["BEER MUG 480 ML", "COL BOX 6 PC"]),
                TestCell(code="B2", name_lines=["BEER MUG 600 ML", "COL BOX 6 PC"]),
                TestCell(code="J1", name_lines=["DELI 190 ML JUICE GLASS", "COL BOX 6 PC"]),
                TestCell(code="X1", name_lines=["PLAIN THING", "COL BOX 2 PC"]),
            ]
        ]
    )


def _setup_tally(tenant_id: str, *, online: bool = True) -> str:
    with SessionLocal() as s:
        shop = BackupShop(
            name="Shop",
            api_key_hash=uuid.uuid4().hex,
            tenant_id=tenant_id,
            last_checkin_at=datetime.now(UTC) if online else None,
            last_tally_status="connected" if online else None,
        )
        s.add(shop)
        s.flush()
        s.add(
            TallyCompany(tenant_id=tenant_id, company_name="Fleek", shop_id=shop.id, ledger_map={})
        )
        s.commit()
        return shop.id


class Env:
    def __init__(self, client: TestClient, h: dict[str, str], tid: str, cid: str) -> None:
        self.client, self.h, self.tid, self.cid = client, h, tid, cid

    def post(self, path: str, body: Any = None) -> Any:
        return self.client.post(f"/api/supplier-catalogs{path}", headers=self.h, json=body)

    def get(self, path: str) -> Any:
        return self.client.get(f"/api/supplier-catalogs{path}", headers=self.h)

    def put(self, path: str, body: Any) -> Any:
        return self.client.put(f"/api/supplier-catalogs{path}", headers=self.h, json=body)

    def rows(self) -> list[dict[str, Any]]:
        return _items(self.client, self.h, self.cid, limit=200)

    def preflight(self, **extra: Any) -> dict[str, Any]:
        r = self.post(f"/{self.cid}/tally/preflight", {"all_included": True, **extra})
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]

    def checks(self, **extra: Any) -> dict[str, bool]:
        return {c["code"]: c["ok"] for c in self.preflight(**extra)["checks"]}


@pytest.fixture
def env(catalog_client: CatalogEnv) -> Env:
    client, h, tid = catalog_client
    sup = _party(client, h, "Sugal Glass House")
    cat = _upload(client, h, data=_cells(), name="a.pdf", supplier_party_id=sup).json()
    return Env(client, h, tid, cat["id"])


def _promote(env: Env) -> dict[str, Any]:
    r = env.post(f"/{env.cid}/promote", {"all_included": True})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def _do_check(
    env: Env,
    monkeypatch: pytest.MonkeyPatch,
    groups: list[tuple[str, str]] | None = None,
    items: list[tuple[str, str, str]] | None = None,
) -> None:
    """Run "check Tally": queue the job over the API, then answer it like the agent."""
    r = env.client.post("/api/supplier-catalogs/tally/check", headers=env.h)
    if r.status_code == 409:  # the push already queued its own reconcile check: answer that
        with SessionLocal() as s:
            job_id = s.scalar(
                select(TallySyncJob.id).where(
                    TallySyncJob.tenant_id == env.tid,
                    TallySyncJob.entity_type == "stock_check",
                    TallySyncJob.status == "queued",
                )
            )
    else:
        assert r.status_code == 201, r.text
        job_id = r.json()["id"]
    xml = _masters_xml(groups or [], items or [])
    monkeypatch.setattr(pull_mod, "get_object", lambda key: xml)
    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        process_pull_result(s, job, ok=True, r2_key="k", agent_error=None)
        s.commit()
        assert job.status == "ok", job.error


def _reply(job_id: str, xml: str) -> TallySyncJob:
    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        process_push_result(s, job, ok=True, tally_response=xml, agent_error=None)
        s.commit()
        s.refresh(job)
        s.expunge(job)
        return job


def _push_jobs(tid: str) -> list[TallySyncJob]:
    with SessionLocal() as s:
        rows = list(
            s.scalars(
                select(TallySyncJob)
                .where(TallySyncJob.tenant_id == tid, TallySyncJob.kind == "push_items")
                .order_by(TallySyncJob.created_at)
            )
        )
        s.expunge_all()
        return rows


def _payload_xml(job_id: str) -> etree._Element:
    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        ob = s.get(AgentOutboxItem, job.outbox_item_id)
        assert ob.payload["action"] == "push_items"
        return etree.fromstring(ob.payload["voucher_xml"].encode("utf-8"))


# --- promote ---------------------------------------------------------------------------


def test_promote_creates_items_with_code_rate_and_locks(env: Env) -> None:
    r = env.post(f"/{env.cid}/promote/preview", {"all_included": True})
    assert r.json()["create"] == 4 and r.json()["already"] == 0
    out = _promote(env)
    assert out["total"] == 4 and out["create"] == 4

    rows = env.rows()
    assert all(x["item_id"] and x["code_locked"] for x in rows)
    mug = next(x for x in rows if x["supplier_code"] == "B1")
    with SessionLocal() as s:
        item = s.get(Item, mug["item_id"])
        assert item.name == mug["display_name"]
        assert item.barcode == mug["code"] == item.sku
        assert item.uom == "nos" and item.source == "catalog"
        assert float(item.default_rate) == float(mug["sell_price"])
        assert float(item.last_purchase_rate) == float(mug["cost_price"])
        assert item.category_id == mug["category_id"]


def test_promote_twice_is_a_no_op(env: Env) -> None:
    _promote(env)
    again = _promote(env)
    assert again["create"] == 0 and again["already"] == 4
    with SessionLocal() as s:
        assert s.query(Item).filter_by(tenant_id=env.tid).count() == 4


def test_promote_links_an_existing_item_with_the_same_name(env: Env) -> None:
    mug = next(x for x in env.rows() if x["supplier_code"] == "B1")
    made = env.client.post(
        "/api/items", headers=env.h, json={"name": mug["display_name"], "uom": "nos"}
    )
    assert made.status_code == 201, made.text
    rate_before = made.json()["default_rate"]
    out = _promote(env)
    assert out["link_existing"] == 1 and out["create"] == 3
    linked = next(x for x in env.rows() if x["supplier_code"] == "B1")
    assert linked["item_id"] == made.json()["id"]
    with SessionLocal() as s:
        assert s.query(Item).filter_by(tenant_id=env.tid).count() == 4  # no duplicate
        assert s.get(Item, linked["item_id"]).default_rate == rate_before  # the shop's own rate


def test_same_name_products_get_the_code_appended(catalog_client: CatalogEnv) -> None:
    client, h, tid = catalog_client
    sup = _party(client, h, "Sugal Glass House")
    pdf = make_pdf(
        [
            [
                TestCell(code=c, name_lines=["DELI 190 ML JUICE GLASS", "COL BOX 6 PC"])
                for c in ("A", "B")
            ]
        ]
    )
    cat = _upload(client, h, data=pdf, name="a.pdf", supplier_party_id=sup).json()
    e = Env(client, h, tid, cat["id"])
    assert _promote(e)["renamed"] == 2
    rows = e.rows()
    with SessionLocal() as s:
        names = [s.get(Item, r["item_id"]).name for r in rows]
    assert len(set(names)) == 2
    assert all(r["code"] in n for r, n in zip(rows, names, strict=True))


def test_two_catalogs_promoting_one_product_share_the_item(catalog_client: CatalogEnv) -> None:
    client, h, tid = catalog_client
    sup = _party(client, h, "Sugal Glass House")
    a = _upload(client, h, data=_cells(), name="a.pdf", supplier_party_id=sup).json()
    ea = Env(client, h, tid, a["id"])
    _promote(ea)
    b = _upload(client, h, data=_cells() + b"\n% v2\n", name="b.pdf", supplier_party_id=sup).json()
    eb = Env(client, h, tid, b["id"])
    out = _promote(eb)
    assert out["reuse"] == 4 and out["create"] == 0
    assert {r["item_id"] for r in eb.rows()} == {r["item_id"] for r in ea.rows()}


# --- preflight -------------------------------------------------------------------------


def test_preflight_without_tally_connected_is_blocked(env: Env) -> None:
    _promote(env)
    pf = env.preflight()
    assert pf["ok"] is False
    assert [c["code"] for c in pf["checks"] if not c["ok"] and c["blocking"]] == ["no_company"]


def test_preflight_blocks_when_not_promoted_or_not_checked(env: Env) -> None:
    _setup_tally(env.tid)
    checks = env.checks()
    assert checks["not_promoted"] is False and checks["check_needed"] is False
    assert checks["agent"] is True


def test_preflight_blocks_when_the_agent_is_offline(env: Env) -> None:
    _setup_tally(env.tid, online=False)
    assert env.checks()["agent"] is False


def test_preflight_passes_after_promote_and_check(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_tally(env.tid)
    _promote(env)
    _do_check(env, monkeypatch, groups=[("Beer Mugs", "Primary")])
    pf = env.preflight()
    assert pf["ok"] is True, pf["checks"]
    assert pf["to_push"] == 4 and pf["batches"] == 1
    status = {g["our_name"]: g["status"] for g in pf["groups"]}
    assert status["Beer Mugs"] == "existing"
    assert "create" in status.values()


def test_a_stale_check_blocks(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_tally(env.tid)
    _promote(env)
    _do_check(env, monkeypatch)
    with SessionLocal() as s:
        c = s.scalar(select(TallyCompany).where(TallyCompany.tenant_id == env.tid))
        c.known_stock_at = datetime.now(UTC) - timedelta(hours=2)
        s.commit()
    assert env.checks()["check_needed"] is False


def test_a_name_already_in_tally_is_a_blocker_not_an_overwrite(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_tally(env.tid)
    _promote(env)
    mug = next(x for x in env.rows() if x["supplier_code"] == "B1")
    _do_check(env, monkeypatch, items=[(mug["display_name"].upper(), "Primary", "g-1")])
    pf = env.preflight()
    assert pf["ok"] is False
    assert [c["name"] for c in pf["collisions"]] == [mug["display_name"]]
    r = env.post(f"/{env.cid}/tally/push", {"all_included": True})
    assert r.status_code == 422 and "overwritten" in r.json()["detail"]
    assert _push_jobs(env.tid) == []


def test_existing_only_blocks_a_group_tally_lacks(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_tally(env.tid)
    _promote(env)
    r = env.put("/tally/settings", {"tally_group_create_policy": "existing_only"})
    assert r.status_code == 200
    _do_check(env, monkeypatch, groups=[])
    pf = env.preflight()
    assert pf["ok"] is False
    assert any(g["status"] == "missing" for g in pf["groups"])
    # mapping the group to one Tally has fixes it
    mug = next(x for x in env.rows() if x["supplier_code"] == "B1")
    _do_check(env, monkeypatch, groups=[("Drinkware", "Primary")])
    for g in {x["category_id"] for x in env.rows() if x["category_id"]}:
        env.put("/tally/settings", {"stock_group_map": {g: "Drinkware"}})
    pf = env.preflight()
    assert pf["ok"] is True, [c for c in pf["checks"] if not c["ok"]]
    assert mug["category_id"] in {g["category_id"] for g in pf["groups"]}


# --- the push --------------------------------------------------------------------------


def _ready(env: Env, monkeypatch: pytest.MonkeyPatch, **kw: Any) -> None:
    _setup_tally(env.tid)
    _promote(env)
    _do_check(env, monkeypatch, **kw)


def test_push_sends_units_groups_and_items(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _ready(env, monkeypatch, groups=[("Beer Mugs", "Primary")])
    r = env.post(f"/{env.cid}/tally/push", {"all_included": True})
    assert r.status_code == 201, r.text
    assert r.json()["state"] == "running" and r.json()["batches"] == 1

    jobs = _push_jobs(env.tid)
    assert len(jobs) == 1
    root = _payload_xml(jobs[0].id)
    assert root.findtext(".//SVCURRENTCOMPANY") == "Fleek"
    assert [u.get("NAME") for u in root.iter("UNIT")] == ["Nos"]
    created_groups = {g.get("NAME") for g in root.iter("STOCKGROUP")}
    assert "Beer Mugs" not in created_groups  # exists in Tally: never re-sent (it would alter)
    assert created_groups  # the others are created
    rows = {x["display_name"]: x for x in env.rows()}
    stock = list(root.iter("STOCKITEM"))
    assert len(stock) == 4
    for si in stock:
        row = rows[si.get("NAME")]
        assert si.findtext("PARTNO") == row["code"]
        assert si.findtext("BASEUNITS") == "Nos"
        assert si.find("ALIAS") is None and si.find("PARTNUMBER") is None
        assert all(a.get("ACTION") == "Create" for a in [si])
    parents = {si.get("NAME"): si.findtext("PARENT") for si in stock}
    mug = next(x for x in env.rows() if x["supplier_code"] == "B1")
    assert parents[mug["display_name"]] == "Beer Mugs"
    assert {x["tally_status"] for x in env.rows()} == {"queued"}


def test_success_marks_rows_synced_and_queues_a_reconcile_check(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ready(env, monkeypatch)
    env.post(f"/{env.cid}/tally/push", {"all_included": True})
    job = _push_jobs(env.tid)[0]
    done = _reply(job.id, OK_XML.format(c=7, a=0))
    assert done.status == "ok"
    assert {x["tally_status"] for x in env.rows()} == {"synced"}
    run = env.get(f"/{env.cid}/tally/run").json()
    assert run["state"] == "done" and run["synced"] == 4 and run["batches_done"] == 1
    with SessionLocal() as s:  # a reconcile "check Tally" was queued
        checks = s.scalars(
            select(TallySyncJob).where(
                TallySyncJob.tenant_id == env.tid, TallySyncJob.entity_type == "stock_check"
            )
        ).all()
        assert any(c.entity_id == "reconcile" and c.status == "queued" for c in checks)


def test_a_second_push_skips_what_is_already_in_tally(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ready(env, monkeypatch)
    env.post(f"/{env.cid}/tally/push", {"all_included": True})
    _reply(_push_jobs(env.tid)[0].id, OK_XML.format(c=7, a=0))
    _do_check(env, monkeypatch)  # fresh again; the pushed names are not in this fake export
    pf = env.preflight()
    assert pf["to_push"] == 0 and pf["already_synced"] == 4 and pf["ok"] is False
    again = env.preflight(include_synced=True)
    assert again["to_push"] == 4


def test_exceptions_stop_the_run_and_say_how_to_clear_them(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ready(env, monkeypatch)
    env.post(f"/{env.cid}/tally/push", {"all_included": True})
    done = _reply(_push_jobs(env.tid)[0].id, EXC_XML)
    assert done.status == "error" and "Exceptions" in done.error
    assert {x["tally_status"] for x in env.rows()} == {"error"}
    run = env.get(f"/{env.cid}/tally/run").json()
    assert run["state"] == "error" and run["synced"] == 0
    # a retry is allowed, and the rows are offered again
    assert env.preflight()["to_push"] == 4


def test_an_agent_failure_is_a_clean_error(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _ready(env, monkeypatch)
    env.post(f"/{env.cid}/tally/push", {"all_included": True})
    job = _push_jobs(env.tid)[0]
    with SessionLocal() as s:
        process_push_result(
            s, s.get(TallySyncJob, job.id), ok=False, tally_response=None, agent_error="boom"
        )
        s.commit()
    assert env.get(f"/{env.cid}/tally/run").json()["error"] == "boom"


def test_batches_go_one_after_another_and_groups_only_in_the_first(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ti, "BATCH_SIZE", 3)
    _ready(env, monkeypatch)
    assert env.preflight()["batches"] == 2
    env.post(f"/{env.cid}/tally/push", {"all_included": True})
    first = _push_jobs(env.tid)
    assert len(first) == 1  # only the first batch is out
    assert len(list(_payload_xml(first[0].id).iter("STOCKITEM"))) == 3
    assert len(list(_payload_xml(first[0].id).iter("STOCKGROUP"))) >= 1

    _reply(first[0].id, OK_XML.format(c=5, a=0))
    jobs = _push_jobs(env.tid)
    assert len(jobs) == 2
    second = _payload_xml(jobs[1].id)
    assert len(list(second.iter("STOCKITEM"))) == 1
    assert list(second.iter("STOCKGROUP")) == []  # groups were created with batch 1
    run = env.get(f"/{env.cid}/tally/run").json()
    assert run["state"] == "running" and run["batches_done"] == 1 and run["synced"] == 3

    _reply(jobs[1].id, OK_XML.format(c=2, a=0))
    run = env.get(f"/{env.cid}/tally/run").json()
    assert run["state"] == "done" and run["synced"] == 4 and run["batches_done"] == 2


def test_a_failed_batch_stops_the_rest_and_unqueues_them(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ti, "BATCH_SIZE", 3)
    _ready(env, monkeypatch)
    env.post(f"/{env.cid}/tally/push", {"all_included": True})
    _reply(_push_jobs(env.tid)[0].id, EXC_XML)
    assert len(_push_jobs(env.tid)) == 1  # the second batch was never sent
    statuses = sorted(x["tally_status"] for x in env.rows())
    assert statuses == ["error", "error", "error", "none"]


def test_cannot_start_a_second_push_while_one_runs(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ready(env, monkeypatch)
    assert env.post(f"/{env.cid}/tally/push", {"all_included": True}).status_code == 201
    r = env.post(f"/{env.cid}/tally/push", {"all_included": True})
    assert r.status_code == 422 and "already running" in r.json()["detail"]


def test_check_tally_reads_groups_items_and_links_pushed_items(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ready(env, monkeypatch)
    env.post(f"/{env.cid}/tally/push", {"all_included": True})
    _reply(_push_jobs(env.tid)[0].id, OK_XML.format(c=7, a=0))
    mug = next(x for x in env.rows() if x["supplier_code"] == "B1")
    _do_check(
        env,
        monkeypatch,
        groups=[("Beer Mugs", "Primary")],
        items=[(mug["display_name"], "Beer Mugs", "guid-777")],
    )
    with SessionLocal() as s:
        assert s.get(Item, mug["item_id"]).tally_guid == "guid-777"  # usable on invoices now
    settings = env.get("/tally/settings").json()
    assert settings["check_is_fresh"] is True and "Beer Mugs" in settings["known_groups"]
    latest = env.client.get("/api/supplier-catalogs/tally/check", headers=env.h).json()
    assert latest["status"] == "ok"


# --- settings --------------------------------------------------------------------------


def test_settings_roundtrip_and_validation(env: Env) -> None:
    assert env.get("/tally/settings").json()["connected"] is False
    assert env.put("/tally/settings", {"stock_group_root": "X"}).status_code == 404
    _setup_tally(env.tid)
    s = env.put("/tally/settings", {"stock_group_root": "  Catalog Items  "}).json()
    assert s["stock_group_root"] == "Catalog Items" and s["connected"] is True
    assert env.put("/tally/settings", {"stock_group_root": ""}).json()["stock_group_root"] is None
    assert env.put("/tally/settings", {"stock_group_map": {"nope": "X"}}).status_code == 422
    bad = env.client.put(
        "/api/supplier-catalogs/tally/settings",
        headers=env.h,
        json={"tally_group_create_policy": "whatever"},
    )
    assert bad.status_code == 422


def test_root_group_is_created_first_and_parents_the_new_groups(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_tally(env.tid)
    _promote(env)
    env.put("/tally/settings", {"stock_group_root": "Catalog Items"})
    _do_check(env, monkeypatch, groups=[("Beer Mugs", "Primary")])
    env.post(f"/{env.cid}/tally/push", {"all_included": True})
    root = _payload_xml(_push_jobs(env.tid)[0].id)
    groups = [(g.get("NAME"), g.findtext("PARENT")) for g in root.iter("STOCKGROUP")]
    assert groups[0] == ("Catalog Items", "")  # the root itself, under Primary
    assert all(parent == "Catalog Items" for _, parent in groups[1:])
    parents = {si.get("NAME"): si.findtext("PARENT") for si in root.iter("STOCKITEM")}
    assert "Catalog Items" in parents.values()  # the ungrouped item sits under the root


# --- the XML ---------------------------------------------------------------------------


def test_xml_escapes_quotes_and_ampersands() -> None:
    xml = ti.build_envelope(
        'Fleek "Co" & Sons',
        groups=[('Cups & "Mugs"', None)],
        items=[('Mug 6" <big> & red', 'Cups & "Mugs"', "BM-0001")],
    )
    root = etree.fromstring(xml)  # still well-formed
    assert root.findtext(".//SVCURRENTCOMPANY") == 'Fleek "Co" & Sons'
    assert next(root.iter("STOCKITEM")).get("NAME") == 'Mug 6" <big> & red'
    assert next(root.iter("STOCKGROUP")).get("NAME") == 'Cups & "Mugs"'


def test_unit_is_only_sent_with_items() -> None:
    xml = ti.build_envelope("Fleek", groups=[("G", None)], items=[])
    assert list(etree.fromstring(xml).iter("UNIT")) == []


def test_tally_gate_is_the_catalog_flag(client: TestClient) -> None:
    from tests.catalog.conftest import auth, register

    h = auth(register(client, "noflag@catalog.example.com"))
    assert client.get("/api/supplier-catalogs/tally/settings", headers=h).status_code == 404
    assert (
        client.post(
            "/api/supplier-catalogs/x/promote", headers=h, json={"all_included": True}
        ).status_code
        == 404
    )


def test_products_stay_after_items_are_promoted(env: Env) -> None:
    _promote(env)
    with SessionLocal() as s:
        prods = s.scalars(select(CatalogProduct).where(CatalogProduct.tenant_id == env.tid)).all()
        assert len(prods) == 4 and all(p.item_id and p.code_locked for p in prods)
        rows = s.scalars(
            select(SupplierCatalogItem).where(SupplierCatalogItem.catalog_id == env.cid)
        ).all()
        assert {r.item_id for r in rows} == {p.item_id for p in prods}


# --- a push that Tally never answers (closed / disconnected / agent stopped) ----------------


def _age_job(job_id: str, minutes: int) -> None:
    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        job.created_at = datetime.now(UTC) - timedelta(minutes=minutes)
        s.commit()


def _agent_not_ready(job_id: str, status: str) -> None:
    from app.services.tally.jobs import record_agent_status

    with SessionLocal() as s:
        record_agent_status(s, s.get(TallySyncJob, job_id), status)
        s.commit()


def _in_flight_text(env: Env) -> str:
    checks = {c["code"]: c["message"] for c in env.preflight()["checks"]}
    return str(checks["in_flight"])


def test_a_running_push_says_where_it_is(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ti, "BATCH_SIZE", 3)
    _ready(env, monkeypatch)
    env.post(f"/{env.cid}/tally/push", {"all_included": True})
    text = _in_flight_text(env)
    assert "already running" in text and "batch 1 of 2" in text and "0 of 4 items sent" in text
    r = env.post(f"/{env.cid}/tally/push", {"all_included": True})
    assert r.status_code == 422 and "already running" in r.json()["detail"]


@pytest.mark.parametrize(
    ("status", "words"),
    [("tally_unavailable", "not reachable"), ("no_company_loaded", "no company open")],
)
def test_a_not_ready_agent_shows_waiting_not_sending(
    env: Env, monkeypatch: pytest.MonkeyPatch, status: str, words: str
) -> None:
    _ready(env, monkeypatch)
    env.post(f"/{env.cid}/tally/push", {"all_included": True})
    _agent_not_ready(_push_jobs(env.tid)[0].id, status)

    run = env.get(f"/{env.cid}/tally/run").json()
    assert run["state"] == "running" and run["waiting"] == status
    text = _in_flight_text(env)
    assert "already running" in text and words in text and "10 minutes" in text


def test_a_stalled_batch_is_cancelled_so_the_push_can_be_sent_again(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ready(env, monkeypatch)
    env.post(f"/{env.cid}/tally/push", {"all_included": True})
    job = _push_jobs(env.tid)[0]
    _agent_not_ready(job.id, "tally_unavailable")
    _age_job(job.id, 11)

    run = env.get(f"/{env.cid}/tally/run").json()
    assert run["state"] == "error"
    assert "not reachable" in run["error"] and "send again" in run["error"]
    assert {x["tally_status"] for x in env.rows()} == {"none"}  # nothing stays stuck as queued
    with SessionLocal() as s:
        j = s.get(TallySyncJob, job.id)
        assert j.status == "error" and j.counts["expired"] is True
        assert s.get(AgentOutboxItem, j.outbox_item_id).status == "expired"

    # a late answer for the cancelled batch must not resurrect it
    _reply(job.id, OK_XML.format(c=4, a=0))
    assert {x["tally_status"] for x in env.rows()} == {"none"}

    # ...and the shop can simply send again
    assert env.post(f"/{env.cid}/tally/push", {"all_included": True}).status_code == 201


def test_a_batch_nobody_picked_up_is_cancelled_with_an_agent_message(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ready(env, monkeypatch)
    env.post(f"/{env.cid}/tally/push", {"all_included": True})
    _age_job(_push_jobs(env.tid)[0].id, 11)  # the agent never pinged: it was stopped
    run = env.get(f"/{env.cid}/tally/run").json()
    assert run["state"] == "error" and "agent did not report back" in run["error"]


def test_a_young_batch_is_left_alone(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _ready(env, monkeypatch)
    env.post(f"/{env.cid}/tally/push", {"all_included": True})
    job = _push_jobs(env.tid)[0]
    _agent_not_ready(job.id, "tally_unavailable")
    _age_job(job.id, 5)
    assert env.get(f"/{env.cid}/tally/run").json()["state"] == "running"
    assert {x["tally_status"] for x in env.rows()} == {"queued"}


def test_a_stall_in_a_later_batch_keeps_what_already_reached_tally(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ti, "BATCH_SIZE", 3)
    _ready(env, monkeypatch)
    env.post(f"/{env.cid}/tally/push", {"all_included": True})
    _reply(_push_jobs(env.tid)[0].id, OK_XML.format(c=3, a=0))  # batch 1 landed
    second = _push_jobs(env.tid)[1]
    _age_job(second.id, 11)  # batch 2 never answered

    run = env.get(f"/{env.cid}/tally/run").json()
    assert run["state"] == "error" and run["synced"] == 3
    assert sorted(x["tally_status"] for x in env.rows()) == ["none", "synced", "synced", "synced"]
