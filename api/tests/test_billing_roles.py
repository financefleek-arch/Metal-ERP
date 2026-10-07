"""Billing hardening: a counter drafts and an accountant finalizes; two people cannot silently
overwrite each other's draft."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import User
from app.security import hash_password


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _shop(client: TestClient) -> dict[str, str]:
    r = client.post(
        "/api/auth/register",
        json={
            "firm_name": "Sethia Metal Store",
            "email": "owner@x.example.com",
            "password": "s3cret-pass",
        },
    )
    assert r.status_code == 201, r.text
    return _h(r.json()["access_token"])


def _colleague(client: TestClient, owner: dict[str, str], email: str, role: str) -> dict[str, str]:
    me = client.get("/api/auth/me", headers=owner).json()
    with SessionLocal() as s:
        s.add(
            User(
                tenant_id=me["tenant_id"],
                email=email,
                password_hash=hash_password("s3cret-pass"),
                role=role,
            )
        )
        s.commit()
    r = client.post("/api/auth/login", json={"email": email, "password": "s3cret-pass"})
    assert r.status_code == 200, r.text
    return _h(r.json()["access_token"])


def _line(rate: str = "100") -> dict:
    return {"description": "Steel Glass", "quantity": "2", "uom": "nos", "unit_rate": rate}


def test_a_counter_prepares_drafts_but_cannot_finalize_or_take_money(client: TestClient) -> None:
    owner = _shop(client)
    counter = _colleague(client, owner, "counter@x.example.com", "counter")
    party = client.post("/api/parties", headers=counter, json={"legal_name": "Jay Matadee"})
    assert party.status_code == 201, party.text  # a counter can add the customer being billed
    pid = party.json()["id"]

    d = client.post("/api/invoices", headers=counter, json={"party_id": pid, "lines": [_line()]})
    assert d.status_code == 201, d.text
    inv = d.json()
    assert inv["created_by"] == "counter@x.example.com"
    ok = client.put(f"/api/invoices/{inv['id']}", headers=counter, json={"notes": "deliver Monday"})
    assert ok.status_code == 200
    assert client.post(f"/api/invoices/{inv['id']}/duplicate", headers=counter).status_code == 201

    blocked = [
        client.post(f"/api/invoices/{inv['id']}/finalize", headers=counter),
        client.post(f"/api/invoices/{inv['id']}/cancel", headers=counter),
        client.post(
            "/api/payments",
            headers=counter,
            json={"party_id": pid, "amount": "10", "mode": "cash", "allocations": []},
        ),
        client.patch("/api/tenant", headers=counter, json={"order_terms_line": "x"}),
        client.post("/api/items", headers=counter, json={"name": "New", "uom": "nos"}),
    ]
    assert [r.status_code for r in blocked] == [403] * len(blocked)
    # the accountant side can finalize what the counter prepared
    assert client.post(f"/api/invoices/{inv['id']}/finalize", headers=owner).status_code == 200
    # a finalized bill stays out of the counter's hands
    assert (
        client.put(f"/api/invoices/{inv['id']}", headers=counter, json={"notes": "x"}).status_code
        == 409
    )


def test_a_viewer_can_only_look(client: TestClient) -> None:
    owner = _shop(client)
    viewer = _colleague(client, owner, "viewer@x.example.com", "viewer")
    pid = client.post("/api/parties", headers=owner, json={"legal_name": "Jay Matadee"}).json()[
        "id"
    ]
    d = client.post("/api/invoices", headers=owner, json={"party_id": pid}).json()
    assert client.post("/api/invoices", headers=viewer, json={"party_id": pid}).status_code == 403
    assert (
        client.put(f"/api/invoices/{d['id']}", headers=viewer, json={"notes": "x"}).status_code
        == 403
    )
    assert (
        client.post("/api/parties", headers=viewer, json={"legal_name": "Nope"}).status_code == 403
    )
    assert client.get(f"/api/invoices/{d['id']}", headers=viewer).status_code == 200


def test_the_ops_console_can_hand_out_the_counter_role() -> None:
    from app.schemas_admin import ASSIGNABLE_ROLES

    assert "counter" in ASSIGNABLE_ROLES and "viewer" in ASSIGNABLE_ROLES


def test_a_stale_save_is_refused_and_names_who_changed_the_draft(client: TestClient) -> None:
    owner = _shop(client)
    counter = _colleague(client, owner, "counter@x.example.com", "counter")
    pid = client.post("/api/parties", headers=owner, json={"legal_name": "Jay Matadee"}).json()[
        "id"
    ]
    d = client.post(
        "/api/invoices", headers=owner, json={"party_id": pid, "lines": [_line()]}
    ).json()
    seen = d["updated_at"]  # both people open the draft at this version

    mine = client.put(
        f"/api/invoices/{d['id']}",
        headers=counter,
        json={"lines": [_line("150")], "expected_updated_at": seen},
    )
    assert mine.status_code == 200, mine.text
    assert mine.json()["updated_at"] != seen  # a lines-only edit still moves the version

    late = client.put(
        f"/api/invoices/{d['id']}",
        headers=owner,
        json={"lines": [_line("999")], "expected_updated_at": seen},
    )
    assert late.status_code == 409
    detail = late.json()["detail"]
    assert detail["code"] == "draft_changed" and detail["edited_by"] == "counter@x.example.com"
    # nothing of the late save landed
    now = client.get(f"/api/invoices/{d['id']}", headers=owner).json()
    assert (
        now["lines"][0]["unit_rate"] == "150.00"
        and now["last_edited_by"] == "counter@x.example.com"
    )

    # after loading their version the owner can save on top of it
    again = client.put(
        f"/api/invoices/{d['id']}",
        headers=owner,
        json={"lines": [_line("999")], "expected_updated_at": now["updated_at"]},
    )
    assert again.status_code == 200
    assert again.json()["last_edited_by"] == "owner@x.example.com"


def test_saving_twice_from_the_same_screen_is_not_a_clash(client: TestClient) -> None:
    owner = _shop(client)
    pid = client.post("/api/parties", headers=owner, json={"legal_name": "Jay Matadee"}).json()[
        "id"
    ]
    d = client.post("/api/invoices", headers=owner, json={"party_id": pid}).json()
    first = client.put(
        f"/api/invoices/{d['id']}",
        headers=owner,
        json={"notes": "a", "expected_updated_at": d["updated_at"]},
    ).json()
    second = client.put(
        f"/api/invoices/{d['id']}",
        headers=owner,
        json={"notes": "b", "expected_updated_at": first["updated_at"]},
    )
    assert second.status_code == 200
    # an old client that sends no version still works
    assert (
        client.put(f"/api/invoices/{d['id']}", headers=owner, json={"notes": "c"}).status_code
        == 200
    )


def test_a_user_row_exists_for_the_counter(client: TestClient) -> None:
    owner = _shop(client)
    _colleague(client, owner, "counter@x.example.com", "counter")
    with SessionLocal() as s:
        assert s.scalar(select(User.role).where(User.email == "counter@x.example.com")) == "counter"
