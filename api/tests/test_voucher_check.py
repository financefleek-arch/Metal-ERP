"""S0.6 - a retry must not post a voucher Tally may already hold (gap F1).

Live finding (2026-10-30): Tally does not reject a duplicate voucher, and a user-defined field cannot be
read back, but the standard Reference field can. So a push that ended without Tally answering is looked
up by its reference before it is posted again (services/tally/voucher_check.py).
"""

# ruff: noqa: F811
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AgentOutboxItem, TallyLink, TallySyncJob
from app.services.invoices.tally_xml import (
    build_sales_voucher_envelope,
    sales_reference,
)
from app.services.tally.push import process_push_result
from app.services.tally.voucher_capture import is_export_request
from app.services.tally.voucher_check import (
    CheckFailed,
    build_reference_request,
    parse_reference_result,
    process_check_result,
)
from tests.test_invoice_tally_push import _invoice, _item, _line, _party  # noqa: F401
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _h,
    _setup_pushable_invoice,
    _stub_installer_build,  # autouse: no real agent build in tests
    client,
)


def _answer(reference: str, *, number: str = "12", guid: str = "g-1", master: str = "27",
            vtype: str = "Sales", party: str = "Anand Metals", status: str = "1") -> bytes:
    return (
        f"<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>{status}</STATUS></HEADER><BODY><DATA>"
        "<COLLECTION><VOUCHER>"
        f"<VOUCHERTYPENAME>{vtype}</VOUCHERTYPENAME><VOUCHERNUMBER>{number}</VOUCHERNUMBER>"
        f"<REFERENCE>{reference}</REFERENCE><PARTYLEDGERNAME>{party}</PARTYLEDGERNAME>"
        f"<GUID>{guid}</GUID><MASTERID> {master}</MASTERID>"
        "</VOUCHER></COLLECTION></DATA></BODY></ENVELOPE>"
    ).encode()


_EMPTY = (
    b"<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>"
    b"<BODY><DATA><COLLECTION/></DATA></BODY></ENVELOPE>"
)


# --------------------------------------------------------------------------
# the reference, the request, the parser
# --------------------------------------------------------------------------


def _inv(series: str = "Sales"):
    item = _item("X")
    line = _line(1, item, qty="1", rate="1", line_total="1")
    inv = _invoice([line], number=42, grand_total="1", round_off="0")
    inv.series = series
    inv.date = date(2026, 10, 1)
    return inv


def test_the_default_series_reference_reads_as_our_invoice_number() -> None:
    assert sales_reference(_inv()) == "ME/2026-27/42"


def test_another_series_is_part_of_the_reference() -> None:
    assert sales_reference(_inv("Proforma")) == "ME/Proforma/2026-27/42"


def test_the_sales_voucher_carries_the_reference() -> None:
    env = build_sales_voucher_envelope(_inv(), _party(), {"sales_ledger": "Sales Accounts"})
    assert env.findtext(".//VOUCHER/REFERENCE") == "ME/2026-27/42"


def test_request_is_a_read_only_export_for_that_day_and_company() -> None:
    xml = build_reference_request("Shah & Sons", date(2026, 10, 1), "ME/2026-27/42")
    assert is_export_request(xml)
    assert "<SVCURRENTCOMPANY>Shah &amp; Sons</SVCURRENTCOMPANY>" in xml
    assert "<SVFROMDATE>20261001</SVFROMDATE><SVTODATE>20261001</SVTODATE>" in xml
    assert '$Reference = "ME/2026-27/42"' in xml


def test_a_reference_with_a_double_quote_is_refused_not_injected() -> None:
    with pytest.raises(ValueError):
        build_reference_request("Fleek", date(2026, 10, 1), 'A" OR 1=1')


def test_parser_finds_the_voucher_by_exact_reference() -> None:
    got = parse_reference_result(_answer("ME/2026-27/42"), "ME/2026-27/42", voucher_type="Sales")
    assert [(v.guid, v.master_id, v.number) for v in got] == [("g-1", "27", "12")]


