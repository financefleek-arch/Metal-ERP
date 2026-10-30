"""S0.4 - know which Tally company this really is (gap A2).

Live finding (2026-10-30): the Company collection answers with name, GUID and books-from date, needs no
company selected, and lists only what that Tally process has open (services/tally/company_verify.py).
"""

# ruff: noqa: F811
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AgentOutboxItem, TallyCompany, TallySyncJob
from app.services.tally.company_verify import (
    OpenCompany,
    VerifyFailed,
    build_request,
    judge,
    parse_companies,
    process_verify_result,
)
from app.services.tally.voucher_capture import is_export_request
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _h,
    _setup_pushable_invoice,
    _stub_installer_build,  # autouse: no real agent build in tests
    client,
)


def _answer(*companies: tuple[str, str, str]) -> bytes:
    body = "".join(
        f'<COMPANY NAME="{n}" RESERVEDNAME=""><STARTINGFROM TYPE="Date">{d}</STARTINGFROM>'
        f'<BOOKSFROM TYPE="Date">{d}</BOOKSFROM><NAME TYPE="String">{n}</NAME>'
        f'<GUID TYPE="String">{g}</GUID><MASTERID TYPE="Number"> 29</MASTERID></COMPANY>'
        for n, g, d in companies
    )
    return (
        "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER><BODY>"
        "<DESC><CMPINFO><COMPANY>0</COMPANY><LEDGER>27</LEDGER></CMPINFO></DESC>"
        f"<DATA><COLLECTION>{body}</COLLECTION></DATA></BODY></ENVELOPE>"
    ).encode()


FLEEK = ("Fleek", "167b31a2-6486-4396-8ee8-7fe38496052d", "20260401")
OTHER = ("Fleek Test", "67baeb49-5454-418e-aad2-4ae28441e6d1", "20260401")


def _company(name: str = "Fleek", guid: str | None = None) -> TallyCompany:
    return TallyCompany(tenant_id="t", company_name=name, company_guid=guid, ledger_map={})


# --------------------------------------------------------------------------
# request + parser
# --------------------------------------------------------------------------


def test_request_is_a_read_only_export_that_names_no_company() -> None:
    xml = build_request()
    assert is_export_request(xml)
    assert "SVCURRENTCOMPANY" not in xml


def test_parser_reads_name_guid_and_books_from_and_skips_the_counter() -> None:
    got = parse_companies(_answer(FLEEK))
    assert got == [OpenCompany("Fleek", FLEEK[1], date(2026, 4, 1))]


def test_parser_returns_nothing_when_no_company_is_open() -> None:
    assert parse_companies(_answer()) == []


def test_parser_raises_when_tally_refused_or_sent_junk() -> None:
    refused = (
        b"<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>0</STATUS></HEADER>"
        b"<BODY><DATA><LINEERROR>nope</LINEERROR></DATA></BODY></ENVELOPE>"
    )
    with pytest.raises(VerifyFailed, match="nope"):
        parse_companies(refused)
    with pytest.raises(VerifyFailed):
        parse_companies(b"not xml")


# --------------------------------------------------------------------------
# the judgement
# --------------------------------------------------------------------------


def test_first_time_matches_by_name_and_learns_the_guid() -> None:
    state, _note, matched = judge(_company("fleek"), parse_companies(_answer(FLEEK, OTHER)))
    assert state == "verified" and matched.guid == FLEEK[1]


def test_nothing_matching_open_says_what_is_open() -> None:
    state, note, matched = judge(_company("Shah Traders"), parse_companies(_answer(FLEEK, OTHER)))
    assert state == "not_open" and matched is None
    assert '"Fleek"' in note and '"Fleek Test"' in note


def test_same_guid_same_name_is_verified() -> None:
    state, _n, _m = judge(_company("Fleek", FLEEK[1]), parse_companies(_answer(FLEEK)))
    assert state == "verified"


def test_same_guid_new_name_is_a_rename() -> None:
    renamed = ("Fleek Traders", FLEEK[1], "20260401")
    state, note, matched = judge(_company("Fleek", FLEEK[1]), parse_companies(_answer(renamed)))
    assert state == "renamed" and matched.name == "Fleek Traders"
    assert "Fleek Traders" in note and "refuse" in note


def test_same_name_other_guid_is_a_different_company() -> None:
    impostor = ("Fleek", "00000000-0000-0000-0000-000000000000", "20250401")
    state, _n, _m = judge(_company("Fleek", FLEEK[1]), parse_companies(_answer(impostor)))
    assert state == "different"


