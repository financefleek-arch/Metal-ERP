"""Items to and from a spreadsheet: export a selection, edit it in Excel, import it back.

Rows are matched by the item's **code** (its SKU, else barcode), never by name, so names can be
changed in the sheet. The import updates existing items only: it never creates one and never
deletes one. A blank cell leaves the field as it is. Every row is reported (changed, unchanged,
or an error with the reason), and a dry run shows the same report without saving.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Item, ItemCategory
from app.models._mixins import Availability
from app.services.items import hsn_exists

HEADERS = [
    "Code",
    "Name",
    "Group",
    "Availability",
    "Selling price",
    "Pack qty",
    "Carton qty",
    "HSN",
    "GST %",
]
_AVAIL_LABELS = {
    "in stock": Availability.in_stock,
    "expected": Availability.expected,
    "on order": Availability.expected,
    "out of stock": Availability.out_of_stock,
    "discontinued": Availability.discontinued,
}
MAX_ROWS = 20000
MAX_BYTES = 8 * 1024 * 1024


class SheetError(ValueError):
    """The file cannot be read as an item sheet."""


@dataclass
class RowResult:
    row: int  # spreadsheet row number
    code: str
    name: str
    result: str  # changed | unchanged | error
    detail: str | None = None


@dataclass
class SheetResult:
    dry_run: bool
    changed: int = 0
    unchanged: int = 0
    errors: int = 0
    rows: list[RowResult] = field(default_factory=list)


def availability_label(a: str) -> str:
    return {
        "in_stock": "In stock",
        "expected": "Expected",
        "out_of_stock": "Out of stock",
        "discontinued": "Discontinued",
    }.get(str(a), str(a))


def export_xlsx(session: Session, tenant_id: str, items: list[Item]) -> bytes:
    cats = dict(
        session.execute(
            select(ItemCategory.id, ItemCategory.name).where(ItemCategory.tenant_id == tenant_id)
        ).all()
    )
    wb = Workbook()
    ws = wb.active
    ws.title = "Items"
    ws.append(HEADERS)
    for it in items:
        ws.append(
            [
                (it.sku or it.barcode or ""),
                it.name,
                cats.get(it.category_id or "", ""),
                availability_label(it.availability),
                float(it.default_rate) if it.default_rate is not None else None,
                it.pack_qty,
                it.carton_qty,
                it.hsn_code or "",
                float(it.gst_rate) if it.gst_rate is not None else None,
            ]
        )
    ws.freeze_panes = "A2"
    for col, width in zip("ABCDEFGHI", (14, 48, 22, 14, 14, 10, 12, 10, 8), strict=True):
        ws.column_dimensions[col].width = width
    # the code is an identifier: keep it as text so Excel never turns it into a number
    for row in ws.iter_rows(min_row=2, max_col=1):
        row[0].number_format = "@"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def read_rows(data: bytes, filename: str) -> list[dict[str, str]]:
    """The sheet's rows as {header: text}, header names matched case-insensitively."""
    if len(data) > MAX_BYTES:
        raise SheetError("That file is over 8 MB.")
    raw: list[list[object]]
    if filename.lower().endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        raw = [list(r) for r in csv.reader(io.StringIO(text))]
    else:
        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001 - not a workbook
            raise SheetError("That is not an Excel (.xlsx) or CSV file.") from exc
        ws = wb.worksheets[0]
        raw = [list(r) for r in ws.iter_rows(values_only=True)]
    if not raw:
        raise SheetError("The sheet is empty.")
    header = [str(h or "").strip().lower() for h in raw[0]]
    if "code" not in header:
        raise SheetError("The first row needs a Code column. Export your items to get the layout.")
    if len(raw) - 1 > MAX_ROWS:
        raise SheetError(f"That is more than {MAX_ROWS} rows. Split the sheet.")
    out: list[dict[str, str]] = []
    for r in raw[1:]:
        cells = {
            header[i]: ("" if v is None else _text(v))
            for i, v in enumerate(r)
            if i < len(header) and header[i]
        }
        if any(v.strip() for v in cells.values()):
            out.append(cells)
    return out


def _text(v: object) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _num(s: str, what: str) -> Decimal:
    try:
        d = Decimal(s.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"{what} '{s}' is not a number") from exc
    if d < 0:
        raise ValueError(f"{what} cannot be negative")
    return d


