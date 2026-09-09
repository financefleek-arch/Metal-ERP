"""Tally Connector (F1a) — masters-in via the companion agent.

Operator-only: routes sit under `/api/admin/firms/{firm_id}/tally/*` behind
`require_platform_admin`, same shape as the firm-whatsapp routes. The
connector reuses the existing `staging_tally_party` / `staging_tally_item`
tables + review/commit flow; the new pieces are `tally_company`, the
`pull_masters` sync job, the agent result + status callbacks, and the lazy
auto-cancel of a wedged pull.

R2 is monkeypatched — `app.backup_storage.get_object` returns a fixture, no
network. `pull_mod.get_object` (used when staging) and the router's own
`get_object` (used by the XML download) are both patched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.routers.tally as tally_router
import app.services.tally.installer as installer_mod
import app.services.tally.pull as pull_mod
from app.db import SessionLocal
from app.main import app
from app.models import (
    AgentOutboxItem,
    StagingTallyItem,
    StagingTallyParty,
    TallyCompany,
    TallySyncJob,
    Tenant,
)
from tools.make_platform_admin import run as make_admin

_FIXTURE = Path(__file__).parent / "fixtures" / "tally_connector_pull_sample.xml"
_MASTERS_XML = _FIXTURE.read_bytes()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _stub_r2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pull_mod, "get_object", lambda r2_key: _MASTERS_XML)
    monkeypatch.setattr(tally_router, "get_object", lambda r2_key: _MASTERS_XML)


_last_built_key: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _stub_installer_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provisioning now also builds + caches an installer zip — stub that
    out here (no real agent build dir / R2 in tests) so it behaves as a
    successful no-op, same shape as `build_and_cache_installer`. The
    plaintext key no longer comes back in the HTTP response (it's baked
    into the zip), so tests that need it read `_last_built_key`."""
    _last_built_key.clear()

    def _fake_build(session, shop, *, plaintext_key):  # type: ignore[no-untyped-def]
        shop.installer_r2_key = f"installers/{shop.id}.zip"
        session.flush()
        _last_built_key[shop.id] = plaintext_key
        return shop.installer_r2_key

    monkeypatch.setattr(installer_mod, "build_and_cache_installer", _fake_build)
    import app.routers.admin as admin_router

    monkeypatch.setattr(admin_router, "get_object", lambda r2_key: b"PK\x03\x04fake-zip")


def _mark_agent_healthy(shop_id: str, *, tally_status: str = "connected") -> None:
    """Simulate the agent's first checkin so `pull-masters`'s reachability
    gate (services/tally/agent_health.py) doesn't 409 in tests that don't
    care about that gate specifically — a real pull is always preceded by at
    least one checkin in practice."""
    from datetime import UTC, datetime

    from app.models import BackupShop

    with SessionLocal() as s:
        shop = s.get(BackupShop, shop_id)
        assert shop is not None
        shop.last_checkin_at = datetime.now(UTC)
        shop.last_tally_status = tally_status
        if tally_status == "connected":
            shop.last_tally_ok_at = datetime.now(UTC)
        s.commit()