def test_the_linked_company_being_closed_is_not_open() -> None:
    state, _n, _m = judge(_company("Fleek", FLEEK[1]), parse_companies(_answer(OTHER)))
    assert state == "not_open"


# --------------------------------------------------------------------------
# the flow: endpoint -> agent -> result
# --------------------------------------------------------------------------


def _tally_company(tenant_id: str) -> TallyCompany:
    with SessionLocal() as s:
        return s.scalar(select(TallyCompany).where(TallyCompany.tenant_id == tenant_id))


def _verify(tenant_id: str, monkeypatch: pytest.MonkeyPatch, raw: bytes) -> TallySyncJob:
    from app.services.tally.company_verify import enqueue_verify_company

    monkeypatch.setattr("app.services.tally.company_verify.get_object", lambda key: raw)
    with SessionLocal() as s:
        company = s.scalar(select(TallyCompany).where(TallyCompany.tenant_id == tenant_id))
        job = enqueue_verify_company(s, company)
        process_verify_result(s, job, ok=True, r2_key="k", agent_error=None)
        s.commit()
        return s.get(TallySyncJob, job.id)


def test_verify_queues_a_read_only_export_for_the_agent(client: TestClient) -> None:
    from app.services.tally.company_verify import enqueue_verify_company

    _tok, tenant_id, _iid, _shop = _setup_pushable_invoice(client, "cv1@x.example.com")
    with SessionLocal() as s:
        company = s.scalar(select(TallyCompany).where(TallyCompany.tenant_id == tenant_id))
        job = enqueue_verify_company(s, company)
        same = enqueue_verify_company(s, company)  # a double click
        s.commit()
        assert same.id == job.id
        outbox = s.get(AgentOutboxItem, job.outbox_item_id)
        assert outbox.payload["action"] == "pull_vouchers"
        assert is_export_request(outbox.payload["export_xml"])


def test_verify_stores_guid_and_books_from(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tok, tenant_id, _iid, _shop = _setup_pushable_invoice(client, "cv2@x.example.com")
    name = _tally_company(tenant_id).company_name
    job = _verify(tenant_id, monkeypatch, _answer((name, FLEEK[1], "20260401")))

    assert job.status == "ok" and job.counts["state"] == "verified"
    c = _tally_company(tenant_id)
    assert c.company_guid == FLEEK[1] and c.books_from == date(2026, 4, 1)
    assert c.verify_state == "verified" and c.verified_at is not None


def test_verify_flags_a_rename_and_keeps_the_stored_guid(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tok, tenant_id, _iid, _shop = _setup_pushable_invoice(client, "cv3@x.example.com")
    name = _tally_company(tenant_id).company_name
    _verify(tenant_id, monkeypatch, _answer((name, FLEEK[1], "20260401")))
    job = _verify(tenant_id, monkeypatch, _answer(("Brand New Name", FLEEK[1], "20260401")))

    assert job.counts["state"] == "renamed"
    c = _tally_company(tenant_id)
    assert c.verify_state == "renamed" and "Brand New Name" in c.verify_note
    assert c.company_name == name  # we do not rename the firm's link behind its back


def test_verify_with_nothing_open_changes_no_stored_guid(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tok, tenant_id, _iid, _shop = _setup_pushable_invoice(client, "cv4@x.example.com")
    name = _tally_company(tenant_id).company_name
    _verify(tenant_id, monkeypatch, _answer((name, FLEEK[1], "20260401")))
    job = _verify(tenant_id, monkeypatch, _answer(OTHER))

    assert job.counts["state"] == "not_open"
    c = _tally_company(tenant_id)
    assert c.company_guid == FLEEK[1] and c.verify_state == "not_open"


def test_an_agent_failure_is_a_job_error_and_leaves_the_company_alone(
    client: TestClient,
) -> None:
    from app.services.tally.company_verify import enqueue_verify_company

    _tok, tenant_id, _iid, _shop = _setup_pushable_invoice(client, "cv5@x.example.com")
    with SessionLocal() as s:
        company = s.scalar(select(TallyCompany).where(TallyCompany.tenant_id == tenant_id))
        job = enqueue_verify_company(s, company)
        process_verify_result(s, job, ok=False, r2_key=None, agent_error="Tally is not running")
        s.commit()
        assert job.status == "error" and "not running" in job.error
    assert _tally_company(tenant_id).verify_state is None
