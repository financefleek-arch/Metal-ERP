"""A price Tally holds for a party shows in the invoice editor's price lookup, labelled as Tally's."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Item, Party, PartyItemPrice
from app.services.invoices.price_history import item_history, last_prices
from app.services.tally.companies import add_company
from tests.test_invoice_price_history import (  # noqa: F401
    _bill,
    _item_id,
    _line,
    _party,
    _register,
    client,
)


def _tally_price(session, party_id: str, item_id: str, rate: str, day: date, company=None):  # type: ignore[no-untyped-def]
    tenant_id = session.scalar(select(Party.tenant_id).where(Party.id == party_id))
    company = company or add_company(session, tenant_id, "GST")
    session.add(
        PartyItemPrice(
            tenant_id=tenant_id,
            company_id=company.id,
            party_id=party_id,
            item_id=item_id,
            rate=Decimal(rate),
            unit="Nos",
            quantity=Decimal("10"),
            sold_on=day,
            voucher_no="SL-91",
        )
    )
    session.commit()
    return company


def _item(session, name: str = "Steel Glass") -> str:  # type: ignore[no-untyped-def]
    from app.models._mixins import ItemSource, ItemStatus  # noqa: F401

    return _item_id(session, name)


def test_a_party_with_only_a_tally_price_gets_it_prefilled(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _register(client, "rp1@x.example.com")
    sharma = _party(client, h, "Sharma Traders")
    other = _party(client, h, "Other Co")
    _bill(client, h, other, [_line("Steel Glass", "300.00")])  # makes the item exist
    item = _item(session)
    _tally_price(session, sharma, item, "96.00", date(2026, 9, 5))

    r = client.get(
        "/api/invoices/price-history/last",
        headers=h,
        params={"party_id": sharma, "item_ids": item},
    )
    assert r.status_code == 200, r.text
    (p,) = r.json()["prices"]
    assert p["source"] == "tally" and p["unit_rate"] == "96.00" and p["uom"] == "Nos"
    assert p["series"] == "Tally SL-91"

    h2 = client.get(
        "/api/invoices/price-history", headers=h, params={"party_id": sharma, "item_id": item}
    )
    assert [row["source"] for row in h2.json()["rows"]] == ["tally"]


def test_a_later_native_sale_beats_an_older_tally_one_and_the_reverse(
    client: TestClient, session  # type: ignore[no-untyped-def]
) -> None:
    h = _register(client, "rp2@x.example.com")
    sharma = _party(client, h, "Sharma Traders")
    _bill(client, h, sharma, [_line("Steel Glass", "420.00")])  # billed today
    item = _item(session)
    tenant_id = session.scalar(select(Party.tenant_id).where(Party.id == sharma))

    company = _tally_price(session, sharma, item, "96.00", date.today() - timedelta(days=60))
    found = last_prices(session, tenant_id, sharma, [item])
    assert found[item].source == "native" and found[item].unit_rate == Decimal("420.00")

    row = session.scalar(select(PartyItemPrice))
    row.sold_on = date.today() + timedelta(days=1)  # Tally's sale is the later one
    session.commit()
    found = last_prices(session, tenant_id, sharma, [item])
    assert found[item].source == "tally" and found[item].unit_rate == Decimal("96.00")
    assert [p.source for p in item_history(session, tenant_id, sharma, item)] == ["tally", "native"]
    assert company.id


def test_a_company_the_user_cannot_see_contributes_no_price(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _register(client, "rp3@x.example.com")
    sharma = _party(client, h, "Sharma Traders")
    other = _party(client, h, "Other Co")
    _bill(client, h, other, [_line("Steel Glass", "300.00")])
    item = _item(session)
    tenant_id = session.scalar(select(Party.tenant_id).where(Party.id == sharma))
    company = _tally_price(session, sharma, item, "96.00", date(2026, 9, 5))

    assert last_prices(session, tenant_id, sharma, [item], company_ids=[]) == {}
    assert item in last_prices(session, tenant_id, sharma, [item], company_ids=[company.id])
    assert session.scalar(select(Item.id).where(Item.id == item))