def _admin_token(client: TestClient, email: str = "ops@f1a.example.com") -> str:
    with SessionLocal() as s:
        make_admin(s, email=email, password="ops-s3cret-pass")
        s.commit()
    r = client.post(
        "/api/auth/login", json={"email": email, "password": "ops-s3cret-pass"}
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _make_firm(client: TestClient, token: str, name: str = "F1a Traders") -> str:
    r = client.post(
        "/api/admin/firms", headers=_auth(token), json={"legal_name": name}
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _provision_agent(client: TestClient, tok: str, firm_id: str) -> tuple[str, str]:
    """Provision the firm's companion agent via the admin UI endpoint.
    Returns (shop_id, plaintext_api_key). The key is baked into the cached
    installer rather than echoed in the response — read it back from the
    stubbed builder."""
    r = client.post(
        f"/api/admin/firms/{firm_id}/tally-shop", headers=_auth(tok)
    )
    assert r.status_code == 201, r.text
    shop_id = r.json()["shop_id"]
    return shop_id, _last_built_key[shop_id]


def _base(firm_id: str) -> str:
    return f"/api/admin/firms/{firm_id}/tally"


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------


def test_routes_require_platform_admin(client: TestClient) -> None:
    reg = client.post(
        "/api/auth/register",
        json={"firm_name": "Nobody", "email": "n@x.example.com", "password": "pw123456"},
    )
    tok = reg.json()["access_token"]
    with SessionLocal() as s:
        firm_id = s.scalar(select(Tenant.id))
    r = client.get(f"{_base(firm_id)}/company", headers=_auth(tok))
    assert r.status_code == 403
    r = client.get(f"{_base(firm_id)}/company")
    assert r.status_code == 401


# --------------------------------------------------------------------------
# company + ledger map
# --------------------------------------------------------------------------


def test_company_crud_and_ledger_map(client: TestClient) -> None:
    tok = _admin_token(client)
    firm_id = _make_firm(client, tok)
    _provision_agent(client, tok, firm_id)

    assert client.get(f"{_base(firm_id)}/company", headers=_auth(tok)).status_code == 404

    r = client.post(
        f"{_base(firm_id)}/company",
        headers=_auth(tok),
        json={"company_name": "M/s F1a Traders"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["company_name"] == "M/s F1a Traders"
    assert r.json()["transport"] == "file"
    assert r.json()["ledger_map"] == {}

    r = client.put(
        f"{_base(firm_id)}/company/ledger-map",
        headers=_auth(tok),
        json={"debtors_parent": "Sundry Debtors", "sales_ledger": "Sales @ 18%"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ledger_map"] == {
        "debtors_parent": "Sundry Debtors",
        "sales_ledger": "Sales @ 18%",
    }
    r = client.put(
        f"{_base(firm_id)}/company/ledger-map",
        headers=_auth(tok),
        json={"sales_ledger": ""},
    )
    assert r.json()["ledger_map"] == {"debtors_parent": "Sundry Debtors"}

    r = client.put(
        f"{_base(firm_id)}/company/ledger-map",
        headers=_auth(tok),
        json={"not_a_slot": "x"},
    )
    assert r.status_code == 422


def test_pull_needs_a_provisioned_agent(client: TestClient) -> None:
    tok = _admin_token(client)
    firm_id = _make_firm(client, tok)
    # company registered before the agent exists -> shop_id stays null
    r = client.post(
        f"{_base(firm_id)}/company",
        headers=_auth(tok),
        json={"company_name": "X"},
    )
    assert r.status_code == 200
    assert r.json()["shop_id"] is None
    r = client.post(f"{_base(firm_id)}/pull-masters", headers=_auth(tok))
    assert r.status_code == 422
    assert "companion-agent" in r.json()["detail"]


# --------------------------------------------------------------------------
# pull-masters -> outbox -> checkin -> result callback
# --------------------------------------------------------------------------


def _link_company(client: TestClient, tok: str, firm_id: str) -> tuple[str, str]:
    """Provision the agent, register the company, and simulate a healthy
    first checkin (a real pull is always preceded by one). Returns
    (shop_id, key)."""
    shop_id, key = _provision_agent(client, tok, firm_id)
    r = client.post(
        f"{_base(firm_id)}/company",
        headers=_auth(tok),
        json={"company_name": "M/s F1a Traders"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["shop_id"] == shop_id
    _mark_agent_healthy(shop_id)
    return shop_id, key


def test_full_pull_flow(client: TestClient) -> None:
    tok = _admin_token(client)
    firm_id = _make_firm(client, tok)
    shop_id, key = _link_company(client, tok, firm_id)

    r = client.post(f"{_base(firm_id)}/pull-masters", headers=_auth(tok))
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "queued"
    job_id = job["id"]

    with SessionLocal() as s:
        outbox = s.scalar(
            select(AgentOutboxItem).where(AgentOutboxItem.shop_id == shop_id)
        )
        assert outbox is not None
        assert outbox.module == "tally"
        assert outbox.payload["action"] == "pull_masters"
        assert outbox.payload["job_id"] == job_id

    # a second pull is blocked while one is in flight
    r = client.post(f"{_base(firm_id)}/pull-masters", headers=_auth(tok))
    assert r.status_code == 409

    # agent checkin picks it up (flips item -> sent, job -> sent, once)
    r = client.post("/api/tally-agent/checkin", headers={"X-Shop-Key": key}, json={})
    assert r.status_code == 200
    assert len(r.json()["outbox"]) == 1
    r2 = client.post("/api/tally-agent/checkin", headers={"X-Shop-Key": key}, json={})
    assert r2.json()["outbox"] == []
    with SessionLocal() as s:
        assert s.get(TallySyncJob, job_id).status == "sent"

    # agent uploads XML + posts the result
    r = client.post(
        f"/api/tally-agent/jobs/{job_id}/result",
        headers={"X-Shop-Key": key},
        json={"status": "ok", "r2_key": f"masters/{shop_id}/x.xml"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["counts"]["ledgers"] == 3
    assert body["counts"]["items"] == 2
    batch_id = body["batch_id"]

    with SessionLocal() as s:
        parties = s.scalars(
            select(StagingTallyParty).where(StagingTallyParty.batch_id == batch_id)
        ).all()
        assert {p.ledger_name for p in parties} == {
            "Anand Metals",
            "Beecee Advance Co",
            "Chhotu Supplies",
        }
        assert all(p.sync_job_id == job_id for p in parties)
        items = s.scalars(
            select(StagingTallyItem).where(StagingTallyItem.batch_id == batch_id)
        ).all()
        assert {i.stock_name for i in items} == {
            "SS 202 Patta 3in 1.5mm",
            "SS Thali 11in",
        }
        company = s.scalar(select(TallyCompany))
        assert company.last_masters_pull_at is not None
        names = {k["name"] for k in company.known_ledgers}
        assert "Sales @ 18%" in names and "Sundry Debtors" in names

    # still in flight until the batch is reviewed/committed
    r = client.post(f"{_base(firm_id)}/pull-masters", headers=_auth(tok))
    assert r.status_code == 409
    assert "waiting for review" in r.json()["detail"]

    # sync-jobs list defaults to 10, newest first
    r = client.get(f"{_base(firm_id)}/sync-jobs", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()[0]["id"] == job_id


def test_pull_blocked_when_agent_reports_tally_unreachable(client: TestClient) -> None:
    tok = _admin_token(client)
    firm_id = _make_firm(client, tok)
    shop_id, key = _link_company(client, tok, firm_id)

    # a fresh 'refused' checkin blocks the pull with an actionable message
    r = client.post(
        "/api/tally-agent/checkin",
        headers={"X-Shop-Key": key},
        json={"tally_reachable": False, "tally_reason": "refused"},
    )
    assert r.status_code == 200
    r = client.post(f"{_base(firm_id)}/pull-masters", headers=_auth(tok))
    assert r.status_code == 409
    assert "acts as Server" in r.json()["detail"]

    # once the agent reports connected, the pull proceeds
    r = client.post(
        "/api/tally-agent/checkin",
        headers={"X-Shop-Key": key},
        json={"tally_reachable": True},
    )
    assert r.status_code == 200
    r = client.post(f"{_base(firm_id)}/pull-masters", headers=_auth(tok))
    assert r.status_code == 201, r.text


def test_pull_blocked_when_agent_offline(client: TestClient) -> None:
    from datetime import UTC, datetime, timedelta

    tok = _admin_token(client)
    firm_id = _make_firm(client, tok)
    shop_id, key = _link_company(client, tok, firm_id)

    # age the last checkin past the offline window
    from app.models import BackupShop

    with SessionLocal() as s:
        shop = s.get(BackupShop, shop_id)
        shop.last_checkin_at = datetime.now(UTC) - timedelta(minutes=10)
        s.commit()

    r = client.post(f"{_base(firm_id)}/pull-masters", headers=_auth(tok))
    assert r.status_code == 409
    assert "offline" in r.json()["detail"]


def test_result_error_path_and_idempotency(client: TestClient) -> None:
    tok = _admin_token(client)
    firm_id = _make_firm(client, tok)
    shop_id, key = _link_company(client, tok, firm_id)

    r = client.post(f"{_base(firm_id)}/pull-masters", headers=_auth(tok))
    job_id = r.json()["id"]

    r = client.post(
        f"/api/tally-agent/jobs/{job_id}/result",
        headers={"X-Shop-Key": key},
        json={"status": "error", "error": "no export file in folder"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "error"

    # second result on a terminal job is a no-op
    r = client.post(
        f"/api/tally-agent/jobs/{job_id}/result",
        headers={"X-Shop-Key": key},
        json={"status": "ok", "r2_key": f"masters/{shop_id}/x.xml"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "error"

    # failed job frees the tenant to retry
    r = client.post(f"{_base(firm_id)}/pull-masters", headers=_auth(tok))
    assert r.status_code == 201


def test_result_rejects_wrong_shop(client: TestClient) -> None:
    tok = _admin_token(client)
    firm_id = _make_firm(client, tok)
    shop_a, _ka = _link_company(client, tok, firm_id)
    # a second, unrelated firm + its own agent
    firm_b = _make_firm(client, tok, name="Other Firm")
    _sb, key_b = _provision_agent(client, tok, firm_b)

    r = client.post(f"{_base(firm_id)}/pull-masters", headers=_auth(tok))
    job_id = r.json()["id"]

    r = client.post(
        f"/api/tally-agent/jobs/{job_id}/result",
        headers={"X-Shop-Key": key_b},
        json={"status": "ok", "r2_key": "x"},
    )
    assert r.status_code == 403


def test_missing_r2_key_fails_job(client: TestClient) -> None:
    tok = _admin_token(client)
    firm_id = _make_firm(client, tok)
    shop_id, key = _link_company(client, tok, firm_id)

    r = client.post(f"{_base(firm_id)}/pull-masters", headers=_auth(tok))
    job_id = r.json()["id"]

    r = client.post(
        f"/api/tally-agent/jobs/{job_id}/result",
        headers={"X-Shop-Key": key},
        json={"status": "ok"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "error"
    assert "r2_key" in r.json()["error"]


# --------------------------------------------------------------------------
# agent status ping + lazy auto-cancel
# --------------------------------------------------------------------------


def test_status_ping_records_and_stays_non_terminal(client: TestClient) -> None:
    tok = _admin_token(client)
    firm_id = _make_firm(client, tok)
    shop_id, key = _link_company(client, tok, firm_id)

    r = client.post(f"{_base(firm_id)}/pull-masters", headers=_auth(tok))
    job_id = r.json()["id"]

    r = client.post(
        f"/api/tally-agent/jobs/{job_id}/status",
        headers={"X-Shop-Key": key},
        json={"agent_status": "no_company_loaded"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "sent"  # non-terminal

    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        assert job.last_agent_status == "no_company_loaded"
        assert job.last_agent_status_at is not None

    # an invalid status value is rejected
    r = client.post(
        f"/api/tally-agent/jobs/{job_id}/status",
        headers={"X-Shop-Key": key},
        json={"agent_status": "whatever"},
    )
    assert r.status_code == 422


def test_stuck_pull_is_lazily_auto_cancelled(client: TestClient) -> None:
    tok = _admin_token(client)
    firm_id = _make_firm(client, tok)
    shop_id, key = _link_company(client, tok, firm_id)

    r = client.post(f"{_base(firm_id)}/pull-masters", headers=_auth(tok))
    job_id = r.json()["id"]
    client.post(
        f"/api/tally-agent/jobs/{job_id}/status",
        headers={"X-Shop-Key": key},
        json={"agent_status": "tally_unavailable"},
    )

    # not old enough yet -> still 409
    r = client.post(f"{_base(firm_id)}/pull-masters", headers=_auth(tok))
    assert r.status_code == 409

    # age the job past the window
    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        job.created_at = datetime.now(UTC) - timedelta(minutes=15)
        s.commit()

    # now a fresh pull expires the wedged one and proceeds
    r = client.post(f"{_base(firm_id)}/pull-masters", headers=_auth(tok))
    assert r.status_code == 201
    new_job_id = r.json()["id"]
    assert new_job_id != job_id
    with SessionLocal() as s:
        assert s.get(TallySyncJob, job_id).status == "error"


# --------------------------------------------------------------------------
# XML download for a failed job
# --------------------------------------------------------------------------


def test_download_xml_for_failed_job(client: TestClient) -> None:
    tok = _admin_token(client)
    firm_id = _make_firm(client, tok)
    shop_id, key = _link_company(client, tok, firm_id)

    r = client.post(f"{_base(firm_id)}/pull-masters", headers=_auth(tok))
    job_id = r.json()["id"]

    # a job that uploaded then failed to parse: give it an r2_key, mark error
    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        job.status = "error"
        job.r2_key = f"masters/{shop_id}/x.xml"
        job.error = "could not parse the Tally XML: something"
        s.commit()

    r = client.get(f"{_base(firm_id)}/sync-jobs/{job_id}/xml", headers=_auth(tok))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert b"<LEDGER" in r.content

    # a job with no r2_key -> 404
    r2 = client.post(f"{_base(firm_id)}/pull-masters", headers=_auth(tok))
    # (previous job is terminal, so this succeeds)
    jid2 = r2.json()["id"]
    r = client.get(f"{_base(firm_id)}/sync-jobs/{jid2}/xml", headers=_auth(tok))
    assert r.status_code == 404
