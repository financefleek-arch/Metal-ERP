"""S1.7 - who may see a Tally company (services/tally/visibility.py). One test per role and per surface."""

# ruff: noqa: F811
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.security import hash_password
from app.db import SessionLocal
from app.models import User
from app.models._mixins import UserRole
from app.services.tally.companies import add_company, get_default_company
from app.services.tally.visibility import can_see_company, visible_companies
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _register_firm,
    _stub_installer_build,
    client,
)

ROLES = ["owner", "accountant", "viewer", "counter", "weighbridge", "rate_desk"]


def _user(tid: str, role: str) -> User:
    return User(
        tenant_id=tid, email=f"{role}-{tid[:6]}@x.example.com",
        password_hash=hash_password("s3cret-pass"), role=role,
    )


def test_the_rule_for_every_role(client: TestClient) -> None:
    _tok, tid = _register_firm(client, "vis1@x.example.com")
    with SessionLocal() as s:
        everyone = add_company(s, tid, "Open")
        books = add_company(s, tid, "Estimate", kind="estimate")
        books.visible_roles = ["accountant"]
        nobody_but_owner = add_company(s, tid, "Private", kind="other")
        nobody_but_owner.visible_roles = ["owner"]
        s.flush()
        for role in ROLES:
            u = _user(tid, role)
            assert can_see_company(u, everyone)  # null roles: everyone
            assert can_see_company(u, books) == (role in ("owner", "accountant"))
            assert can_see_company(u, nobody_but_owner) == (role == "owner")  # the owner always
        admin = _user(tid, "viewer")
        admin.is_platform_admin = True
        assert can_see_company(admin, nobody_but_owner)
        s.add_all([_user(tid, "counter")])
        s.flush()
        counter = _user(tid, "counter")
        assert [c.company_name for c in visible_companies(s, counter)] == ["Open"]
