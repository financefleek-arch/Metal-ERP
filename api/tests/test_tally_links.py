"""S1.2 - one Tally link per company: the same customer or item can be linked in each of a firm's Tally
companies under a different Tally id, and nothing reads the old per-record column except as a fallback
for a record that has no link anywhere (services/tally/links.py)."""

# ruff: noqa: F811
from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Item, Party, TallyLink
from app.models._mixins import ItemSource, ItemStatus, PartySource, PartyStatus
from app.schemas_item import ItemFilter
from app.services.item_filter import filter_clauses
from app.services.tally.companies import add_company
from app.services.tally.links import (
    adopt_legacy_links,
    guid_for,
    is_linked,
    linked_ids,
    record_master_guid,
)
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _register_firm,
    _stub_installer_build,  # autouse: no real agent build in tests
    client,
)


def _firm(client: TestClient, email: str) -> str:
    _tok, tenant_id = _register_firm(client, email)
    return tenant_id


def _party(s, tenant_id: str, name: str = "Anand Metals") -> Party:
    p = Party(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        legal_name=name,
        legal_name_normalized=name.lower(),
        status=PartyStatus.active,
        source=PartySource.manual,
    )
    s.add(p)
    s.flush()
    return p


def _item(s, tenant_id: str, name: str = "SS Thali") -> Item:
    it = Item(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        name=name,
        name_normalized=name.lower(),
        source=ItemSource.manual,
        status=ItemStatus.confirmed,
    )
    s.add(it)
    s.flush()
    return it


# --------------------------------------------------------------------------
# one record, a link in each company
# --------------------------------------------------------------------------


def test_the_same_customer_is_linked_in_each_company_under_its_own_id(client: TestClient) -> None:
    tid = _firm(client, "lk1@x.example.com")
    with SessionLocal() as s:
        gst = add_company(s, tid, "Shah (GST)")
        est = add_company(s, tid, "Shah (Estimate)", kind="estimate")
        third = add_company(s, tid, "Shah (Branch)", kind="branch")
        p = _party(s, tid)
        record_master_guid(s, tid, gst, "party", p, "guid-gst")
        record_master_guid(s, tid, est, "party", p, "guid-est")
        s.commit()

        assert guid_for(s, gst, "party", p) == "guid-gst"
        assert guid_for(s, est, "party", p) == "guid-est"
        assert not is_linked(s, third, "party", p)


def test_a_link_never_takes_an_id_another_record_holds_in_that_company(client: TestClient) -> None:
    tid = _firm(client, "lk2@x.example.com")
    with SessionLocal() as s:
        a = add_company(s, tid, "A")
        b = add_company(s, tid, "B")
        first, second = _party(s, tid, "First"), _party(s, tid, "Second")
        assert record_master_guid(s, tid, a, "party", first, "same") is not None
        assert record_master_guid(s, tid, a, "party", second, "same") is None  # Tally's id names one master
        assert record_master_guid(s, tid, b, "party", second, "same") is not None  # another company is fine
        s.commit()
        assert not is_linked(s, a, "party", second)