def apply_rows(
    session: Session,
    tenant_id: str,
    rows: list[dict[str, str]],
    *,
    dry_run: bool,
    normalize,  # callable(name) -> normalised key
) -> SheetResult:
    res = SheetResult(dry_run=dry_run)
    cats = {
        n.casefold(): cid
        for cid, n in session.execute(
            select(ItemCategory.id, ItemCategory.name).where(ItemCategory.tenant_id == tenant_id)
        ).all()
    }
    seen: set[str] = set()
    for i, cells in enumerate(rows, start=2):
        code = cells.get("code", "").strip()
        name_in = cells.get("name", "").strip()
        r = RowResult(row=i, code=code, name=name_in, result="unchanged")
        res.rows.append(r)
        try:
            if not code:
                raise ValueError("no code")
            if code in seen:
                raise ValueError("this code appears twice in the sheet")
            seen.add(code)
            item = session.scalar(
                select(Item).where(
                    Item.tenant_id == tenant_id,
                    Item.merged_into_id.is_(None),
                    (Item.sku == code) | (Item.barcode == code),
                )
            )
            if item is None:
                raise ValueError("no item has this code")
            r.name = item.name
            diff = _diff(session, tenant_id, item, cells, cats, normalize)
            if not diff:
                res.unchanged += 1
                continue
            r.result = "changed"
            r.detail = "; ".join(f"{k}: {old} → {new}" for k, (old, new, _) in diff.items())
            for _k, (_o, _n, apply) in diff.items():  # a dry run is rolled back by the caller
                apply()
            session.flush()
            res.changed += 1
        except ValueError as exc:
            r.result, r.detail = "error", str(exc)
            res.errors += 1
    return res


def _diff(session, tenant_id, item: Item, cells, cats, normalize):  # type: ignore[no-untyped-def]
    """{field: (old, new, apply)} for the cells that differ from the item."""
    out: dict[str, tuple[object, object, object]] = {}

    name = cells.get("name", "").strip()
    if name and name != item.name:
        if len(name) > 200:
            raise ValueError("name is over 200 characters")
        key = normalize(name)
        clash = session.scalar(
            select(Item.name).where(
                Item.tenant_id == tenant_id, Item.id != item.id, Item.name_normalized == key
            )
        )
        if not key or clash is not None:
            raise ValueError(f"name would match the existing item '{clash}'")

        def set_name(item=item, name=name, key=key) -> None:
            item.name, item.name_normalized = name, key

        out["name"] = (item.name, name, set_name)

    group = cells.get("group", "").strip()
    if group:
        cid = cats.get(group.casefold())
        if cid is None:
            raise ValueError(f"unknown group '{group}'")
        if cid != item.category_id:
            old = next((n for n, c in cats.items() if c == item.category_id), "none")

            def set_group(item=item, cid=cid) -> None:
                item.category_id = cid

            out["group"] = (old, group, set_group)

    avail = cells.get("availability", "").strip()
    if avail:
        a = _AVAIL_LABELS.get(avail.casefold())
        if a is None:
            raise ValueError(
                f"availability '{avail}' is not In stock, Expected, Out of stock or Discontinued"
            )
        if str(a.value) != str(item.availability):

            def set_avail(item=item, a=a) -> None:
                item.availability = a

            out["availability"] = (
                availability_label(item.availability),
                availability_label(a.value),
                set_avail,
            )

    price = cells.get("selling price", "").strip()
    if price:
        p = _num(price, "selling price").quantize(Decimal("0.01"))
        old_p = (
            Decimal(str(item.default_rate)).quantize(Decimal("0.01"))
            if item.default_rate is not None
            else None
        )
        if old_p != p:

            def set_price(item=item, p=p) -> None:
                item.default_rate = float(p)

            out["price"] = (old_p, p, set_price)

    for header, attr, label in (
        ("pack qty", "pack_qty", "pack qty"),
        ("carton qty", "carton_qty", "carton qty"),
    ):
        v = cells.get(header, "").strip()
        if v:
            n = _num(v, label)
            if n != n.to_integral_value() or n < 1 or n > 100000:
                raise ValueError(f"{label} must be a whole number from 1 to 100000")
            if getattr(item, attr) != int(n):

                def set_qty(item=item, attr=attr, n=int(n)) -> None:
                    setattr(item, attr, n)

                out[label] = (getattr(item, attr), int(n), set_qty)

    hsn = cells.get("hsn", "").strip()
    if hsn:
        if not (hsn.isdigit() and 4 <= len(hsn) <= 8):
            raise ValueError("HSN must be 4 to 8 digits")
        if not hsn_exists(session, hsn):
            raise ValueError(f"HSN {hsn} is not in the HSN list")
        if hsn != (item.hsn_code or ""):

            def set_hsn(item=item, hsn=hsn) -> None:
                item.hsn_code = hsn

            out["hsn"] = (item.hsn_code, hsn, set_hsn)

    gst = cells.get("gst %", "").strip()
    if gst:
        g = _num(gst, "GST %").quantize(Decimal("0.01"))
        if g > 100:
            raise ValueError("GST % cannot be over 100")
        old_g = (
            Decimal(str(item.gst_rate)).quantize(Decimal("0.01"))
            if item.gst_rate is not None
            else None
        )
        if old_g != g:

            def set_gst(item=item, g=g) -> None:
                item.gst_rate = float(g)

            out["gst %"] = (old_g, g, set_gst)
    return out
