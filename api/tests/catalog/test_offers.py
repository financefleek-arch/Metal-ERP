"""The shop sends its new range on WhatsApp: the preview, the send, and what is asked of Fleek."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import CatalogShareLink, Item, Party, Tenant
from app.services import crm_sync, fleek_messages
from tests.catalog.test_media import _img, _item, _upload_photo

ORG = "org-shop-1"
TEMPLATES = {
    "templates": [
        {"ref": "wa:range", "title": "New range", "purpose": "", "cards": 2, "ready": True, "reason": None}
    ]
}


class FakeFleek:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.answers: dict[str, tuple[int, dict[str, Any]]] = {
            "GET campaigns/templates": (200, TEMPLATES),
            "GET campaigns/allowance": (200, {"cap": 1000, "used": 10, "adding": 0, "over": False}),
            "POST campaigns": (
                201,
                {"status": "success", "campaign": {"id": "camp-1", "state": "running", "recipients": 2}},
            ),
        }
        self.down = False
        self.pushed = 0

        def request(method: str, org: str, suffix: str, payload: dict[str, Any] | None = None, **_: object):  # type: ignore[no-untyped-def]
            if self.down:
                raise fleek_messages.FleekUnavailable("down")
            self.calls.append((method, suffix, payload))
            key = f"{method} {suffix}"
            if key in self.answers:
                return self.answers[key]
            return 200, {
                "status": "success",
                "campaign": {"id": suffix.split("/")[1], "state": suffix.split("/")[-1]},
            }

        def push(session: object, tenant: object, **_: object) -> crm_sync.SyncResult:
            self.pushed += 1
            self.order.append("push")
            return crm_sync.SyncResult(sent=2)

        self.order: list[str] = []
        real = request

        def recording(method, org, suffix, payload=None, **kw):  # type: ignore[no-untyped-def]
            self.order.append(f"{method} {suffix}")
            return real(method, org, suffix, payload, **kw)

        monkeypatch.setattr(fleek_messages, "request", recording)
        monkeypatch.setattr(crm_sync, "push", push)


@pytest.fixture
def fleek(monkeypatch: pytest.MonkeyPatch) -> FakeFleek:
    s = get_settings()
    monkeypatch.setattr(s, "control_plane_secret", "secret")
    monkeypatch.setattr(s, "control_plane_url", "https://fleek.test")
    return FakeFleek(monkeypatch)


def _link_firm(tenant_id: str, org: str | None = ORG) -> None:
    with SessionLocal() as s:
        t = s.get(Tenant, tenant_id)
        assert t is not None
        t.fleek_org_id = org
        s.commit()


def _shop(catalog_client, n_items: int = 3, photos: int = 3):  # type: ignore[no-untyped-def]
    """A linked firm: `n_items` priced items (first `photos` with a photo), two customers who agreed."""
    client, h, tid = catalog_client
    _link_firm(tid)
    items = []
    for i in range(n_items):
        it = _item(client, h, f"Steel Item {i}", default_rate="100", pack_qty=6)
        if i < photos:
            assert (
                _upload_photo(client, h, it["id"], _img(600, 400, color=(10 * i, 90, 160))).status_code == 200
            )
        items.append(it)
    for i in range(2):
        p = client.post(
            "/api/parties",
            headers=h,
            json={"legal_name": f"Agreed {i}", "role": "customer", "phone": f"98765432{i}0"},
        ).json()
        assert (
            client.put(
                f"/api/parties/{p['id']}/catalog-consent", headers=h, json={"status": "opted_in"}
            ).status_code
            == 200
        )
    client.post(
        "/api/parties", headers=h, json={"legal_name": "Silent", "role": "customer", "phone": "9876543299"}
    )
    return client, h, tid, items


def _preview(client: TestClient, h: dict[str, str], items: list[dict]) -> Any:
    return client.post(
        "/api/offers/preview", headers=h, json={"selection": {"ids": [i["id"] for i in items]}}
    )


def test_the_preview_picks_the_best_sellers_with_photos_and_counts_who_agreed(catalog_client, fleek) -> None:  # type: ignore[no-untyped-def]
    client, h, tid, items = _shop(catalog_client)
    with SessionLocal() as s:
        for it, n in zip(items, (5, 40, 12), strict=True):
            row = s.get(Item, it["id"])
            assert row is not None
            row.times_billed = n
        s.commit()
    r = _preview(client, h, items)
    assert r.status_code == 200, r.text
    out = r.json()
    assert [c["name"] for c in out["cards"]] == [
        "Steel Item 1",
        "Steel Item 2",
    ]  # most billed first, two cards for a two-card message
    assert out["template"]["ref"] == "wa:range" and out["template"]["cards"] == 2
    assert (out["selected"], out["offered"], out["with_photo"], out["without_photo"]) == (3, 3, 3, 0)
    assert out["audience"] == {"agreed": 2, "not_asked": 1, "opted_out": 0}
    assert out["allowance"] == {"cap": 1000, "used": 10} and out["approx_cost"] == 1.72
    assert out["ready"] is True and out["blockers"] == []
    assert all(c["photo_url"].startswith("/api/media/") for c in out["cards"])


def test_what_is_in_the_way_is_said_in_plain_words(catalog_client, fleek) -> None:  # type: ignore[no-untyped-def]
    client, h, tid, items = _shop(catalog_client, n_items=3, photos=1)
    out = _preview(client, h, items).json()
    assert (
        out["ready"] is False
        and "2 items with photos" in out["blockers"][0]
        and "1 with a photo" in out["blockers"][0]
    )
    assert out["with_photo"] == 1 and out["without_photo"] == 2

    fleek.answers["GET campaigns/templates"] = (
        200,
        {"templates": [{**TEMPLATES["templates"][0], "ready": False}]},
    )
    assert "still checking" in " ".join(_preview(client, h, items).json()["blockers"])
    fleek.answers["GET campaigns/templates"] = (200, {"templates": []})
    assert "No offer message" in " ".join(_preview(client, h, items).json()["blockers"])
    fleek.down = True
    assert "could not be reached" in " ".join(_preview(client, h, items).json()["blockers"])


def test_nobody_agreed_or_the_allowance_is_used_up_blocks_the_send(catalog_client, fleek) -> None:  # type: ignore[no-untyped-def]
    client, h, tid, items = _shop(catalog_client)
    fleek.answers["GET campaigns/allowance"] = (200, {"cap": 11, "used": 10, "adding": 0, "over": False})
    assert "monthly allowance" in " ".join(_preview(client, h, items).json()["blockers"])
    with SessionLocal() as s:
        for p in s.scalars(select(Party)):
            p.wa_catalog_consent = "none"
        s.commit()
    assert "No customer has agreed" in " ".join(_preview(client, h, items).json()["blockers"])


def test_a_firm_not_linked_to_fleek_cannot_send_and_an_empty_pick_is_refused(catalog_client, fleek) -> None:  # type: ignore[no-untyped-def]
    client, h, tid, items = _shop(catalog_client)
    assert client.post("/api/offers/preview", headers=h, json={"selection": {"ids": []}}).status_code == 422
    _link_firm(tid, None)
    r = _preview(client, h, items)
    assert r.status_code == 200 and "not set up" in r.json()["blockers"][0]
    assert client.post(
        "/api/offers/send", headers=h, json={"selection": {"ids": [items[0]["id"]]}}
    ).status_code in (409, 422)
    assert client.post("/api/offers/preview", json={"selection": {"ids": ["x"]}}).status_code == 401


def test_send_pushes_the_customers_then_asks_fleek_for_a_campaign_with_the_cards_and_the_link(
    catalog_client, fleek
) -> None:  # type: ignore[no-untyped-def]
    client, h, tid, items = _shop(catalog_client)
    r = client.post(
        "/api/offers/send",
        headers=h,
        json={"selection": {"ids": [i["id"] for i in items]}, "title": "October  range"},
    )
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["id"] == "camp-1" and out["recipients"] == 2 and out["approx_cost"] == 1.72

    # the customers (with their tokens and consent) are pushed BEFORE the campaign is created
    assert fleek.order.index("push") < fleek.order.index("POST campaigns")
    method, suffix, payload = next(c for c in fleek.calls if c[1] == "campaigns")
    assert payload is not None
    assert (
        payload["template"] == "wa:range" and payload["start"] is True and payload["name"] == "October range"
    )
    assert len(payload["cards"]) == 2
    for card in payload["cards"]:
        assert card["image_url"].startswith("http") and "/card?e=" in card["image_url"]
    assert payload["requested_by"]
    with SessionLocal() as s:
        link = s.scalar(select(CatalogShareLink).where(CatalogShareLink.token == payload["catalog_link"]))
        assert link is not None and link.title == "October range" and link.revoked_at is None
    assert out["link_token"] == payload["catalog_link"]


def test_fleeks_refusal_is_said_plainly_and_the_catalogue_link_is_kept(catalog_client, fleek) -> None:  # type: ignore[no-untyped-def]
    client, h, tid, items = _shop(catalog_client)
    fleek.answers["POST campaigns"] = (
        409,
        {
            "error": "monthly_cap",
            "message": "This would pass the plan's monthly allowance of campaign messages.",
        },
    )
    r = client.post("/api/offers/send", headers=h, json={"selection": {"ids": [i["id"] for i in items]}})
    assert r.status_code == 409 and "monthly allowance" in r.json()["detail"]
    with SessionLocal() as s:
        assert (
            s.scalar(select(CatalogShareLink.id)) is not None
        )  # the link and the customers' tokens are not lost


def test_a_send_that_is_blocked_asks_fleek_for_nothing(catalog_client, fleek) -> None:  # type: ignore[no-untyped-def]
    client, h, tid, items = _shop(catalog_client, photos=0)
    r = client.post("/api/offers/send", headers=h, json={"selection": {"ids": [i["id"] for i in items]}})
    assert r.status_code == 422 and "photo" in r.json()["detail"]
    assert fleek.pushed == 0 and not any(c[1] == "campaigns" for c in fleek.calls)


def test_pause_resume_and_cancel_are_passed_on_and_a_bad_action_is_refused(catalog_client, fleek) -> None:  # type: ignore[no-untyped-def]
    client, h, tid, items = _shop(catalog_client)
    for action in ("pause", "resume", "cancel"):
        r = client.post(f"/api/offers/camp-1/{action}", headers=h)
        assert r.status_code == 200 and r.json()["campaign"]["state"] == action
    assert client.post("/api/offers/camp-1/explode", headers=h).status_code == 422
    assert client.get("/api/offers/camp-1", headers=h).status_code == 200
    assert [c[1] for c in fleek.calls if c[0] == "POST"] == [
        "campaigns/camp-1/pause",
        "campaigns/camp-1/resume",
        "campaigns/camp-1/cancel",
    ]
