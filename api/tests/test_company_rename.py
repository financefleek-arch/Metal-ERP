"""Tally renamed the company we linked: the name here follows by Tally's id, and nothing else renames it."""

# ruff: noqa: F811
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import BackupShop, TallySyncJob
from app.services.tally.companies import add_company
from app.services.tally.company_verify import OpenCompany, apply_open_companies
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _register_firm,
    _stub_installer_build,
    client,
)

GUID = "b8c7a8b8-39db-43a0-8834-90f0c1c0357f"


def _linked(s, tid: str, name: str = "ESTIMATE & ORDER NEW 1"):
    shop = BackupShop(name="shop", api_key_hash="h", tenant_id=tid, is_active=True)
    s.add(shop)
    s.flush()
    c = add_company(s, tid, name, shop_id=shop.id)
    c.company_guid = GUID
    c.verify_state = "verified"
    s.flush()
    return shop, c


def _open(name: str, guid: str = GUID) -> list[OpenCompany]:
    return [OpenCompany(name=name, guid=guid, books_from=date(2025, 4, 1))]


def test_a_rename_in_tally_is_followed_by_the_name_here(client: TestClient) -> None:
    tid = _register_firm(client, "rn1@x.example.com")[1]
    with SessionLocal() as s:
        shop, c = _linked(s, tid)
        apply_open_companies(s, shop, _open("ESTIMATE & ORDER NEW"))
        s.commit()
        assert c.company_name == "ESTIMATE & ORDER NEW"
        assert c.verify_state == "verified" and c.company_guid == GUID
        # pushes and reads are addressed by this name, so they now name what Tally calls it
        from app.services.tally.voucher_capture import build_ledgers_request

        assert "<SVCURRENTCOMPANY>ESTIMATE &amp; ORDER NEW</SVCURRENTCOMPANY>" in build_ledgers_request(c.company_name)


def test_the_rename_is_recorded_so_support_can_see_what_it_was(client: TestClient) -> None:
    tid = _register_firm(client, "rn2@x.example.com")[1]
    with SessionLocal() as s:
        shop, c = _linked(s, tid)
        apply_open_companies(s, shop, _open("ESTIMATE & ORDER NEW"))
        s.commit()
        (job,) = s.scalars(select(TallySyncJob).where(TallySyncJob.kind == "company_renamed")).all()
        assert job.company_id == c.id and job.status == "ok"
        assert job.counts == {"from": "ESTIMATE & ORDER NEW 1", "to": "ESTIMATE & ORDER NEW"}


def test_the_next_heartbeat_changes_nothing_and_records_nothing_more(client: TestClient) -> None:
    tid = _register_firm(client, "rn3@x.example.com")[1]
    with SessionLocal() as s:
        shop, c = _linked(s, tid)
        for _ in range(3):
            apply_open_companies(s, shop, _open("ESTIMATE & ORDER NEW"))
        assert len(s.scalars(select(TallySyncJob).where(TallySyncJob.kind == "company_renamed")).all()) == 1


def test_a_name_another_company_of_the_firm_already_uses_is_not_taken(client: TestClient) -> None:
    tid = _register_firm(client, "rn4@x.example.com")[1]
    with SessionLocal() as s:
        shop, c = _linked(s, tid)
        add_company(s, tid, "ESTIMATE & ORDER NEW", kind="estimate")  # the other books already have it
        apply_open_companies(s, shop, _open("ESTIMATE & ORDER NEW"))
        assert c.company_name == "ESTIMATE & ORDER NEW 1" and c.verify_state == "renamed"
        assert s.scalar(select(TallySyncJob).where(TallySyncJob.kind == "company_renamed")) is None


def test_a_company_with_the_same_name_but_another_id_is_never_followed(client: TestClient) -> None:
    tid = _register_firm(client, "rn5@x.example.com")[1]
    with SessionLocal() as s:
        shop, c = _linked(s, tid)
        apply_open_companies(s, shop, _open("ESTIMATE & ORDER NEW 1", guid="some-other-guid"))
        assert c.company_name == "ESTIMATE & ORDER NEW 1" and c.verify_state == "different"
        assert c.company_guid == GUID


def test_a_firm_without_a_stored_id_is_not_renamed_by_a_guess(client: TestClient) -> None:
    tid = _register_firm(client, "rn6@x.example.com")[1]
    with SessionLocal() as s:
        shop, c = _linked(s, tid)
        c.company_guid = None
        apply_open_companies(s, shop, _open("ESTIMATE & ORDER NEW"))
        assert c.company_name == "ESTIMATE & ORDER NEW 1" and c.verify_state == "not_open"


