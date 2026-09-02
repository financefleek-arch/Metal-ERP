"""The learning loop: recategorising an unconfirmed item in the Items screen
writes a tenant `item_classify_rule` that the classifier then honours.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Item, ItemCategory, ItemClassifyRule, ProductGroup
from app.models._mixins import ItemSource, ItemStatus
from app.services.catalogue.classify_apply import load_learned_rules


def _token(client: TestClient, email: str) -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Sethia Metal Store", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def test_recategorise_unconfirmed_item_writes_a_rule(session) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(app)
    h = _h(_token(client, "learn-1@x.example.com"))
    tid = session.scalar(select(ItemCategory.tenant_id))

    # an unconfirmed item the classifier would file elsewhere
    it = Item(
        tenant_id=tid, name="VAGHARIYA JEET NO 7",
        name_normalized="vaghariya jeet no 7",
        source=ItemSource.import_, status=ItemStatus.unconfirmed,
    )
    session.add(it)
    session.commit()

    # user drops it into the Cookware > Handi group
    handi = session.scalar(
        select(ProductGroup).where(
            ProductGroup.tenant_id == tid,
            ProductGroup.name == "Handi",
        )
    )
    resp = client.patch(
        f"/api/items/{it.id}", headers=h, json={"group_id": handi.id}
    )
    assert resp.status_code == 200

    rule = session.scalar(
        select(ItemClassifyRule).where(ItemClassifyRule.tenant_id == tid)
    )
    assert rule is not None
    assert rule.group_id == handi.id
    assert rule.source == "learned"
    assert "vaghariya" in rule.phrase_normalized

    # the classifier now honours it
    learned = load_learned_rules(session, tid)
    from app.domain.item_classify import classify_item

    r = classify_item("VAGHARIYA JEET NO 9", learned=learned)
    assert (r.department, r.group) == ("Cookware", "Handi")
    assert r.source == "learned"


def test_recategorise_confirmed_item_teaches_nothing(session) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(app)
    h = _h(_token(client, "learn-2@x.example.com"))
    tid = session.scalar(select(ItemCategory.tenant_id))

    it = Item(
        tenant_id=tid, name="SOME CONFIRMED THING",
        name_normalized="some confirmed thing",
        source=ItemSource.manual, status=ItemStatus.confirmed,
    )
    session.add(it)
    session.commit()

    handi = session.scalar(
        select(ProductGroup).where(
            ProductGroup.tenant_id == tid, ProductGroup.name == "Handi"
        )
    )
    client.patch(f"/api/items/{it.id}", headers=h, json={"group_id": handi.id})

    assert session.scalar(
        select(ItemClassifyRule).where(ItemClassifyRule.tenant_id == tid)
    ) is None