def test_parser_ignores_other_references_types_and_parties() -> None:
    raw = _answer("ME/2026-27/43")
    assert parse_reference_result(raw, "ME/2026-27/42", voucher_type="Sales") == []
    assert parse_reference_result(_answer("R1", vtype="Purchase"), "R1", voucher_type="Sales") == []
    assert parse_reference_result(_answer("R1"), "R1", voucher_type="Sales", party="Someone Else") == []
    assert parse_reference_result(_EMPTY, "R1", voucher_type="Sales") == []


def test_parser_raises_when_tally_refused() -> None:
    refused = (
        b"<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>0</STATUS></HEADER><BODY><DATA>"
        b"<LINEERROR>Could not set 'SVCurrentCompany' to 'Fleek Test'</LINEERROR></DATA></BODY></ENVELOPE>"
    )
    with pytest.raises(CheckFailed, match="SVCurrentCompany"):
        parse_reference_result(refused, "R1", voucher_type="Sales")
    with pytest.raises(CheckFailed):
        parse_reference_result(_answer("R1", status="0"), "R1", voucher_type="Sales")
    with pytest.raises(CheckFailed):
        parse_reference_result(b"not xml", "R1", voucher_type="Sales")


# --------------------------------------------------------------------------
# the retry flow
# --------------------------------------------------------------------------


def _lose_the_first_push(client: TestClient, tok: str, iid: str) -> str:
    """Push once, then let the job sit `sent` past the window as if the agent never answered."""
    r = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok))
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]
    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        job.status = "sent"
        job.created_at = datetime.now(UTC) - timedelta(minutes=20)
        s.commit()
    return job_id


def _jobs(iid: str, kind: str) -> list[TallySyncJob]:
    with SessionLocal() as s:
        return list(
            s.scalars(
                select(TallySyncJob).where(
                    TallySyncJob.entity_id == iid, TallySyncJob.kind == kind
                ).order_by(TallySyncJob.created_at)
            )
        )


def test_an_unanswered_push_makes_the_retry_check_tally_first(client: TestClient) -> None:
    tok, _tenant, iid, shop_id = _setup_pushable_invoice(client, "vc1@x.example.com")
    first = _lose_the_first_push(client, tok, iid)

    r = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok))
    assert r.status_code == 201, r.text
    check = r.json()
    assert check["kind"] == "check_voucher"  # nothing was posted again
    assert len(_jobs(iid, "push_sales")) == 1

    with SessionLocal() as s:
        assert s.get(TallySyncJob, first).outcome_unknown is True
        outbox = s.get(AgentOutboxItem, s.get(TallySyncJob, check["id"]).outbox_item_id)
        assert outbox.payload["action"] == "pull_vouchers"  # the agent's existing read-only action
        assert is_export_request(outbox.payload["export_xml"])
        assert "ME/" in outbox.payload["export_xml"]


def test_a_second_click_while_the_check_runs_does_not_queue_another(client: TestClient) -> None:
    tok, _t, iid, _s = _setup_pushable_invoice(client, "vc2@x.example.com")
    _lose_the_first_push(client, tok, iid)
    a = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok)).json()
    b = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok))
    assert b.status_code in (200, 201, 409)
    if b.status_code != 409:
        assert b.json()["id"] == a["id"]
    assert len(_jobs(iid, "check_voucher")) == 1


def test_found_in_tally_completes_the_earlier_push_and_posts_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tok, tenant_id, iid, _shop = _setup_pushable_invoice(client, "vc3@x.example.com")
    first = _lose_the_first_push(client, tok, iid)
    check = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok)).json()
    reference = check["counts"]["reference"]

    monkeypatch.setattr(
        "app.services.tally.voucher_check.get_object",
        lambda key: _answer(reference, guid="tally-guid-9", number="31"),
    )
    with SessionLocal() as s:
        process_check_result(s, s.get(TallySyncJob, check["id"]), ok=True, r2_key="k", agent_error=None)
        s.commit()

    with SessionLocal() as s:
        prior = s.get(TallySyncJob, first)
        assert prior.status == "ok" and prior.outcome_unknown is False
        done = s.get(TallySyncJob, check["id"])
        assert done.status == "ok" and done.counts["found"] is True and done.counts["voucher_number"] == "31"
        link = s.scalar(
            select(TallyLink).where(TallyLink.entity_id == iid, TallyLink.entity_type == "invoice")
        )
        assert link is not None and link.tally_guid == "tally-guid-9"
    assert len(_jobs(iid, "push_sales")) == 1  # no second voucher