def test_only_three_places_write_a_companys_name() -> None:
    """The name Tally is addressed by changes only on a person's action (the owner's edit, "use the company
    that is open", the old operator upsert) or by following Tally's own rename by id. Nothing in the refill,
    the verify heartbeat or a push assigns it."""
    app = Path(__file__).resolve().parents[1] / "app"
    writers = sorted(
        str(p.relative_to(app)).replace("\\", "/")
        for p in app.rglob("*.py")
        if re.search(r"\b(company|c)\.company_name\s*=[^=]", p.read_text(encoding="utf-8"))
    )
    assert writers == [
        "routers/tally.py",
        "services/tally/companies.py",
        "services/tally/company_verify.py",
    ]


# ---- the case seen at the pilot: two open companies that carry one Tally id (a copy), not a rename


def _two_copies() -> list[OpenCompany]:
    return [
        OpenCompany(name="ESTIMATE & ORDER NEW", guid=GUID, books_from=date(2025, 4, 1)),
        OpenCompany(name="ESTIMATE & ORDER NEW 1", guid=GUID, books_from=date(2025, 4, 1)),
    ]


def test_two_open_copies_of_one_company_are_not_a_rename(client: TestClient) -> None:
    from app.services.tally.company_identity import company_payload

    tid = _register_firm(client, "rn7@x.example.com")[1]
    with SessionLocal() as s:
        shop, c = _linked(s, tid)
        apply_open_companies(s, shop, _two_copies())
        assert c.company_name == "ESTIMATE & ORDER NEW 1"  # ours is open under its own name: left alone
        assert c.verify_state == "duplicate" and "same Tally id" in c.verify_note
        assert "copy" in c.verify_note and '"ESTIMATE & ORDER NEW 1"' in c.verify_note
        assert s.scalar(select(TallySyncJob).where(TallySyncJob.kind == "company_renamed")) is None
        # the agent would read two open companies with this id and stop a push as "renamed": checked by name
        assert company_payload(c) == {"company_name": "ESTIMATE & ORDER NEW 1"}


def test_the_listing_order_does_not_decide_which_copy_we_use(client: TestClient) -> None:
    tid = _register_firm(client, "rn8@x.example.com")[1]
    with SessionLocal() as s:
        shop, c = _linked(s, tid)
        apply_open_companies(s, shop, list(reversed(_two_copies())))
        assert c.company_name == "ESTIMATE & ORDER NEW 1" and c.verify_state == "duplicate"


def test_when_neither_copy_has_our_name_nothing_is_guessed(client: TestClient) -> None:
    tid = _register_firm(client, "rn9@x.example.com")[1]
    with SessionLocal() as s:
        shop, c = _linked(s, tid, "OLD NAME")
        apply_open_companies(s, shop, _two_copies())
        assert c.company_name == "OLD NAME" and c.verify_state == "duplicate"
        assert "neither is" in c.verify_note and s.scalar(select(TallySyncJob).where(TallySyncJob.kind == "company_renamed")) is None


def test_closing_the_copy_goes_back_to_normal(client: TestClient) -> None:
    from app.services.tally.company_identity import company_payload

    tid = _register_firm(client, "rn10@x.example.com")[1]
    with SessionLocal() as s:
        shop, c = _linked(s, tid)
        apply_open_companies(s, shop, _two_copies())
        assert c.verify_state == "duplicate"
        apply_open_companies(s, shop, [_two_copies()[1]])  # the shop closes the other copy
        assert c.verify_state == "verified" and c.company_name == "ESTIMATE & ORDER NEW 1"
        assert company_payload(c)["company_guid"] == GUID  # checked by id again


def test_a_duplicate_does_not_block_a_push_but_a_rename_or_another_id_does() -> None:
    """Ours is open under its own name, so a push can go; only a rename or another company's id stops one."""
    from types import SimpleNamespace

    from app.services.tally.push_readiness import _wrong_company_blockers

    def state(name: str):
        return SimpleNamespace(verify_state=name, verify_note="note")

    assert _wrong_company_blockers(state("duplicate")) == []
    assert _wrong_company_blockers(state("verified")) == []
    assert [b.code for b in _wrong_company_blockers(state("renamed"))] == ["company_renamed"]
    assert [b.code for b in _wrong_company_blockers(state("different"))] == ["company_different"]
