"""seed_taxonomy: register seeds departments + brands + groups; idempotent
top-up; the reclassify backfill fills category_id / group_id / status.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.domain.item_taxonomy import DEPARTMENTS, STARTER_BRAND_CATEGORIES, all_group_names
from app.main import app
from app.models import Item, ItemCategory, ProductGroup
from app.models._mixins import ItemSource, ItemStatus
from app.services.catalogue.seed_taxonomy import seed_taxonomy


def _token(client: TestClient, email: str) -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Sethia Metal Store", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def test_register_seeds_full_taxonomy(session) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(app)
    _token(client, "seed-1@x.example.com")

    tid = session.scalar(select(ItemCategory.tenant_id))
    cat_names = set(
        session.scalars(
            select(ItemCategory.name).where(ItemCategory.tenant_id == tid)
        ).all()
    )
    assert set(DEPARTMENTS) <= cat_names
    assert set(STARTER_BRAND_CATEGORIES) <= cat_names

    grp_count = session.scalar(
        select(func.count()).select_from(ProductGroup).where(
            ProductGroup.tenant_id == tid
        )
    )
    assert grp_count == len(all_group_names())


def test_seed_taxonomy_is_idempotent(session) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(app)
    _token(client, "seed-2@x.example.com")
    tid = session.scalar(select(ItemCategory.tenant_id))

    before_cats = session.scalar(
        select(func.count()).select_from(ItemCategory).where(
            ItemCategory.tenant_id == tid
        )
    )
    before_grps = session.scalar(
        select(func.count()).select_from(ProductGroup).where(
            ProductGroup.tenant_id == tid
        )
    )

    res = seed_taxonomy(session, tid)
    session.commit()

    assert res.categories_created == 0
    assert res.groups_created == 0
    after_cats = session.scalar(
        select(func.count()).select_from(ItemCategory).where(
            ItemCategory.tenant_id == tid
        )
    )
    after_grps = session.scalar(
        select(func.count()).select_from(ProductGroup).where(
            ProductGroup.tenant_id == tid
        )
    )
    assert (after_cats, after_grps) == (before_cats, before_grps)


def test_reclassify_backfill_assigns_and_confirms(session) -> None:  # type: ignore[no-untyped-def]
    from tools.reclassify_items import run

    client = TestClient(app)
    _token(client, "seed-3@x.example.com")
    tid = session.scalar(select(ItemCategory.tenant_id))

    # a high-confidence item and an Other one, both unconfirmed
    kadai = Item(
        tenant_id=tid, name="240 MM KADAI GRANITE",
        name_normalized="240 mm kadai granite", hsn_code="76151030",
        source=ItemSource.import_, status=ItemStatus.unconfirmed,
    )
    mystery = Item(
        tenant_id=tid, name="MYSTERY WIDGET XYZ",
        name_normalized="mystery widget xyz",
        source=ItemSource.import_, status=ItemStatus.unconfirmed,
    )
    session.add_all([kadai, mystery])
    session.commit()

    report = run(session, tid, apply=True)
    session.commit()

    by_name = {r["name"]: r for r in report}
    assert by_name["240 MM KADAI GRANITE"]["new_department"] == "Cookware"
    assert by_name["240 MM KADAI GRANITE"]["new_status"] == str(ItemStatus.confirmed)
    assert by_name["MYSTERY WIDGET XYZ"]["new_department"] == "Other / Uncategorised"
    assert by_name["MYSTERY WIDGET XYZ"]["new_status"] == str(ItemStatus.unconfirmed)

    session.refresh(kadai)
    assert kadai.group_id is not None
    assert kadai.category_id is not None
    assert kadai.status == ItemStatus.confirmed
