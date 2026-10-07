"""Payment reminders: proposed daily, sent only when the shop approves (Meta is stubbed)."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import PaymentReminder, Tenant, User
from app.security import hash_password
from app.services import reminders as svc
from app.services import whatsapp as wa

TODAY = date(2026, 10, 7)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stand in for Meta. Records template sends and statement sends."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(wa, "get_config", lambda *a, **k: wa.ResolvedConfig("t", "pn", "tok"))

    def fake(cfg: object, **kw: Any) -> str:
        calls.append({"kind": "template", **kw})
        return f"wamid.{len(calls)}"

    def fake_statement(session: object, party_id: str, **kw: Any) -> object:
        calls.append({"kind": "statement", "party_id": party_id, **kw})
        return SimpleNamespace(id=None)

    monkeypatch.setattr(wa, "_send_template_message", fake)
    monkeypatch.setattr(wa, "send_party_statement", fake_statement)
    return calls


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _shop(client: TestClient) -> tuple[dict[str, str], str]:
    r = client.post(
        "/api/auth/register",
        json={
            "firm_name": "Sethia Metal Store",
            "email": "o@x.example.com",
            "password": "s3cret-pass",
        },
    )
    assert r.status_code == 201, r.text
    h = _h(r.json()["access_token"])
    return h, client.get("/api/auth/me", headers=h).json()["tenant_id"]


def _party(
    client: TestClient, h: dict[str, str], name: str, phone: str | None = "9876543210"
) -> str:
    body: dict[str, Any] = {"legal_name": name}
    if phone:
        body["phone"] = phone
    r = client.post("/api/parties", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _bill(
    client: TestClient, h: dict[str, str], party_id: str, *, age: int, rate: str = "1000"
) -> str:
    d = client.post(
        "/api/invoices",
        headers=h,
        json={
            "party_id": party_id,
            "date": (TODAY - timedelta(days=age)).isoformat(),
            "lines": [
                {"description": "Steel Glass", "quantity": "1", "uom": "nos", "unit_rate": rate}
            ],
        },
    ).json()
    r = client.post(f"/api/invoices/{d['id']}/finalize", headers=h)
    assert r.status_code == 200, r.text
    return d["id"]


def _propose(tenant_id: str, as_on: date = TODAY) -> int:
    with SessionLocal() as s:
        n = svc.propose(s, s.get(Tenant, tenant_id), as_on)
        s.commit()
        return n


def _rows(tenant_id: str) -> list[PaymentReminder]:
    with SessionLocal() as s:
        rows = list(
            s.scalars(select(PaymentReminder).where(PaymentReminder.tenant_id == tenant_id))
        )
        s.expunge_all()
        return rows


def test_an_overdue_invoice_is_proposed_once_and_never_sent_by_itself(client, sent) -> None:
    h, tid = _shop(client)
    pid = _party(client, h, "Jay Matadee")
    _bill(client, h, pid, age=40)
    assert _propose(tid) == 1
    assert _propose(tid) == 0  # running it again changes nothing
    (r,) = _rows(tid)
    assert (r.kind, r.status, r.stage_days, r.oldest_overdue_days) == (
        "invoice",
        "proposed",
        30,
        40,
    )
    assert str(r.amount) == "1000.00"
    assert sent == []  # proposing never messages anyone


def test_young_invoices_and_paid_ones_are_left_alone(client, sent) -> None:
    h, tid = _shop(client)
    pid = _party(client, h, "Jay Matadee")
    _bill(client, h, pid, age=3)  # not yet 7 days overdue
    assert _propose(tid) == 0
    paid = _bill(client, h, pid, age=40)
    r = client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid,
            "amount": "1000",
            "mode": "cash",
            "date": TODAY.isoformat(),
            "allocations": [{"type": "against_invoice", "invoice_id": paid, "amount": "1000"}],
        },
    )
    assert r.status_code == 201, r.text
    assert _propose(tid) == 0


def test_several_overdue_invoices_make_one_statement_not_several_messages(client, sent) -> None:
    h, tid = _shop(client)
    pid = _party(client, h, "Jay Matadee")
    _bill(client, h, pid, age=40, rate="1000")
    _bill(client, h, pid, age=20, rate="500")
    assert _propose(tid) == 1
    (r,) = _rows(tid)
    assert r.kind == "statement" and str(r.amount) == "1500.00" and len(r.invoice_ids) == 2
    out = client.get("/api/reminders", headers=h).json()
    assert (
        len(out) == 1
        and out[0]["party_name"] == "Jay Matadee"
        and len(out[0]["invoice_numbers"]) == 2
    )


def test_sending_quotes_what_is_still_owed_and_records_the_message(client, sent) -> None:
    h, tid = _shop(client)
    pid = _party(client, h, "Jay Matadee")
    inv = _bill(client, h, pid, age=40, rate="1000")
    client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid,
            "amount": "400",
            "mode": "cash",
            "date": TODAY.isoformat(),
            "allocations": [{"type": "against_invoice", "invoice_id": inv, "amount": "400"}],
        },
    )
    _propose(tenant_id=tid)
    rid = client.get("/api/reminders", headers=h).json()[0]["id"]
    res = client.post("/api/reminders/send", headers=h, json={"ids": [rid]})
    assert res.status_code == 200, res.text
    assert (res.json()["sent"], res.json()["failed"]) == (1, 0)
    call = sent[0]
    assert call["template_name"] == "payment_reminder"
    assert call["body_params"][0] == "Jay Matadee" and call["body_params"][2] == "600.00"
    assert client.get("/api/reminders", headers=h).json() == []  # decided, off the open list
    assert [x["status"] for x in client.get("/api/reminders?state=sent", headers=h).json()] == [
        "sent"
    ]
    # sent once: the same invoice at the same stage is not proposed again, even much later
    assert _propose(tid, TODAY + timedelta(days=10)) == 0


def test_a_statement_goes_out_for_a_party_with_several_overdue_invoices(client, sent) -> None:
    h, tid = _shop(client)
    pid = _party(client, h, "Jay Matadee")
    _bill(client, h, pid, age=40)
    _bill(client, h, pid, age=20)
    _propose(tid)
    rid = client.get("/api/reminders", headers=h).json()[0]["id"]
    client.post("/api/reminders/send", headers=h, json={"ids": [rid]})
    assert sent == [{"kind": "statement", "party_id": pid, "period": "this_fy"}]


def test_skipping_clears_it_until_the_next_stage(client, sent) -> None:
    h, tid = _shop(client)
    pid = _party(client, h, "Jay Matadee")
    _bill(client, h, pid, age=40)  # on TODAY this is at stage 30
    _propose(tid)
    rid = client.get("/api/reminders", headers=h).json()[0]["id"]
    assert client.post("/api/reminders/skip", headers=h, json={"ids": [rid]}).json() == {
        "skipped": 1
    }
    assert sent == []
    assert _propose(tid, TODAY + timedelta(days=5)) == 0  # still stage 30
    assert _propose(tid, TODAY + timedelta(days=60)) == 0  # 100 days overdue is still stage 30


def test_a_second_stage_is_proposed_after_the_first_was_sent(client, sent) -> None:
    h, tid = _shop(client)
    pid = _party(client, h, "Jay Matadee")
    _bill(client, h, pid, age=8)  # stage 7
    _propose(tid)
    rid = client.get("/api/reminders", headers=h).json()[0]["id"]
    client.post("/api/reminders/send", headers=h, json={"ids": [rid]})
    assert _propose(tid, TODAY + timedelta(days=1)) == 0  # nothing new, and too soon anyway
    assert _propose(tid, TODAY + timedelta(days=8)) == 1  # now 16 days overdue: stage 15


def test_a_failed_send_stays_open_and_blocks_new_proposals(client, monkeypatch, sent) -> None:
    h, tid = _shop(client)
    pid = _party(client, h, "Jay Matadee")
    _bill(client, h, pid, age=40)
    _propose(tid)
    rid = client.get("/api/reminders", headers=h).json()[0]["id"]

    def boom(cfg: object, **kw: Any) -> str:
        raise wa.WhatsappError("send failed: 400 recipient not on WhatsApp")

    monkeypatch.setattr(wa, "_send_template_message", boom)
    res = client.post("/api/reminders/send", headers=h, json={"ids": [rid]}).json()
    assert (res["sent"], res["failed"]) == (0, 1)
    (open_one,) = client.get("/api/reminders", headers=h).json()
    assert open_one["status"] == "failed" and "not on WhatsApp" in open_one["error"]
    assert _propose(tid, TODAY + timedelta(days=1)) == 0  # one waiting item per party, not a pile


def test_paying_before_the_send_turns_it_into_a_skip(client, sent) -> None:
    h, tid = _shop(client)
    pid = _party(client, h, "Jay Matadee")
    inv = _bill(client, h, pid, age=40)
    _propose(tid)
    rid = client.get("/api/reminders", headers=h).json()[0]["id"]
    client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid,
            "amount": "1000",
            "mode": "cash",
            "date": TODAY.isoformat(),
            "allocations": [{"type": "against_invoice", "invoice_id": inv, "amount": "1000"}],
        },
    )
    res = client.post("/api/reminders/send", headers=h, json={"ids": [rid]}).json()
    assert (res["sent"], res["skipped"]) == (0, 1) and sent == []


def test_only_the_scheduler_for_firms_that_switched_it_on_proposes(client, sent) -> None:
    h, tid = _shop(client)
    pid = _party(client, h, "Jay Matadee")
    _bill(client, h, pid, age=2000)  # long overdue in real time too
    assert svc.run_daily() == 0  # off by default
    assert (
        client.patch("/api/tenant", headers=h, json={"reminder_enabled": True}).status_code == 200
    )
    assert svc.run_daily() == 1
    assert svc.run_daily() == 0


def test_settings_are_checked_and_a_counter_cannot_send(client, sent) -> None:
    h, tid = _shop(client)
    assert (
        client.patch("/api/tenant", headers=h, json={"reminder_days": "30, 7,15,7"}).json()[
            "reminder_days"
        ]
        == "7,15,30"
    )
    for bad in ("", "abc", "0", "400", "1,2,3,4,5,6"):
        assert (
            client.patch("/api/tenant", headers=h, json={"reminder_days": bad}).status_code == 422
        )
    with SessionLocal() as s:
        s.add(
            User(
                tenant_id=tid,
                email="c@x.example.com",
                password_hash=hash_password("s3cret-pass"),
                role="counter",
            )
        )
        s.commit()
    tok = client.post(
        "/api/auth/login", json={"email": "c@x.example.com", "password": "s3cret-pass"}
    )
    ch = _h(tok.json()["access_token"])
    assert client.post("/api/reminders/run", headers=ch).status_code == 403
    assert client.post("/api/reminders/send", headers=ch, json={"ids": ["x"]}).status_code == 403
    assert client.get("/api/reminders", headers=ch).status_code == 200


def _auto(h: dict[str, str]) -> None:
    r = client_patch(h, {"reminder_enabled": True, "reminder_auto_send": True})
    assert r.status_code == 200, r.text


def client_patch(h: dict[str, str], body: dict) -> Any:
    return TestClient(app).patch("/api/tenant", headers=h, json=body)


def test_automatic_firms_send_what_just_came_due_and_leave_old_backlog(client, sent) -> None:
    h, tid = _shop(client)
    fresh = _party(client, h, "Fresh Overdue")
    old = _party(client, h, "Months Overdue", phone="9123456780")
    today = date.today()
    # one bill that crossed the 7-day stage 2 days ago, one that has been overdue for 200 days
    for pid, age in ((fresh, 9), (old, 200)):
        d = client.post(
            "/api/invoices",
            headers=h,
            json={
                "party_id": pid,
                "date": (today - timedelta(days=age)).isoformat(),
                "lines": [
                    {"description": "Glass", "quantity": "1", "uom": "nos", "unit_rate": "100"}
                ],
            },
        ).json()
        assert client.post(f"/api/invoices/{d['id']}/finalize", headers=h).status_code == 200
    _auto(h)

    assert svc.run_daily(auto_send_allowed=False) == 2  # outside working hours: proposals only
    assert sent == []
    assert svc.run_daily() == 0
    assert [c["body_params"][0] for c in sent] == ["Fresh Overdue"]  # the 200-day backlog waits
    by_party = {r.party_id: r.status for r in _rows(tid)}
    assert by_party[fresh] == "sent" and by_party[old] == "proposed"
    assert svc.run_daily() == 0 and len(sent) == 1  # never twice


def test_a_firm_that_reviews_first_is_never_sent_to_automatically(client, sent) -> None:
    h, tid = _shop(client)
    pid = _party(client, h, "Jay Matadee")
    d = client.post(
        "/api/invoices",
        headers=h,
        json={
            "party_id": pid,
            "date": (date.today() - timedelta(days=9)).isoformat(),
            "lines": [{"description": "Glass", "quantity": "1", "uom": "nos", "unit_rate": "100"}],
        },
    ).json()
    client.post(f"/api/invoices/{d['id']}/finalize", headers=h)
    client_patch(h, {"reminder_enabled": True})
    assert svc.run_daily() == 1 and sent == []


def test_fleek_can_switch_automatic_reminders_off_for_one_firm(client, sent) -> None:
    from tests.test_admin import _admin_token

    h, tid = _shop(client)
    pid = _party(client, h, "Jay Matadee")
    d = client.post(
        "/api/invoices",
        headers=h,
        json={
            "party_id": pid,
            "date": (date.today() - timedelta(days=9)).isoformat(),
            "lines": [{"description": "Glass", "quantity": "1", "uom": "nos", "unit_rate": "100"}],
        },
    ).json()
    client.post(f"/api/invoices/{d['id']}/finalize", headers=h)
    assert client.get("/api/tenant", headers=h).json()["reminder_auto_allowed"] is True
    assert (
        client_patch(h, {"reminder_enabled": True, "reminder_auto_send": True}).status_code == 200
    )

    ops = {"Authorization": f"Bearer {_admin_token(client)}"}
    off = client.patch(
        f"/api/admin/firms/{tid}", headers=ops, json={"reminder_auto_allowed": False}
    )
    assert off.status_code == 200 and off.json()["reminder_auto_allowed"] is False

    assert svc.run_daily() == 1 and sent == []  # still proposed for review, never sent by itself
    assert client_patch(h, {"reminder_auto_send": True}).status_code == 403  # and cannot be chosen
    assert client_patch(h, {"reminder_auto_send": False}).status_code == 200  # but can be dropped


def test_credit_days_can_be_set_and_a_null_does_not_erase_a_setting(client, sent) -> None:
    h, tid = _shop(client)
    assert client.get("/api/tenant", headers=h).json()["default_credit_days"] == 0
    assert client_patch(h, {"default_credit_days": 30}).json()["default_credit_days"] == 30
    assert client_patch(h, {"default_credit_days": 400}).status_code == 422
    after = client_patch(h, {"default_credit_days": None, "reminder_enabled": None})
    assert after.status_code == 200
    assert after.json()["default_credit_days"] == 30 and after.json()["reminder_enabled"] is False
