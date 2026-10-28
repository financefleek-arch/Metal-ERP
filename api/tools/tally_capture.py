"""Capture a raw Tally voucher export from a connected shop (plan P0).

Run on the VPS:
    docker exec -it metalerp-api python -m tools.tally_capture vouchers --firm <firm_id> \\
        --from 2025-04-01 --to 2025-04-30
    docker exec -it metalerp-api python -m tools.tally_capture bills --firm <firm_id>
    docker exec -it metalerp-api python -m tools.tally_capture xml --firm <firm_id> \\
        --label my-probe --file request.xml
    docker exec -it metalerp-api python -m tools.tally_capture fetch <r2_key> --out /tmp/x.xml
    docker cp metalerp-api:/tmp/x.xml .

It queues ONE read-only Export request for the shop's agent, waits for the result, and prints
where the unparsed XML landed in R2 plus its size and the time Tally took. Nothing is parsed
or imported. The request is built here, never by a shop user, and the agent refuses anything
that is not an Export. The agent must be a build that knows `pull_vouchers`.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date

from sqlalchemy import select

from app import backup_storage
from app.db import SessionLocal
from app.models import TallyCompany, TallySyncJob
from app.services.tally.voucher_capture import (
    assert_no_capture_in_flight,
    build_bills_receivable_request,
    build_vouchers_request,
    enqueue_voucher_capture,
)

_POLL_SECONDS = 5
_DEFAULT_WAIT = 600


def _company(session, firm_id: str) -> TallyCompany:
    company = session.scalar(select(TallyCompany).where(TallyCompany.tenant_id == firm_id))
    if company is None:
        raise SystemExit(f"firm {firm_id}: no Tally company is set up")
    return company


def _queue_and_wait(firm_id: str, label: str, request_xml: str, wait: int) -> int:
    with SessionLocal() as session:
        company = _company(session, firm_id)
        assert_no_capture_in_flight(session, firm_id)
        job = enqueue_voucher_capture(session, company, label=label, request_xml=request_xml)
        job_id = job.id
        session.commit()
    print(f"queued job {job_id} for company '{company.company_name}' (label {label})")
    print("waiting for the agent (it checks in about once a minute) ...")

    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(_POLL_SECONDS)
        with SessionLocal() as session:
            job = session.get(TallySyncJob, job_id)
            if job.status == "ok":
                c = job.counts or {}
                print(f"ok  r2_key={job.r2_key}  bytes={c.get('bytes')}  ms={c.get('ms')}")
                return 0
            if job.status == "error":
                print(f"error: {job.error}", file=sys.stderr)
                return 1
            if job.last_agent_status:
                print(f"  agent says: {job.last_agent_status}")
    print(f"still not finished after {wait}s; job {job_id} left queued", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("vouchers", help="all vouchers in a date range")
    v.add_argument("--firm", required=True)
    v.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    v.add_argument("--to", dest="end", required=True, type=date.fromisoformat)
    v.add_argument("--wait", type=int, default=_DEFAULT_WAIT)

    b = sub.add_parser("bills", help="Bills Receivable (open bills of any age)")
    b.add_argument("--firm", required=True)
    b.add_argument("--as-on", type=date.fromisoformat, default=date.today())
    b.add_argument("--wait", type=int, default=_DEFAULT_WAIT)

    x = sub.add_parser("xml", help="send an Export request you wrote yourself")
    x.add_argument("--firm", required=True)
    x.add_argument("--label", required=True)
    x.add_argument("--file", required=True)
    x.add_argument("--wait", type=int, default=_DEFAULT_WAIT)

    f = sub.add_parser("fetch", help="download a captured file from R2")
    f.add_argument("r2_key")
    f.add_argument("--out", required=True)

    a = p.parse_args(argv)

    if a.cmd == "fetch":
        data = backup_storage.get_object(a.r2_key, max_bytes=512 * 1024 * 1024)
        with open(a.out, "wb") as fh:
            fh.write(data)
        print(f"wrote {len(data)} bytes to {a.out}")
        return 0

    with SessionLocal() as session:
        name = _company(session, a.firm).company_name

    if a.cmd == "vouchers":
        label = f"vouchers-{a.start:%Y%m%d}-{a.end:%Y%m%d}"
        xml = build_vouchers_request(name, a.start, a.end)
    elif a.cmd == "bills":
        label = f"bills-receivable-{a.as_on:%Y%m%d}"
        xml = build_bills_receivable_request(name, a.as_on)
    else:
        label = a.label
        with open(a.file, encoding="utf-8") as fh:
            xml = fh.read()
    return _queue_and_wait(a.firm, label, xml, a.wait)


if __name__ == "__main__":
    raise SystemExit(main())
