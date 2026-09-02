"""Item catalogue CRUD: create/dedupe, search, confirm, merge, delete guard,
metal attributes, price band, HSN→GST auto-fill, reference lookups.

SQLite fixture DB (fuzzy search degrades to substring; that's exercised).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import HsnCode


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _seed_hsn(session) -> None:  # type: ignore[no-untyped-def]
    """A couple of HSN reference rows for the lookup + GST-fill tests."""
    for code, desc, chapter, rate in (
        ("72193590", "Flat-rolled stainless steel, cold-rolled, < 3mm", "72", 18.0),
        ("73239390", "SS household/kitchen articles, other", "73", 12.0),
    ):
        if session.scalar(select(HsnCode).where(HsnCode.code == code)) is None:
            session.add(
                HsnCode(code=code, description=desc, chapter=chapter, default_gst_rate=rate)
            )
    session.commit()


def _token(client: TestClient, email: str) -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Sethia Metal Store", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _mk(client: TestClient, h: dict[str, str], name: str, **extra: object) -> dict:
    r = client.post("/api/items", headers=h, json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------
# create + dedupe
# --------------------------------------------------------------------------


def test_create_starts_unconfirmed(client: TestClient) -> None:
    h = _h(_token(client, "it-1@x.example.com"))
    it = _mk(client, h, "SS 304 Patta 4in 2mm", metal="SS", shape="patta", grade="304")
    assert it["status"] == "unconfirmed"
    assert it["source"] == "manual"
    assert it["metal"] == "SS" and it["grade"] == "304"
    assert it["name_normalized"]  # non-empty dedupe key


def test_normalized_key_collision_409(client: TestClient) -> None:
    h = _h(_token(client, "it-2@x.example.com"))
    _mk(client, h, "MS Angle 40x40x5")
    # different spacing / case around the dimensions -> same normalized key
    r = client.post("/api/items", headers=h, json={"name": "ms  angle  40 X 40 X 5"})
    assert r.status_code == 409
    assert "existing item" in r.text.lower()


def test_price_band_must_be_ordered(client: TestClient) -> None:
    h = _h(_token(client, "it-3@x.example.com"))
    r = client.post(
        "/api/items",
        headers=h,
        json={"name": "Band Test", "price_min": "300.00", "price_max": "250.00"},
    )
    assert r.status_code == 422


def test_conversion_factor_must_be_positive(client: TestClient) -> None:
    h = _h(_token(client, "it-4@x.example.com"))
    r = client.post(
        "/api/items",
        headers=h,
        json={"name": "Pipe X", "secondary_uom": "pcs", "conversion_factor": "0"},
    )
    assert r.status_code == 422


# --------------------------------------------------------------------------
# HSN -> GST rate auto-fill
# --------------------------------------------------------------------------


def test_hsn_pick_fills_gst_rate(client: TestClient) -> None:
    h = _h(_token(client, "it-5@x.example.com"))
    it = _mk(client, h, "SS Sheet 304", hsn_code="72193590")
    assert float(it["gst_rate"]) == 18.0

    # change HSN -> rate follows
    upd = client.patch(f"/api/items/{it['id']}", headers=h, json={"hsn_code": "73239390"})
    assert upd.status_code == 200
    assert float(upd.json()["gst_rate"]) == 12.0


def test_rate_in_band_flag(client: TestClient) -> None:
    h = _h(_token(client, "it-6@x.example.com"))
    it = _mk(
        client,
        h,
        "Rate Band Co",
        default_rate="250.00",
        price_min="260.00",
        price_max="300.00",
    )
    full = client.get(f"/api/items/{it['id']}", headers=h).json()
    assert full["rate_in_band"] is False

    client.patch(f"/api/items/{it['id']}", headers=h, json={"default_rate": "275.00"})
    assert client.get(f"/api/items/{it['id']}", headers=h).json()["rate_in_band"] is True


# --------------------------------------------------------------------------
# list filters + search
# --------------------------------------------------------------------------


def test_list_filters(client: TestClient) -> None:
    h = _h(_token(client, "it-7@x.example.com"))
    a = _mk(client, h, "SS Utensil", item_type="mrp", hsn_code="73239390")
    _mk(client, h, "MS Angle 50x50", item_type="bulk")  # no HSN

    client.post(f"/api/items/{a['id']}/confirm", headers=h)

    def names(qs: str) -> list[str]:
        return [i["name"] for i in client.get(f"/api/items{qs}", headers=h).json()]

    assert len(names("")) == 2
    assert names("?type=mrp") == ["SS Utensil"]
    assert names("?status=unconfirmed") == ["MS Angle 50x50"]
    assert names("?no_hsn=true") == ["MS Angle 50x50"]


def test_search_by_name_grade_size(client: TestClient) -> None:
    h = _h(_token(client, "it-8@x.example.com"))
    _mk(client, h, "SS 304 Patta 4in 2mm", grade="304", size_text="4in")
    _mk(client, h, "MS Angle 40x40x5", size_text="40x40x5")

    assert [i["name"] for i in client.get("/api/items?q=patta", headers=h).json()] == [
        "SS 304 Patta 4in 2mm"
    ]
    assert [i["name"] for i in client.get("/api/items?q=40x40", headers=h).json()] == [
        "MS Angle 40x40x5"
    ]
    assert [i["name"] for i in client.get("/api/items?q=304", headers=h).json()] == [
        "SS 304 Patta 4in 2mm"
    ]


def test_search_is_synonym_aware(client: TestClient) -> None:
    """Typing a synonym / spelling variant finds an item stored under the
    canonical token — register seeds the bartan dictionary, so 'karahi',
    'kadhai' and 'kadai' all resolve to the same normalized key. SQLite here:
    this is the normalized-substring rung, not trigram."""
    h = _h(_token(client, "it-syn@x.example.com"))
    _mk(client, h, "SS Kadai 10")
    _mk(client, h, "07 Fancy Mor Jhula")

    for term in ("kadai", "kadhai", "karahi", "karai"):
        names = [i["name"] for i in client.get(f"/api/items?q={term}", headers=h).json()]
        assert names == ["SS Kadai 10"], f"{term!r} -> {names}"

    for term in ("jhula", "jhoola", "zhula"):
        names = [i["name"] for i in client.get(f"/api/items?q={term}", headers=h).json()]
        assert names == ["07 Fancy Mor Jhula"], f"{term!r} -> {names}"


def test_search_multi_word_narrows(client: TestClient) -> None:
    """A second word AND-filters the normalized rung — more text narrows,
    not widens. (Regression: every clause was OR'd, so extra words did
    nothing / re-widened via word_similarity.)"""
    h = _h(_token(client, "it-narrow@x.example.com"))
    _mk(client, h, "SS Topia 10")
    _mk(client, h, "Steel Topia Large")
    _mk(client, h, "Aluminium Toaster")

    one = [i["name"] for i in client.get("/api/items?q=topia", headers=h).json()]
    assert set(one) == {"SS Topia 10", "Steel Topia Large"}  # not the toaster

    two = [i["name"] for i in client.get("/api/items?q=topia+steel", headers=h).json()]
    assert two == ["Steel Topia Large"]  # both words required

    none = client.get("/api/items?q=topia+brass", headers=h).json()
    assert none == []  # no item has both tokens


# --------------------------------------------------------------------------
# confirm / merge / delete
# --------------------------------------------------------------------------


def test_confirm_moves_out_of_unconfirmed(client: TestClient) -> None:
    h = _h(_token(client, "it-9@x.example.com"))
    it = _mk(client, h, "Confirm Me")
    r = client.post(f"/api/items/{it['id']}/confirm", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert client.get("/api/items?status=unconfirmed", headers=h).json() == []


def test_merge_aliases_and_hides_loser(client: TestClient) -> None:
    h = _h(_token(client, "it-10@x.example.com"))
    winner = _mk(client, h, "SS 304 Patta 4in 2mm")
    loser = _mk(client, h, "SS Patti 4\" 2mm")

    r = client.post(
        f"/api/items/{loser['id']}/merge", headers=h, json={"target_id": winner["id"]}
    )
    assert r.status_code == 200

    # loser hidden from the default list
    names = {i["name"] for i in client.get("/api/items", headers=h).json()}
    assert "SS Patti 4\" 2mm" not in names and "SS 304 Patta 4in 2mm" in names

    # loser's normalized wording now resolves to the winner
    res = client.post(
        "/api/items/resolve",
        headers=h,
        params={"description": "SS Patti 4\" 2mm"},
    ).json()
    assert res["method"] == "alias"
    assert res["candidates"][0]["id"] == winner["id"]


def test_merge_into_self_422(client: TestClient) -> None:
    h = _h(_token(client, "it-11@x.example.com"))
    it = _mk(client, h, "Solo Item")
    r = client.post(f"/api/items/{it['id']}/merge", headers=h, json={"target_id": it["id"]})
    assert r.status_code == 422


def test_delete_unbilled_item(client: TestClient) -> None:
    h = _h(_token(client, "it-12@x.example.com"))
    it = _mk(client, h, "Deletable Item")
    assert client.delete(f"/api/items/{it['id']}", headers=h).status_code == 204
    assert client.get(f"/api/items/{it['id']}", headers=h).status_code == 404


# --------------------------------------------------------------------------
# reference lookups
# --------------------------------------------------------------------------


def test_reference_lookups(client: TestClient) -> None:
    h = _h(_token(client, "it-13@x.example.com"))
    assert "kg" in client.get("/api/reference/uoms", headers=h).json()
    assert "SS" in client.get("/api/reference/metals", headers=h).json()
    assert "angle" in client.get("/api/reference/shapes", headers=h).json()
    assert any("Stainless" in c for c in client.get("/api/reference/categories", headers=h).json())

    hsn = client.get("/api/reference/hsn?q=stainless", headers=h).json()
    assert any(row["code"] == "72193590" for row in hsn)
    assert all("gst_rate" in row for row in hsn)

    by_code = client.get("/api/reference/hsn?q=7323", headers=h).json()
    assert by_code and by_code[0]["code"].startswith("7323")


def test_resolve_exact_and_new(client: TestClient) -> None:
    h = _h(_token(client, "it-14@x.example.com"))
    it = _mk(client, h, "SS Utensil")

    hit = client.post(
        "/api/items/resolve", headers=h, params={"description": "ss utensil"}
    ).json()
    assert hit["method"] == "exact"
    assert hit["candidates"][0]["id"] == it["id"]

    miss = client.post(
        "/api/items/resolve", headers=h, params={"description": "brand new widget xyz"}
    ).json()
    assert miss["method"] is None and miss["candidates"] == []
