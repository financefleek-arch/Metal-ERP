"""Finalize: gate, gap-free numbering, frozen totals, item accretion,
Loop-2 category learning, party last_txn_at, cancel.

WeasyPrint can't load its native libs here, so `pdf_status` is "failed"
after finalize — and that is the point of the assertion that the finalize
transaction still committed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Invoice, Item, ItemAlias, Party, ProductGroup


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _register(client: TestClient, email: str) -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Sethia Metal Store", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _party(client: TestClient, h: dict) -> str:
    r = client.post("/api/parties", headers=h, json={"legal_name": "Jay Matadee Enterprises"})
    assert r.status_code == 201
    return r.json()["id"]


def _draft_with_lines(client: TestClient, h: dict, pid: str, lines: list[dict]) -> str:
    d = client.post("/api/invoices", headers=h, json={"party_id": pid}).json()
    r = client.put(f"/api/invoices/{d['id']}", headers=h, json={"lines": lines})
    assert r.status_code == 200, r.text
    return d["id"]


# --------------------------------------------------------------------------


def test_gate_blocks_incomplete(client: TestClient) -> None:
    h = _h(_register(client, "fin1@x.example.com"))
    pid = _party(client, h)
    iid = _draft_with_lines(
        client, h, pid, [{"description": "SS Utensil", "quantity": "0", "unit_rate": "0"}]
    )
    r = client.post(f"/api/invoices/{iid}/finalize", headers=h)
    assert r.status_code == 422
    assert any("line 1" in x for x in r.json()["detail"])


def test_finalize_freezes_and_numbers(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _h(_register(client, "fin2@x.example.com"))
    pid = _party(client, h)
    iid = _draft_with_lines(
        client, h, pid,
        [{"description": "SS Utensil", "quantity": "857.15", "unit_rate": "264.00"}],
    )
    r = client.post(f"/api/invoices/{iid}/finalize", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["number"] == 1
    assert body["status"] == "final"
    assert body["totals"]["grand_total"] == "226288.00"
    assert body["totals"]["round_off"] == "0.40"
    # PDF best-effort: rendered on a box with GTK, "failed" here — but committed.
    assert body["pdf_status"] in ("rendered", "failed")

    got = client.get(f"/api/invoices/{iid}", headers=h).json()
    assert got["status"] == "final"
    assert got["number"] == 1
    assert got["totals"]["amount_in_words"].startswith("INR Two Lakh Twenty Six Thousand")

    # editing a finalized invoice is a conflict
    assert client.put(f"/api/invoices/{iid}", headers=h, json={"notes": "x"}).status_code == 409


def test_numbers_are_gap_free_and_sequential(client: TestClient) -> None:
    h = _h(_register(client, "fin3@x.example.com"))
    pid = _party(client, h)
    nums = []
    for _ in range(3):
        iid = _draft_with_lines(
            client, h, pid, [{"description": "MS Angle", "quantity": "5", "unit_rate": "58"}]
        )
        r = client.post(f"/api/invoices/{iid}/finalize", headers=h)
        assert r.status_code == 200, r.text
        nums.append(r.json()["number"])
    assert nums == [1, 2, 3]


def test_accretion_creates_unconfirmed_item(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _h(_register(client, "fin4@x.example.com"))
    pid = _party(client, h)
    iid = _draft_with_lines(
        client, h, pid,
        [{"description": "Brand New Widget 5L", "quantity": "2", "unit_rate": "500"}],
    )
    r = client.post(f"/api/invoices/{iid}/finalize", headers=h)
    assert r.status_code == 200, r.text
    assert len(r.json()["created_item_ids"]) == 1

    it = session.scalar(select(Item).where(Item.name == "Brand New Widget 5L"))
    assert it is not None
    assert it.status == "unconfirmed"
    assert it.source == "auto_from_invoice"
    assert it.times_billed == 1
    assert str(it.last_rate) in ("500", "500.0", "500.00")

    # the invoice line is now linked to it
    inv = session.scalar(select(Invoice).where(Invoice.id == iid))
    assert inv.lines[0].item_id == it.id


def test_existing_item_is_reused_and_bumped(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _h(_register(client, "fin5@x.example.com"))
    pid = _party(client, h)
    # pre-create an item
    mk = client.post("/api/items", headers=h, json={"name": "SS Balti No.3"})
    assert mk.status_code == 201
    item_id = mk.json()["id"]

    iid = _draft_with_lines(
        client, h, pid, [{"description": "ss balti no 3", "quantity": "4", "unit_rate": "532"}]
    )
    r = client.post(f"/api/invoices/{iid}/finalize", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["created_item_ids"] == []  # matched, not created

    session.expire_all()
    it = session.get(Item, item_id)
    assert it.times_billed == 1
    assert it.last_sold_at is not None


def test_loop2_learns_group_and_alias(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _h(_register(client, "fin6@x.example.com"))
    pid = _party(client, h)
    iid = _draft_with_lines(
        client, h, pid,
        [{"description": "Hawkins Contura 5L", "quantity": "3", "unit_rate": "1850"}],
    )
    r = client.post(f"/api/invoices/{iid}/finalize", headers=h)
    assert r.status_code == 200, r.text

    # a product group was learned and the line's item attached to it
    inv = session.scalar(select(Invoice).where(Invoice.id == iid))
    item = session.get(Item, inv.lines[0].item_id)
    assert item.group_id is not None
    grp = session.get(ProductGroup, item.group_id)
    assert grp is not None

    # the typed wording is now an alias (group or leaf)
    aliases = session.scalars(
        select(ItemAlias).where(ItemAlias.tenant_id == item.tenant_id)
    ).all()
    assert any(a.alias_text == "Hawkins Contura 5L" for a in aliases)
    assert all(a.source in ("learned", "auto_from_invoice") for a in aliases)


def test_finalize_sets_party_last_txn(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _h(_register(client, "fin7@x.example.com"))
    pid = _party(client, h)
    iid = _draft_with_lines(
        client, h, pid, [{"description": "MS Angle", "quantity": "5", "unit_rate": "58"}]
    )
    client.post(f"/api/invoices/{iid}/finalize", headers=h)
    session.expire_all()
    p = session.get(Party, pid)
    assert p.last_txn_at is not None


def test_refinalize_is_conflict(client: TestClient) -> None:
    h = _h(_register(client, "fin8@x.example.com"))
    pid = _party(client, h)
    iid = _draft_with_lines(
        client, h, pid, [{"description": "MS Angle", "quantity": "5", "unit_rate": "58"}]
    )
    assert client.post(f"/api/invoices/{iid}/finalize", headers=h).status_code == 200
    r = client.post(f"/api/invoices/{iid}/finalize", headers=h)
    assert r.status_code == 422
    assert "already finalized" in " ".join(r.json()["detail"])


def test_cancel_keeps_number(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _h(_register(client, "fin9@x.example.com"))
    pid = _party(client, h)
    iid = _draft_with_lines(
        client, h, pid, [{"description": "MS Angle", "quantity": "5", "unit_rate": "58"}]
    )
    client.post(f"/api/invoices/{iid}/finalize", headers=h)
    r = client.post(f"/api/invoices/{iid}/cancel", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    assert r.json()["number"] == 1

    # the next finalize still takes 2, not 1 — number not reused
    iid2 = _draft_with_lines(
        client, h, pid, [{"description": "MS Angle", "quantity": "5", "unit_rate": "58"}]
    )
    assert client.post(f"/api/invoices/{iid2}/finalize", headers=h).json()["number"] == 2


def test_draft_cannot_be_cancelled(client: TestClient) -> None:
    h = _h(_register(client, "fin10@x.example.com"))
    pid = _party(client, h)
    d = client.post("/api/invoices", headers=h, json={"party_id": pid}).json()
    assert client.post(f"/api/invoices/{d['id']}/cancel", headers=h).status_code == 409


# --------------------------------------------------------------------------
# discount_pct round-trip (Bug 1)
# --------------------------------------------------------------------------


def test_discount_pct_round_trips(client: TestClient) -> None:
    h = _h(_register(client, "fin11@x.example.com"))
    pid = _party(client, h)
    iid = _draft_with_lines(
        client, h, pid,
        [
            {
                "description": "SS Utensil",
                "quantity": "10",
                "unit_rate": "100",
                "discount": "150.00",  # 15% of 1000, resolved by the client
                "discount_pct": "15.00",
            }
        ],
    )
    got = client.get(f"/api/invoices/{iid}", headers=h).json()
    line = got["lines"][0]
    assert line["discount_pct"] == "15.00"
    assert line["discount"] == "150.00"
    # billing math is untouched by discount_pct — the absolute ₹ `discount`
    # is what feeds the total, same as if discount_pct had never been sent
    assert got["totals"]["grand_total"] == "850.00"


def test_discount_pct_null_when_omitted(client: TestClient) -> None:
    h = _h(_register(client, "fin12@x.example.com"))
    pid = _party(client, h)
    iid = _draft_with_lines(
        client, h, pid,
        [{"description": "SS Utensil", "quantity": "10", "unit_rate": "100", "discount": "50"}],
    )
    got = client.get(f"/api/invoices/{iid}", headers=h).json()
    assert got["lines"][0]["discount_pct"] is None
    assert got["lines"][0]["discount"] == "50.00"


def test_discount_pct_survives_duplicate(client: TestClient) -> None:
    h = _h(_register(client, "fin13@x.example.com"))
    pid = _party(client, h)
    iid = _draft_with_lines(
        client, h, pid,
        [
            {
                "description": "SS Utensil",
                "quantity": "10",
                "unit_rate": "100",
                "discount": "150.00",
                "discount_pct": "15.00",
            }
        ],
    )
    r = client.post(f"/api/invoices/{iid}/duplicate", headers=h)
    assert r.status_code == 201
    clone = client.get(f"/api/invoices/{r.json()['id']}", headers=h).json()
    assert clone["lines"][0]["discount_pct"] == "15.00"


# --------------------------------------------------------------------------
# finalize gate: qty/rate zero (Bug 2 — already-existing behaviour, guarded
# here so a future refactor doesn't silently drop it)
# --------------------------------------------------------------------------


def test_gate_blocks_zero_quantity(client: TestClient) -> None:
    h = _h(_register(client, "fin14@x.example.com"))
    pid = _party(client, h)
    iid = _draft_with_lines(
        client, h, pid, [{"description": "MS Angle", "quantity": "0", "unit_rate": "58"}]
    )
    r = client.post(f"/api/invoices/{iid}/finalize", headers=h)
    assert r.status_code == 422
    assert any("line 1" in x for x in r.json()["detail"])


def test_gate_blocks_zero_rate(client: TestClient) -> None:
    h = _h(_register(client, "fin15@x.example.com"))
    pid = _party(client, h)
    iid = _draft_with_lines(
        client, h, pid, [{"description": "MS Angle", "quantity": "5", "unit_rate": "0"}]
    )
    r = client.post(f"/api/invoices/{iid}/finalize", headers=h)
    assert r.status_code == 422
    assert any("line 1" in x for x in r.json()["detail"])


# --------------------------------------------------------------------------
# finalize gate: zero recorded weight against a kg-uom segment (Bug 2)
# --------------------------------------------------------------------------


def test_gate_blocks_zero_recorded_weight_with_kg_line(client: TestClient) -> None:
    h = _h(_register(client, "fin16@x.example.com"))
    pid = _party(client, h)
    d = client.post("/api/invoices", headers=h, json={"party_id": pid}).json()
    r = client.put(
        f"/api/invoices/{d['id']}",
        headers=h,
        json={
            "lines": [
                {
                    "description": "MS Rod",
                    "quantity": "50",
                    "uom": "kg",
                    "unit_rate": "60",
                    "segment_no": 1,
                }
            ],
            "weighment_slips": [{"seg": 1, "recorded_kg": "0"}],
        },
    )
    assert r.status_code == 200, r.text
    fr = client.post(f"/api/invoices/{d['id']}/finalize", headers=h)
    assert fr.status_code == 422
    assert any("segment 1" in x for x in fr.json()["detail"])


def test_gate_allows_zero_recorded_weight_for_piece_only_segment(client: TestClient) -> None:
    h = _h(_register(client, "fin17@x.example.com"))
    pid = _party(client, h)
    d = client.post("/api/invoices", headers=h, json={"party_id": pid}).json()
    r = client.put(
        f"/api/invoices/{d['id']}",
        headers=h,
        json={
            "lines": [
                {
                    "description": "SS Balti No.3",
                    "quantity": "4",
                    "uom": "nos",
                    "unit_rate": "532",
                    "segment_no": 1,
                }
            ],
            "weighment_slips": [{"seg": 1, "recorded_kg": "0"}],
        },
    )
    assert r.status_code == 200, r.text
    fr = client.post(f"/api/invoices/{d['id']}/finalize", headers=h)
    assert fr.status_code == 200, fr.text
