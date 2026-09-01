"""Field-level validation: phone, PAN, GSTIN, PIN, email, legal name, address.

Exercised through the party create endpoint (the same validators back tenant
and register). Each bad input must 422; each good input must 201 and come
back normalised.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.reference import _gstin_check_char


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _token(client: TestClient, email: str) -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Sethia Metal Store", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _post(client: TestClient, h: dict[str, str], **body: object):
    return client.post("/api/parties", headers=h, json={"legal_name": "Acme Co", **body})


def _valid_gstin(pan: str = "AAJCB1234K", state: str = "27", entity: str = "1") -> str:
    first14 = f"{state}{pan}{entity}Z"
    return first14 + _gstin_check_char(first14)


# --------------------------------------------------------------------------
# phone
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["abcd1234", "12345", "9999999999999999999", "98a7654321", "call-me"],
)
def test_phone_rejects_junk(client: TestClient, bad: str) -> None:
    h = _h(_token(client, f"ph-{abs(hash(bad))}@x.example.com"))
    r = _post(client, h, phone=bad)
    assert r.status_code == 422
    assert "phone" in r.text.lower()


def test_phone_normalises_indian_mobile(client: TestClient) -> None:
    h = _h(_token(client, "ph-ok@x.example.com"))
    r = _post(client, h, phone="98320 11223")
    assert r.status_code == 201
    assert r.json()["phone"] == "+919832011223"


def test_phone_keeps_country_code_form(client: TestClient) -> None:
    h = _h(_token(client, "ph-cc@x.example.com"))
    r = _post(client, h, phone="+91-33-2255-7788")
    assert r.status_code == 201
    assert r.json()["phone"] == "+913322557788"


# --------------------------------------------------------------------------
# PAN
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["ABCDE1234", "ABCDE12345", "12345ABCDF", "ABCD_1234X", "ABCDE1234XX"],
)
def test_pan_rejects_wrong_shape(client: TestClient, bad: str) -> None:
    h = _h(_token(client, f"pan-{abs(hash(bad))}@x.example.com"))
    r = _post(client, h, pan=bad)
    assert r.status_code == 422
    assert "pan" in r.text.lower()


def test_pan_uppercases(client: TestClient) -> None:
    h = _h(_token(client, "pan-ok@x.example.com"))
    r = _post(client, h, pan="abcpp7809d")
    assert r.status_code == 201
    assert r.json()["pan"] == "ABCPP7809D"


# --------------------------------------------------------------------------
# GSTIN
# --------------------------------------------------------------------------


def test_gstin_rejects_short(client: TestClient) -> None:
    h = _h(_token(client, "g-short@x.example.com"))
    assert _post(client, h, gstin="27ABCDE1234").status_code == 422


def test_gstin_rejects_bad_state_prefix(client: TestClient) -> None:
    h = _h(_token(client, "g-state@x.example.com"))
    # 25 is not an assigned state code
    r = _post(client, h, gstin=_valid_gstin(state="25"))
    assert r.status_code == 422
    assert "state" in r.text.lower()


def test_gstin_rejects_bad_check_digit(client: TestClient) -> None:
    h = _h(_token(client, "g-check@x.example.com"))
    good = _valid_gstin()
    bad = good[:-1] + ("A" if good[-1] != "A" else "B")
    r = _post(client, h, gstin=bad)
    assert r.status_code == 422
    assert "check digit" in r.text.lower()


def test_gstin_accepts_valid(client: TestClient) -> None:
    h = _h(_token(client, "g-ok@x.example.com"))
    g = _valid_gstin()
    r = _post(client, h, gstin=g.lower())
    assert r.status_code == 201
    assert r.json()["gstin"] == g


# --------------------------------------------------------------------------
# PIN code
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["12345", "1234567", "012345", "73400a", "73 4001"])
def test_pincode_rejects(client: TestClient, bad: str) -> None:
    h = _h(_token(client, f"pin-{abs(hash(bad))}@x.example.com"))
    r = _post(client, h, addresses=[{"type": "both", "pincode": bad}])
    assert r.status_code == 422
    assert "pin" in r.text.lower()


def test_pincode_accepts_six_digits(client: TestClient) -> None:
    h = _h(_token(client, "pin-ok@x.example.com"))
    r = _post(client, h, addresses=[{"type": "both", "pincode": "734001"}])
    assert r.status_code == 201
    assert r.json()["addresses"][0]["pincode"] == "734001"


# --------------------------------------------------------------------------
# email
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["not-an-email", "a@b", "@x.com", "spaces in@x.com"])
def test_email_rejects(client: TestClient, bad: str) -> None:
    h = _h(_token(client, f"em-{abs(hash(bad))}@x.example.com"))
    assert _post(client, h, email=bad).status_code == 422


# --------------------------------------------------------------------------
# legal name
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["A", "   ", "123456", "<script>", "Bad*Name", "x" * 141])
def test_legal_name_rejects(client: TestClient, bad: str) -> None:
    h = _h(_token(client, f"ln-{abs(hash(bad))}@x.example.com"))
    r = client.post("/api/parties", headers=h, json={"legal_name": bad})
    assert r.status_code == 422


@pytest.mark.parametrize(
    "ok",
    ["M/s S.K. Traders (P) Ltd", "R & R Steel Co.", "Jai-Ambe Metal Mart", "Shop @ Corner"],
)
def test_legal_name_accepts_business_punctuation(client: TestClient, ok: str) -> None:
    h = _h(_token(client, f"lnok-{abs(hash(ok))}@x.example.com"))
    r = client.post("/api/parties", headers=h, json={"legal_name": ok})
    assert r.status_code == 201, r.text


def test_legal_name_collapses_whitespace(client: TestClient) -> None:
    h = _h(_token(client, "ln-ws@x.example.com"))
    r = client.post("/api/parties", headers=h, json={"legal_name": "  Balaji    Traders  "})
    assert r.status_code == 201
    assert r.json()["legal_name"] == "Balaji Traders"


# --------------------------------------------------------------------------
# address line + city
# --------------------------------------------------------------------------


def test_address_line_length_capped(client: TestClient) -> None:
    h = _h(_token(client, "al-long@x.example.com"))
    r = _post(client, h, addresses=[{"type": "both", "line1": "x" * 121}])
    assert r.status_code == 422


def test_address_line_accepts_normal(client: TestClient) -> None:
    h = _h(_token(client, "al-ok@x.example.com"))
    addr = {"type": "both", "line1": "179/1/244 Agrasen Road, Ward-42", "city": "Siliguri"}
    r = _post(client, h, addresses=[addr])
    assert r.status_code == 201


@pytest.mark.parametrize("bad", ["Siliguri 5", "Town#2", "x" * 61])
def test_city_rejects(client: TestClient, bad: str) -> None:
    h = _h(_token(client, f"ct-{abs(hash(bad))}@x.example.com"))
    r = _post(client, h, addresses=[{"type": "both", "city": bad}])
    assert r.status_code == 422
