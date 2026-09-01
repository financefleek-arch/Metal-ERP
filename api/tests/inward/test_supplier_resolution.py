"""X2 — supplier resolution paths, exercised through the upload endpoint and
the resolver directly.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Party
from app.models._mixins import PartyRole
from app.services.inward.resolve_supplier import resolve_supplier


def _tenant_id(client: TestClient, headers: dict[str, str]) -> str:
    return client.get("/api/auth/me", headers=headers).json()["tenant_id"]


def test_gstin_match_links_and_customer_is_promoted_flag(
    inward_client: tuple[TestClient, dict[str, str]],
    sugal_pdf_bytes: bytes,
    seeded_hsn: None,
) -> None:
    client, headers = inward_client
    tenant_id = _tenant_id(client, headers)

    # a pre-existing CUSTOMER party with Sugal's GSTIN
    with SessionLocal() as s:
        p = Party(
            tenant_id=tenant_id,
            legal_name="Sugal Foods (existing)",
            gstin="19BHBPK1450P1Z3",
            role=PartyRole.customer,
        )
        s.add(p)
        s.commit()
        existing_id = p.id
        orig_source = p.source

    # upload -> should link to the existing party, not stage a new one
    r = client.post(
        "/api/inward-bills",
        headers=headers,
        files={"files": ("s.pdf", sugal_pdf_bytes, "application/pdf")},
    )
    bill = r.json()[0]
    assert bill["supplier"]["matched_party_id"] == existing_id
    assert bill["supplier"]["staged"] is None

    # approve -> customer promoted to 'both', source untouched
    rid = bill["id"]
    r = client.post(f"/api/inward-bills/{rid}/approve", headers=headers)
    assert r.status_code == 200
    assert r.json()["created_supplier_id"] is None
    assert r.json()["promoted_party_id"] == existing_id

    with SessionLocal() as s:
        p = s.get(Party, existing_id)
        assert p.role == "both"
        assert p.source == orig_source  # provenance preserved
        assert p.last_txn_at is not None  # bumped


def test_no_match_stages_new_with_derived_fields(
    inward_client: tuple[TestClient, dict[str, str]],
    sugal_pdf_bytes: bytes,
    seeded_hsn: None,
) -> None:
    client, headers = inward_client
    r = client.post(
        "/api/inward-bills",
        headers=headers,
        files={"files": ("s.pdf", sugal_pdf_bytes, "application/pdf")},
    )
    staged = r.json()[0]["supplier"]["staged"]
    assert staged["legal_name"] == "SUGAL FOODS"
    assert staged["gstin"] == "19BHBPK1450P1Z3"
    assert staged["pan"] == "BHBPK1450P"
    assert staged["default_state_code"] == "19"
    assert staged["role"] == "supplier"
    assert staged["phone"] == "8513057060"
    assert staged["address"]["pincode"] == "734005"


class TestResolverUnit:
    def test_intra_vs_inter(self) -> None:
        with SessionLocal() as s:
            intra = resolve_supplier(
                s,
                "t",
                supplier_name="A",
                supplier_gstin="19AAAAA0000A1Z5",
                buyer_gstin="19BBBBB1111B1Z5",
                place_of_supply_state_code="19",
            )
            assert intra.supply_type == "intra"

            inter = resolve_supplier(
                s,
                "t",
                supplier_name="A",
                supplier_gstin="27AAAAA0000A1Z5",  # Maharashtra
                buyer_gstin="19BBBBB1111B1Z5",  # West Bengal
                place_of_supply_state_code="27",
            )
            assert inter.supply_type == "inter"

    def test_pan_derivation_requires_valid_shape(self) -> None:
        with SessionLocal() as s:
            res = resolve_supplier(
                s,
                "t",
                supplier_name="A",
                supplier_gstin="19BHBPK1450P1Z3",
                buyer_gstin=None,
                place_of_supply_state_code=None,
            )
            assert res.new_supplier_staged["pan"] == "BHBPK1450P"

    def test_phone_and_address_land_in_staged_json(self) -> None:
        with SessionLocal() as s:
            res = resolve_supplier(
                s,
                "t",
                supplier_name="A",
                supplier_gstin="19BHBPK1450P1Z3",
                buyer_gstin=None,
                place_of_supply_state_code=None,
                supplier_phone="8513057060",
                address_block={
                    "line1": "179/1/244 Agrasen Road, Siliguri",
                    "line2": None,
                    "city": "Siliguri",
                    "state_code": "19",
                    "pincode": "734005",
                },
            )
            staged = res.new_supplier_staged
            assert staged["phone"] == "8513057060"
            assert staged["address"]["line1"] == "179/1/244 Agrasen Road, Siliguri"
            assert staged["address"]["pincode"] == "734005"


def test_malformed_phone_is_dropped_not_422(
    inward_client: tuple[TestClient, dict[str, str]],
    sugal_pdf_bytes: bytes,
    seeded_hsn: None,
) -> None:
    """A junk extracted phone must not block the whole approval."""
    from app.db import SessionLocal as SL
    from app.models import InwardBill

    client, headers = inward_client
    bill = client.post(
        "/api/inward-bills",
        headers=headers,
        files={"files": ("s.pdf", sugal_pdf_bytes, "application/pdf")},
    ).json()[0]
    bid = bill["id"]

    # corrupt the staged phone to something validate_phone rejects
    with SL() as s:
        b = s.get(InwardBill, bid)
        staged = dict(b.new_supplier_staged_json)
        staged["phone"] = "not-a-number-!!!"
        b.new_supplier_staged_json = staged
        s.commit()

    r = client.post(f"/api/inward-bills/{bid}/approve", headers=headers)
    assert r.status_code == 200, r.text
    with SL() as s:
        b = s.get(InwardBill, bid)
        sup = s.get(Party, r.json()["created_supplier_id"])
        assert sup.phone is None  # dropped, not fatal
        assert sup.legal_name == "SUGAL FOODS"