def test_an_existing_link_is_kept_not_overwritten(client: TestClient) -> None:
    tid = _firm(client, "lk3@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "A")
        p = _party(s, tid)
        record_master_guid(s, tid, c, "party", p, "first")
        record_master_guid(s, tid, c, "party", p, "second")
        s.commit()
        assert guid_for(s, c, "party", p) == "first"


def test_a_blank_id_makes_no_link(client: TestClient) -> None:
    tid = _firm(client, "lk4@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "A")
        p = _party(s, tid)
        assert record_master_guid(s, tid, c, "party", p, "  ") is None
        assert record_master_guid(s, tid, c, "party", p, None) is None
        assert not is_linked(s, c, "party", p)


# --------------------------------------------------------------------------
# the old column: a fallback for a record with no link anywhere
# --------------------------------------------------------------------------


def test_an_import_with_no_company_keeps_the_id_until_the_first_company_adopts_it(
    client: TestClient,
) -> None:
    tid = _firm(client, "lk5@x.example.com")
    with SessionLocal() as s:
        p, it = _party(s, tid), _item(s, tid)
        record_master_guid(s, tid, None, "party", p, "p-guid")  # imported by file, no company yet
        record_master_guid(s, tid, None, "item", it, "i-guid")
        s.commit()
        assert s.scalar(select(TallyLink.id).where(TallyLink.tenant_id == tid)) is None

        company = add_company(s, tid, "Shah Traders")  # the firm's first company adopts them
        s.commit()
        assert guid_for(s, company, "party", p) == "p-guid"
        assert guid_for(s, company, "item", it) == "i-guid"
        links = list(s.scalars(select(TallyLink).where(TallyLink.company_id == company.id)))
        assert {(link.entity_type, link.tally_guid) for link in links} == {
            ("party", "p-guid"),
            ("item", "i-guid"),
        }


def test_a_later_company_does_not_adopt_anything(client: TestClient) -> None:
    tid = _firm(client, "lk6@x.example.com")
    with SessionLocal() as s:
        first = add_company(s, tid, "First")
        p = _party(s, tid)
        p.tally_guid = "leftover"
        second = add_company(s, tid, "Second")
        s.commit()
        assert not is_linked(s, second, "party", p)  # not the default: the old column says nothing
        assert is_linked(s, first, "party", p)  # the default still reads it as a fallback
        assert adopt_legacy_links(s, first) == 1  # an explicit adopt turns it into a link, once
        assert adopt_legacy_links(s, first) == 0


def test_the_old_column_is_ignored_once_the_record_has_a_link_elsewhere(client: TestClient) -> None:
    tid = _firm(client, "lk7@x.example.com")
    with SessionLocal() as s:
        a = add_company(s, tid, "A")
        b = add_company(s, tid, "B")
        p = _party(s, tid)
        p.tally_guid = "old"
        record_master_guid(s, tid, a, "party", p, "in-a")
        s.commit()
        assert guid_for(s, a, "party", p) == "in-a"
        assert guid_for(s, b, "party", p) is None  # linked in A does not mean linked in B


def test_linked_ids_counts_links_in_any_company_and_the_old_column(client: TestClient) -> None:
    tid = _firm(client, "lk8@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "A")
        linked_it, legacy_it, bare_it = _item(s, tid, "L"), _item(s, tid, "G"), _item(s, tid, "B")
        record_master_guid(s, tid, c, "item", linked_it, "g1")
        legacy_it.tally_guid = "g2"
        s.commit()
        ids = [linked_it.id, legacy_it.id, bare_it.id]
        assert linked_ids(s, "item", ids) == {linked_it.id, legacy_it.id}


# --------------------------------------------------------------------------
# the list flag and the filter
# --------------------------------------------------------------------------


def test_the_item_flag_follows_the_default_company(client: TestClient) -> None:
    tid = _firm(client, "lk9@x.example.com")
    with SessionLocal() as s:
        default = add_company(s, tid, "Default")
        other = add_company(s, tid, "Other")
        in_default, in_other, legacy, none = (_item(s, tid, n) for n in ("D", "O", "L", "N"))
        record_master_guid(s, tid, default, "item", in_default, "g-d")
        record_master_guid(s, tid, other, "item", in_other, "g-o")
        legacy.tally_guid = "g-l"
        s.commit()
        flags = {
            i.name: i.tally_linked for i in s.scalars(select(Item).where(Item.tenant_id == tid))
        }
        assert flags == {"D": True, "O": False, "L": True, "N": False}


def test_the_in_tally_filter_uses_the_same_flag(client: TestClient) -> None:
    tid = _firm(client, "lk10@x.example.com")
    with SessionLocal() as s:
        default = add_company(s, tid, "Default")
        a = _item(s, tid, "A")
        _item(s, tid, "B")
        record_master_guid(s, tid, default, "item", a, "g-a")
        s.commit()
        linked = select(Item.name).where(Item.tenant_id == tid, *filter_clauses(ItemFilter(in_tally=True)))
        unlinked = select(Item.name).where(
            Item.tenant_id == tid, *filter_clauses(ItemFilter(in_tally=False))
        )
        assert list(s.scalars(linked)) == ["A"]
        assert list(s.scalars(unlinked)) == ["B"]


# --------------------------------------------------------------------------
# nothing writes the old column any more
# --------------------------------------------------------------------------


def test_no_code_outside_the_link_module_writes_the_old_column() -> None:
    """The one writer of a master's Tally id is links.record_master_guid."""
    app_dir = Path(__file__).resolve().parent.parent / "app"
    write = re.compile(r"\b(party|item|it|entity|p)\.tally_guid\s*=(?!=)")
    ctor = re.compile(r"\b(Party|Item)\([^)]*\btally_guid\s*=", re.S)
    offenders = []
    for path in app_dir.rglob("*.py"):
        if path.name == "links.py":
            continue
        text = path.read_text(encoding="utf-8")
        if write.search(text) or ctor.search(text):
            offenders.append(str(path.relative_to(app_dir)))
    assert offenders == []


# --------------------------------------------------------------------------
# S1.2b - the unit a company holds for an item
# --------------------------------------------------------------------------


def test_each_company_keeps_its_own_unit_for_the_same_item(client: TestClient) -> None:
    from app.services.tally.links import set_unit, unit_for
    from app.services.tally.units import company_line_unit

    tid = _firm(client, "lk11@x.example.com")
    with SessionLocal() as s:
        gst = add_company(s, tid, "Shah (GST)")
        est = add_company(s, tid, "Shah (Estimate)", kind="estimate")
        it = _item(s, tid)
        record_master_guid(s, tid, gst, "item", it, "g1", unit="Nos")
        record_master_guid(s, tid, est, "item", it, "g2", unit="Pcs")
        s.commit()
        assert unit_for(s, gst, it) == "Nos"
        assert unit_for(s, est, it) == "Pcs"
        set_unit(s, est, it, "Kgs")  # a refresh of one company leaves the other alone
        s.commit()
        assert (unit_for(s, gst, it), unit_for(s, est, it)) == ("Nos", "Kgs")
        # the line goes out in the company's own spelling, and a mismatch is that company's
        assert company_line_unit(s, gst, it, "nos") == ("Nos", None)
        assert company_line_unit(s, est, it, "nos")[1] == "differs"


def test_a_unit_seen_before_any_company_moves_onto_the_first_link(client: TestClient) -> None:
    from app.services.tally.links import unit_for

    tid = _firm(client, "lk12@x.example.com")
    with SessionLocal() as s:
        it = _item(s, tid)
        record_master_guid(s, tid, None, "item", it, "g1", unit="Pcs")  # imported by file
        company = add_company(s, tid, "First")
        s.commit()
        assert unit_for(s, company, it) == "Pcs"
        link = s.scalar(select(TallyLink).where(TallyLink.company_id == company.id))
        assert link.tally_unit == "Pcs"


def test_a_later_company_does_not_inherit_the_old_unit(client: TestClient) -> None:
    from app.services.tally.links import unit_for

    tid = _firm(client, "lk13@x.example.com")
    with SessionLocal() as s:
        first = add_company(s, tid, "First")
        second = add_company(s, tid, "Second")
        it = _item(s, tid)
        it.tally_unit = "Pcs"
        s.commit()
        assert unit_for(s, first, it) == "Pcs"  # transition fallback, default company only
        assert unit_for(s, second, it) is None


# --------------------------------------------------------------------------
# S1.3 - the name a push uses is the link's
# --------------------------------------------------------------------------


def test_a_rename_here_does_not_change_the_name_sent_to_tally(client: TestClient) -> None:
    from app.services.tally.links import name_differs, tally_name_for

    tid = _firm(client, "lk14@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        p = _party(s, tid, "Anand Metals")
        record_master_guid(s, tid, c, "party", p, "g1", name="Anand Metals Pvt Ltd")
        s.commit()
        assert tally_name_for(s, c, "party", p) == "Anand Metals Pvt Ltd"
        assert name_differs(s, c, "party", p)
        p.legal_name = "Anand Metals (renamed)"
        s.commit()
        assert tally_name_for(s, c, "party", p) == "Anand Metals Pvt Ltd"  # the voucher still matches
        # a pull refreshes the Tally-side name
        record_master_guid(s, tid, c, "party", p, "g1", name="Anand Metals (renamed)")
        assert tally_name_for(s, c, "party", p) == "Anand Metals (renamed)"
        assert not name_differs(s, c, "party", p)


def test_each_company_keeps_its_own_name_and_an_unlinked_record_uses_ours(client: TestClient) -> None:
    from app.services.tally.links import tally_name_for

    tid = _firm(client, "lk15@x.example.com")
    with SessionLocal() as s:
        gst = add_company(s, tid, "GST")
        est = add_company(s, tid, "Estimate", kind="estimate")
        it = _item(s, tid, "SS Thali")
        record_master_guid(s, tid, gst, "item", it, "g1", name="SS THALI 11")
        s.commit()
        assert tally_name_for(s, gst, "item", it) == "SS THALI 11"
        assert tally_name_for(s, est, "item", it) == "SS Thali"  # not linked there: our own name


# --------------------------------------------------------------------------
# S1.6 - one staged batch per company
# --------------------------------------------------------------------------


def test_a_pull_for_one_company_never_clears_or_blocks_another(client: TestClient) -> None:
    import uuid as _uuid

    from fastapi import HTTPException

    from app.models import StagingTallyParty
    from app.services.tally.jobs import assert_no_pull_in_flight
    from app.services.tally.staging import current_batch, stage_masters_xml
    from tests.test_tally_connector import _MASTERS_XML

    tid = _firm(client, "lk16@x.example.com")
    with SessionLocal() as s:
        a = add_company(s, tid, "GST")
        b = add_company(s, tid, "Estimate", kind="estimate")
        batch_a, batch_b = str(_uuid.uuid4()), str(_uuid.uuid4())
        stage_masters_xml(s, tid, _MASTERS_XML, batch_id=batch_a, sync_job_id=None, company_id=a.id)
        stage_masters_xml(s, tid, _MASTERS_XML, batch_id=batch_b, sync_job_id=None, company_id=b.id)
        s.commit()
        rows = lambda bid: s.scalar(  # noqa: E731
            select(StagingTallyParty.id).where(StagingTallyParty.batch_id == bid).limit(1)
        )
        assert rows(batch_a) and rows(batch_b)  # staging B did not wipe A's batch

        got = current_batch(s, StagingTallyParty, tid, a.id)
        assert got[0] == batch_a and got[2] == a.id and got[3] == "GST" and got[4] == 1
        assert current_batch(s, StagingTallyParty, tid, b.id)[0] == batch_b

        # restaging for A replaces only A's
        batch_a2 = str(_uuid.uuid4())
        stage_masters_xml(s, tid, _MASTERS_XML, batch_id=batch_a2, company_id=a.id)
        s.commit()
        assert rows(batch_a) is None and rows(batch_a2) and rows(batch_b)

        # the review guard is per company too (staged rows here have no job, so it only sees uploads)
        assert_no_pull_in_flight(s, tid, a.id)
        assert_no_pull_in_flight(s, tid, b.id)
        try:
            assert_no_pull_in_flight(s, tid)
        except HTTPException:
            raise AssertionError("an upload is not blocked by company batches") from None


# --------------------------------------------------------------------------
# S1.8 - a new financial year's company links to the same customers, the old one stays as it was
# --------------------------------------------------------------------------


def test_next_years_company_links_the_same_customers_and_leaves_the_old_links(
    client: TestClient,
) -> None:
    import uuid as _uuid

    from app.models import StagingTallyParty
    from app.services.tally.staging import stage_masters_xml
    from tests.test_tally_connector import _MASTERS_XML

    tok, tid = _register_firm(client, "lk17@x.example.com")
    h = {"Authorization": f"Bearer {tok}"}
    with SessionLocal() as s:
        this_year = add_company(s, tid, "Shah Traders 25-26")
        # a customer we already hold, linked in this year's books, with the GSTIN the new year also has
        p = _party(s, tid, "Balaji Traders")
        p.gstin = "19AAJCB1234K1ZM"
        record_master_guid(s, tid, this_year, "party", p, "old-year-guid", name="Balaji Traders")
        next_year = add_company(s, tid, "Shah Traders 26-27")
        batch = str(_uuid.uuid4())
        stage_masters_xml(s, tid, _MASTERS_XML, batch_id=batch, company_id=next_year.id)
        s.commit()
        pid, this_id, next_id = p.id, this_year.id, next_year.id

    out = client.post(f"/api/parties/import/{batch}/commit", headers=h)
    assert out.status_code == 200, out.text
    assert out.json()["updated"] >= 1  # Balaji matched by GSTIN, not duplicated

    with SessionLocal() as s:
        links = {
            link.company_id: link.tally_guid
            for link in s.scalars(
                select(TallyLink).where(TallyLink.entity_type == "party", TallyLink.entity_id == pid)
            )
        }
        assert links[this_id] == "old-year-guid"  # untouched
        assert next_id in links and links[next_id] != "old-year-guid"  # linked in the new company
        assert s.scalar(select(Party.id).where(Party.tenant_id == tid, Party.legal_name == "Balaji Traders")) == pid
