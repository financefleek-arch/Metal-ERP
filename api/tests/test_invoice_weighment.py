"""Weighment: derived weight/count on read, operator-drawn segments,
recorded scale weights, and auto-close of open segments at finalize.

The `measure` block is recomputed on every read from line qty + uom; it is
never a stored column. `tax.py` is untouched by any of this.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.domain.weighment import LineMeasure, compute_measure, is_weight_uom, to_kg
from app.main import app


# --------------------------------------------------------------------------
# pure domain
# --------------------------------------------------------------------------


def test_weight_uom_table() -> None:
    assert is_weight_uom("kg") and is_weight_uom(" KG ") and is_weight_uom("quintal")
    assert not is_weight_uom("nos") and not is_weight_uom(None) and not is_weight_uom("set")
    assert to_kg("2", "quintal") == Decimal("200.000")
    assert to_kg("500", "g") == Decimal("0.500")
    assert to_kg("3", "nos") == Decimal("0")


def test_compute_measure_splits_weight_and_count() -> None:
    m = compute_measure(
        [
            LineMeasure(Decimal("128"), "kg", 1),
            LineMeasure(Decimal("6"), "nos", 1),
            LineMeasure(Decimal("96.5"), "kg", 2),
        ]
    )
    assert m.total_weight_kg == Decimal("224.500")
    assert m.total_count == 6
    assert m.segment_count == 2
    assert [(s.seg, s.line_from, s.line_to) for s in m.segments] == [(1, 1, 2), (2, 3, 3)]
    assert m.segments[0].weight_kg == Decimal("128.000")
    assert m.segments[0].count == 6


def test_recorded_slip_attaches_but_does_not_change_total() -> None:
    lines = [LineMeasure(Decimal("100"), "kg", 1), LineMeasure(Decimal("50"), "kg", 2)]
    m = compute_measure(lines, slips=[{"seg": 1, "recorded_kg": "101.25"}])
    assert m.total_weight_kg == Decimal("150.000")  # line-derived, unchanged
    assert m.segments[0].recorded_kg == Decimal("101.25")
    assert m.segments[1].recorded_kg is None


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _setup(client: TestClient, email: str) -> tuple[dict, str]:
    tok = client.post(
        "/api/auth/register",
        json={"firm_name": "Sethia Metal Store", "email": email, "password": "s3cret-pass"},
    ).json()["access_token"]
    h = _h(tok)
    pid = client.post("/api/parties", headers=h, json={"legal_name": "Jay Matadee"}).json()["id"]
    return h, pid


def test_measure_on_draft_read(client: TestClient) -> None:
    h, pid = _setup(client, "wm1@x.example.com")
    d = client.post("/api/invoices", headers=h, json={"party_id": pid}).json()
    r = client.put(
        f"/api/invoices/{d['id']}",
        headers=h,
        json={
            "lines": [
                {"description": "MS Angle", "quantity": "128", "unit_rate": "62", "uom": "kg"},
                {"description": "SS Kadai", "quantity": "6", "unit_rate": "700", "uom": "nos"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    m = r.json()["measure"]
    assert m["total_weight_kg"] == "128.000"
    assert m["total_count"] == 6
    assert m["segment_count"] == 1


def test_segments_and_slips_round_trip(client: TestClient) -> None:
    h, pid = _setup(client, "wm2@x.example.com")
    d = client.post("/api/invoices", headers=h, json={"party_id": pid}).json()
    r = client.put(
        f"/api/invoices/{d['id']}",
        headers=h,
        json={
            "lines": [
                {"description": "A", "quantity": "100", "unit_rate": "60", "uom": "kg", "segment_no": 1},
                {"description": "B", "quantity": "90", "unit_rate": "60", "uom": "kg", "segment_no": 1},
                {"description": "C", "quantity": "80", "unit_rate": "60", "uom": "kg", "segment_no": 2},
            ],
            "weighment_slips": [{"seg": 1, "recorded_kg": "191.50"}],
        },
    )
    assert r.status_code == 200, r.text
    m = r.json()["measure"]
    assert m["segment_count"] == 2
    assert m["segments"][0]["recorded_kg"] == "191.50"
    assert m["segments"][0]["line_to"] == 2
    assert m["segments"][1]["line_from"] == 3
    assert m["total_weight_kg"] == "270.000"

    # reload keeps the slip
    got = client.get(f"/api/invoices/{d['id']}", headers=h).json()
    assert got["measure"]["segments"][0]["recorded_kg"] == "191.50"
    assert [ln["segment_no"] for ln in got["lines"]] == [1, 1, 2]


def test_finalize_auto_closes_open_segment(client: TestClient) -> None:
    h, pid = _setup(client, "wm3@x.example.com")
    d = client.post("/api/invoices", headers=h, json={"party_id": pid}).json()
    client.put(
        f"/api/invoices/{d['id']}",
        headers=h,
        json={
            "lines": [
                {"description": "A", "quantity": "100", "unit_rate": "60", "uom": "kg", "segment_no": 1},
                {"description": "B", "quantity": "75", "unit_rate": "60", "uom": "kg", "segment_no": 2},
            ],
            # only segment 1 recorded; segment 2 left open
            "weighment_slips": [{"seg": 1, "recorded_kg": "101.00"}],
        },
    )
    r = client.post(f"/api/invoices/{d['id']}/finalize", headers=h)
    assert r.status_code == 200, r.text
    segs = r.json()["measure"]["segments"]
    assert segs[0]["recorded_kg"] == "101.00"  # operator figure kept
    assert segs[1]["recorded_kg"] == "75.000"  # auto-closed at line sum


def test_pure_piece_bill_has_zero_weight(client: TestClient) -> None:
    h, pid = _setup(client, "wm4@x.example.com")
    d = client.post("/api/invoices", headers=h, json={"party_id": pid}).json()
    r = client.put(
        f"/api/invoices/{d['id']}",
        headers=h,
        json={"lines": [{"description": "Kadai", "quantity": "18", "unit_rate": "500", "uom": "nos"}]},
    )
    m = r.json()["measure"]
    assert m["total_weight_kg"] == "0.000"
    assert m["total_count"] == 18
