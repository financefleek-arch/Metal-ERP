"""New write actions: viewers can look, not change; real changes leave an audit entry."""

from __future__ import annotations

import io
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditLog, User
from tests.catalog.conftest import auth, seed_hsn
from tests.catalog.pdfs import TestCell, make_pdf
from tests.catalog.test_api_import import CatalogEnv, _upload
from tests.catalog.test_products import _party

BASE = "/api/supplier-catalogs"


@pytest.fixture
def shop(catalog_client: CatalogEnv) -> tuple[TestClient, dict[str, str], str, dict[str, Any]]:
    client, h, _ = catalog_client
    seed_hsn("7013")
    sup = _party(client, h, "Sugal Glass House")
    pdf = make_pdf([[TestCell(code="B1", name_lines=["BEER MUG 480 ML", "COL BOX 6 PC"])]])
    cat = _upload(client, h, data=pdf, name="a.pdf", supplier_party_id=sup).json()
    return client, h, sup, cat


def _viewer(client: TestClient, h: dict[str, str]) -> dict[str, str]:
    me = client.get("/api/auth/me", headers=h).json()
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.id == me["id"]))
        assert u is not None
        u.role = "viewer"
        s.commit()
    tok = client.post("/api/auth/login", json={"email": me["email"], "password": "s3cret-pass"})
    return auth(tok.json()["access_token"])


def _audit(action: str) -> list[AuditLog]:
    with SessionLocal() as s:
        rows = list(s.scalars(select(AuditLog).where(AuditLog.action == action)))
        s.expunge_all()
        return rows


def test_viewers_cannot_use_the_new_write_actions(shop) -> None:
    client, h, sup, cat = shop
    item = client.post("/api/items", headers=h, json={"name": "Viewer Mug", "uom": "nos"}).json()
    vh = _viewer(client, h)
    sel = {"all_included": True}
    blocked = [
        client.put(
            f"{BASE}/suppliers/{sup}/defaults", headers=vh, json={"add_automatically": True}
        ),
        client.post(f"{BASE}/{cat['id']}/prices/apply", headers=vh, json=sel),
        client.post(f"{BASE}/{cat['id']}/promote/undo", headers=vh, json={"item_ids": ["x"]}),
        client.post(f"{BASE}/{cat['id']}/promote", headers=vh, json=sel),
        client.post("/api/item-tally/push", headers=vh, json={"selection": {"ids": [item["id"]]}}),
        client.post("/api/item-labels", headers=vh, json={"selection": {"ids": [item["id"]]}}),
        client.post("/api/items/bulk-rename", headers=vh, json={"ids": [item["id"]], "find": "a"}),
        client.post("/api/item-categories", headers=vh, json={"name": "Nope"}),
        client.post(
            "/api/item-sheet/import",
            headers=vh,
            params={"dry_run": "false"},
            files={"file": ("a.csv", b"Code\n1\n", "text/csv")},
        ),
    ]
    assert [r.status_code for r in blocked] == [403] * len(blocked)
    # looking is fine
    assert client.get(f"{BASE}/suppliers/{sup}/defaults", headers=vh).status_code == 200
    assert (
        client.post(f"{BASE}/{cat['id']}/prices/preview", headers=vh, json=sel).status_code == 200
    )
    assert client.get("/api/jobs/active", headers=vh).status_code == 200


def test_real_changes_are_audited_and_previews_are_not(shop) -> None:
    client, h, sup, cat = shop
    sel = {"all_included": True}
    out = client.post(f"{BASE}/{cat['id']}/promote", headers=h, json=sel).json()
    assert len(_audit("add_to_items")) == 1
    client.post(f"{BASE}/{cat['id']}/prices/preview", headers=h, json=sel)
    assert _audit("update_item_prices") == []
    client.patch(f"{BASE}/{cat['id']}", headers=h, json={"bulk_margin_pct": "40"})
    client.post(f"{BASE}/{cat['id']}/prices/apply", headers=h, json=sel)
    assert _audit("update_item_prices")[0].after_json == {"updated": 1}
    client.post(
        f"{BASE}/{cat['id']}/promote/undo", headers=h, json={"item_ids": out["created_item_ids"]}
    )
    assert _audit("undo_add_to_items")[0].after_json == {"removed": 1, "kept": 0}

    ids = [
        client.post("/api/items", headers=h, json={"name": "Aud Cup", "uom": "nos"}).json()["id"]
    ]
    body = {"ids": ids, "find": "Cup", "replace": "Mug"}
    client.post("/api/items/bulk-rename?dry_run=true", headers=h, json=body)
    assert _audit("bulk_rename") == []
    client.post("/api/items/bulk-rename", headers=h, json=body)
    assert _audit("bulk_rename")[0].after_json["changed"] == 1

    cat2 = client.post(
        "/api/item-categories", headers=h, json={"name": "Aud", "hsn_code": "7013"}
    ).json()
    client.patch(f"/api/items/{ids[0]}", headers=h, json={"category_id": cat2["id"]})
    client.post(f"/api/item-categories/{cat2['id']}/apply-hsn", headers=h)
    assert _audit("apply_hsn")[0].after_json["hsn"] == "7013"

    wb = Workbook()
    ws = wb.active
    ws.append(["Code", "Selling price"])
    ws.append(["no-such", 5])
    buf = io.BytesIO()
    wb.save(buf)
    client.post(
        "/api/item-sheet/import",
        headers=h,
        params={"dry_run": "false"},
        files={"file": ("a.xlsx", buf.getvalue(), "application/octet-stream")},
    )
    assert _audit("sheet_import") == []  # nothing changed: nothing to record
