"""tools.normalize_uom — folds item/group UOM to the canonical spelling."""

from __future__ import annotations

import pytest

from app.models import Item, ProductGroup, Tenant
from tools.normalize_uom import _fold


@pytest.fixture
def tenant(session):  # type: ignore[no-untyped-def]
    t = Tenant(legal_name="Bartan Bhandar")
    session.add(t)
    session.flush()
    return t


def _item(session, tid: str, name: str, uom: str | None) -> Item:  # type: ignore[no-untyped-def]
    it = Item(tenant_id=tid, name=name, name_normalized=name.lower(), uom=uom)
    session.add(it)
    session.flush()
    return it


def test_dry_run_reports_without_writing(session, tenant) -> None:  # type: ignore[no-untyped-def]
    a = _item(session, tenant.id, "Steel Tumbler", "Doz")
    b = _item(session, tenant.id, "GI Wire", "Kgs")
    _item(session, tenant.id, "MS Angle", "kg")  # already canonical -> not reported

    report = _fold(session, tenant.id, apply=False)
    session.refresh(a)
    session.refresh(b)

    changed = {r["name"]: (r["old_uom"], r["new_uom"]) for r in report}
    assert changed == {
        "Steel Tumbler": ("Doz", "doz"),
        "GI Wire": ("Kgs", "kg"),
    }
    # nothing written on a dry run
    assert a.uom == "Doz" and b.uom == "Kgs"


def test_apply_writes_canonical_values(session, tenant) -> None:  # type: ignore[no-untyped-def]
    a = _item(session, tenant.id, "Steel Tumbler", "DOZEN")
    b = _item(session, tenant.id, "Packet Nails", "PKT")
    grp = ProductGroup(
        tenant_id=tenant.id, name="Tumblers", name_normalized="tumblers", uom="Nos"
    )
    session.add(grp)
    session.flush()

    _fold(session, tenant.id, apply=True)
    session.refresh(a)
    session.refresh(b)
    session.refresh(grp)

    assert a.uom == "doz"
    assert b.uom == "pkt"
    assert grp.uom == "nos"


def test_untabled_unit_is_lowercased_not_blanked(session, tenant) -> None:  # type: ignore[no-untyped-def]
    it = _item(session, tenant.id, "Mystery Good", "WIDGET")
    _fold(session, tenant.id, apply=True)
    session.refresh(it)
    assert it.uom == "widget"
