"""Stage a Tally masters export that is too big for the live pull path.

The connector pull refuses an R2 object over 64 MB (`backup_storage`), and the
parser builds the whole tree in memory. This tool streams the big "All Masters"
file once, keeps only the elements the stagers read (GROUP, LEDGER, STOCKGROUP,
STOCKITEM), and feeds that slim file to the same `stage_masters_xml` /
`stage_stock_items_xml` the pull uses. The result is an ordinary review batch:
nothing is written to `party` / `item` until the firm commits it.

Run it in its OWN container so a big parse can't take the API down:

    # dry run — counts only, nothing written
    docker compose run --rm --no-deps metalerp-api \\
        python -m tools.backfill_masters_xml --tenant <id> \\
        --r2-key <agent upload key>

    # stage the review batch (replaces any uncommitted staged batch)
    docker compose run --rm --no-deps metalerp-api \\
        python -m tools.backfill_masters_xml --tenant <id> \\
        --r2-key <agent upload key> --apply

`--file <path>` reads a local file instead of R2.
"""

from __future__ import annotations

import argparse
import io
import uuid
from datetime import UTC, datetime

from lxml import etree

from tools.tally_import import parser as _parser
from tools.tally_import.parser import _decode, parse_masters, parse_stock_items


def _gst_rate_fixed(si: etree._Element) -> float | None:
    """The parser's fixed `_gst_rate` (a bare <RATE> is a price, not a tax rate).

    Patched in so this tool works against an image that predates the parser fix;
    harmless once the fix is deployed.
    """
    for sw in si.iter("STATEWISEDETAILS.LIST"):
        heads: dict[str, float] = {}
        for rd in sw.iter("RATEDETAILS.LIST"):
            head = (rd.findtext("GSTRATEDUTYHEAD") or "").strip().upper()
            rate = _parser._num(rd.findtext("GSTRATE"))
            if head and rate is not None:
                heads[head] = rate
        if "IGST" in heads:
            return heads["IGST"]
        if "CGST" in heads or "SGST/UTGST" in heads or "SGST" in heads:
            return (
                heads.get("CGST", 0.0)
                + heads.get("SGST/UTGST", heads.get("SGST", 0.0))
            ) or None
    for node in si.iter("GSTRATE"):
        r = _parser._num(node.text)
        if r is not None and 0 <= r <= 100:
            return r
    return None


_parser._gst_rate = _gst_rate_fixed

_WANTED = {"GROUP", "LEDGER", "STOCKGROUP", "STOCKITEM"}
_R2_CAP = 400 * 1024 * 1024


def slim(raw: bytes) -> bytes:
    """Stream `raw` and return a small envelope holding only the wanted masters."""
    data = _decode(raw)
    out: list[bytes] = []
    seen: dict[str, int] = {t: 0 for t in _WANTED}
    for _, msg in etree.iterparse(
        io.BytesIO(data), events=("end",), tag="TALLYMESSAGE", recover=True
    ):
        for child in msg:
            if child.tag in _WANTED:
                out.append(etree.tostring(child))
                seen[child.tag] += 1
        msg.clear()
        while msg.getprevious() is not None:
            del msg.getparent()[0]
    print(f"  streamed: {seen}")
    return b"<ENVELOPE><BODY><DATA>" + b"".join(out) + b"</DATA></BODY></ENVELOPE>"


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage an oversized Tally masters XML")
    ap.add_argument("--tenant", required=True, help="tenant (firm) id")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--r2-key", help="object key the agent uploaded")
    src.add_argument("--file", help="local masters XML path")
    ap.add_argument("--apply", action="store_true", help="stage (default: report only)")
    args = ap.parse_args()

    if args.r2_key:
        from app.backup_storage import get_object

        raw = get_object(args.r2_key, _R2_CAP)
    else:
        with open(args.file, "rb") as fh:
            raw = fh.read()
    print(f"read {len(raw) / 1_048_576:.1f} MB")

    small = slim(raw)
    del raw
    print(f"slim file: {len(small) / 1_048_576:.1f} MB")

    masters = parse_masters(small)
    stock = parse_stock_items(small)
    print(f"  ledgers {len(masters.ledgers)}, groups {len(masters.groups)}")
    print(f"  stock items {len(stock.items)}, stock groups {len(stock.groups)}")
    if not args.apply:
        print("\n(dry run - nothing written; pass --apply to stage)")
        return

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import TallyCompany
    from app.services.tally.staging import (
        NoLedgersError,
        NoStockItemsError,
        stage_masters_xml,
        stage_stock_items_xml,
    )

    batch_id = str(uuid.uuid4())
    with SessionLocal() as session:
        party_summary = None
        try:
            party_summary = stage_masters_xml(session, args.tenant, small, batch_id=batch_id)
            print(f"staged {party_summary.total} parties")
        except NoLedgersError:
            print("no ledgers in the file")
        try:
            item_summary = stage_stock_items_xml(session, args.tenant, small, batch_id=batch_id)
            print(
                f"staged {item_summary.total} items "
                f"({item_summary.dummies_skipped} zero-history dummies skipped)"
            )
        except NoStockItemsError:
            print("no stock items in the file")

        # same side effect as a real pull: feeds the ledger-map dropdowns
        company = session.scalar(
            select(TallyCompany).where(TallyCompany.tenant_id == args.tenant)
        )
        if company is not None:
            if party_summary is not None and party_summary.known_ledgers:
                company.known_ledgers = party_summary.known_ledgers
            company.last_masters_pull_at = datetime.now(UTC)
        session.commit()
    print(f"\n(committed) review batch {batch_id}")


if __name__ == "__main__":
    main()