def test_not_in_tally_lets_the_retry_go_ahead(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tok, _t, iid, _shop = _setup_pushable_invoice(client, "vc4@x.example.com")
    first = _lose_the_first_push(client, tok, iid)
    check = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok)).json()

    monkeypatch.setattr("app.services.tally.voucher_check.get_object", lambda key: _EMPTY)
    with SessionLocal() as s:
        process_check_result(s, s.get(TallySyncJob, check["id"]), ok=True, r2_key="k", agent_error=None)
        s.commit()

    pushes = _jobs(iid, "push_sales")
    assert len(pushes) == 2  # the retry now posts
    with SessionLocal() as s:
        assert s.get(TallySyncJob, first).outcome_unknown is False
        assert s.get(TallySyncJob, check["id"]).counts["resume"] == "queued"


def test_a_check_that_cannot_run_holds_the_retry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tok, _t, iid, _shop = _setup_pushable_invoice(client, "vc5@x.example.com")
    first = _lose_the_first_push(client, tok, iid)
    check = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok)).json()

    refused = (
        b"<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>0</STATUS></HEADER><BODY><DATA>"
        b"<LINEERROR>Could not set 'SVCurrentCompany' to 'Fleek'</LINEERROR></DATA></BODY></ENVELOPE>"
    )
    monkeypatch.setattr("app.services.tally.voucher_check.get_object", lambda key: refused)
    with SessionLocal() as s:
        process_check_result(s, s.get(TallySyncJob, check["id"]), ok=True, r2_key="k", agent_error=None)
        s.commit()

    with SessionLocal() as s:
        failed = s.get(TallySyncJob, check["id"])
        assert failed.status == "error" and "Nothing was posted again" in failed.error
        assert s.get(TallySyncJob, first).outcome_unknown is True  # still unknown
    assert len(_jobs(iid, "push_sales")) == 1

    # the next retry checks again rather than posting blind
    again = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok))
    assert again.status_code == 201 and again.json()["kind"] == "check_voucher"


def test_an_agent_failure_after_sending_is_unknown_but_a_tally_error_is_not(client: TestClient) -> None:
    tok, _t, iid, _shop = _setup_pushable_invoice(client, "vc6@x.example.com")
    job_id = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok)).json()["id"]
    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        process_push_result(s, job, ok=False, tally_response=None, agent_error="timed out")
        s.commit()
        assert job.outcome_unknown is True

    tok2, _t2, iid2, _shop2 = _setup_pushable_invoice(client, "vc7@x.example.com")
    job2 = client.post(f"/api/tally/invoices/{iid2}/push", headers=_h(tok2)).json()["id"]
    with SessionLocal() as s:
        job = s.get(TallySyncJob, job2)
        process_push_result(
            s, job, ok=True, agent_error=None,
            tally_response=(
                "<RESPONSE><LINEERROR>Ledger does not exist</LINEERROR><CREATED>0</CREATED></RESPONSE>"
            ),
        )
        s.commit()
        assert job.outcome_unknown is False
    # a definite Tally error retries straight away: nothing to check
    r = client.post(f"/api/tally/invoices/{iid2}/push", headers=_h(tok2))
    assert r.status_code == 201 and r.json()["kind"] == "push_sales"


def test_a_job_still_queued_when_it_expires_was_never_sent_so_nothing_to_check(client: TestClient) -> None:
    tok, _t, iid, _shop = _setup_pushable_invoice(client, "vc8@x.example.com")
    job_id = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok)).json()["id"]
    with SessionLocal() as s:
        s.get(TallySyncJob, job_id).created_at = datetime.now(UTC) - timedelta(minutes=20)
        s.commit()  # still `queued`: the agent never took it
    r = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok))
    assert r.status_code == 201 and r.json()["kind"] == "push_sales"
