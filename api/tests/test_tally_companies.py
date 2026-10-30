"""S1.1 - a firm can link more than one Tally company, with exactly one default.

Everything that used to ask for "the firm's Tally company" now asks for its default, so a firm with one
company behaves exactly as before. These tests cover the helper module and the one-default rule.
"""

# ruff: noqa: F811
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import TallyCompany, Tenant
from app.services.tally.companies import (
    add_company,
    get_company,
    get_default_company,
    list_companies,
    set_default,
)
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _register_firm,
    _stub_installer_build,  # autouse: no real agent build in tests
    client,
)


def _firm(client: TestClient, email: str, *, gst: bool = False) -> str:
    _tok, tenant_id = _register_firm(client, email)
    with SessionLocal() as s:
        s.get(Tenant, tenant_id).gst_enabled = gst
        s.commit()
    return tenant_id


def test_the_first_company_is_the_default_and_later_ones_are_not(client: TestClient) -> None:
    tid = _firm(client, "co1@x.example.com")
    with SessionLocal() as s:
        a = add_company(s, tid, "Shah Traders")
        b = add_company(s, tid, "Shah Traders (Estimate)", kind="estimate")
        s.commit()
        assert (a.is_default, b.is_default) == (True, False)
        assert get_default_company(s, tid).id == a.id
        assert [c.company_name for c in list_companies(s, tid)] == [
            "Shah Traders",
            "Shah Traders (Estimate)",
        ]


def test_kind_follows_the_firms_gst_setting_unless_given(client: TestClient) -> None:
    gst = _firm(client, "co2@x.example.com", gst=True)
    plain = _firm(client, "co3@x.example.com", gst=False)
    with SessionLocal() as s:
        assert add_company(s, gst, "A").kind == "gst"
        assert add_company(s, plain, "B").kind == "other"
        assert add_company(s, plain, "C", kind="fy").kind == "fy"
        with pytest.raises(ValueError):
            add_company(s, plain, "D", kind="nonsense")
        s.rollback()


def test_set_default_moves_the_default_and_keeps_exactly_one(client: TestClient) -> None:
    tid = _firm(client, "co4@x.example.com")
    with SessionLocal() as s:
        a = add_company(s, tid, "One")
        b = add_company(s, tid, "Two")
        set_default(s, b)
        s.commit()
        assert (a.is_default, b.is_default) == (False, True)
        assert get_default_company(s, tid).id == b.id
        assert sum(c.is_default for c in list_companies(s, tid)) == 1


def test_the_database_refuses_two_defaults(client: TestClient) -> None:
    tid = _firm(client, "co5@x.example.com")
    with SessionLocal() as s:
        add_company(s, tid, "One")
        s.commit()
        s.add(TallyCompany(tenant_id=tid, company_name="Two", ledger_map={}, is_default=True))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()


def test_another_firms_default_is_independent(client: TestClient) -> None:
    t1 = _firm(client, "co6@x.example.com")
    t2 = _firm(client, "co7@x.example.com")
    with SessionLocal() as s:
        add_company(s, t1, "Same Name")
        add_company(s, t2, "Same Name")
        s.commit()
        assert get_default_company(s, t1).id != get_default_company(s, t2).id


def test_get_company_is_scoped_to_the_firm(client: TestClient) -> None:
    t1 = _firm(client, "co8@x.example.com")
    t2 = _firm(client, "co9@x.example.com")
    with SessionLocal() as s:
        mine = add_company(s, t1, "Mine")
        s.commit()
        assert get_company(s, t1, mine.id).id == mine.id
        assert get_company(s, t2, mine.id) is None


def test_a_disabled_company_is_hidden_and_cannot_be_the_default(client: TestClient) -> None:
    tid = _firm(client, "co10@x.example.com")
    with SessionLocal() as s:
        add_company(s, tid, "Live")
        old = add_company(s, tid, "Old Year", kind="fy")
        old.status = "disabled"
        s.commit()
        assert [c.company_name for c in list_companies(s, tid)] == ["Live"]
        assert len(list_companies(s, tid, include_disabled=True)) == 2
        with pytest.raises(ValueError):
            set_default(s, old)


def test_no_company_means_no_default(client: TestClient) -> None:
    tid = _firm(client, "co11@x.example.com")
    with SessionLocal() as s:
        assert get_default_company(s, tid) is None
        assert list_companies(s, tid) == []


def test_the_admin_page_still_manages_only_the_default_company(client: TestClient) -> None:
    from tests.test_invoice_tally_push_integration import _admin_token, _h

    tid = _firm(client, "co12@x.example.com")
    admin = _admin_token(client)
    for name in ("First Name", "Renamed"):
        r = client.post(
            f"/api/admin/firms/{tid}/tally/company", json={"company_name": name}, headers=_h(admin)
        )
        assert r.status_code == 200, r.text
    with SessionLocal() as s:
        rows = list(s.scalars(select(TallyCompany).where(TallyCompany.tenant_id == tid)))
        assert [(c.company_name, c.is_default) for c in rows] == [("Renamed", True)]
