"""Lock `app.domain.tax.compute_invoice` against the shared truth table.

`tests/vectors/tax_vectors.json` is also consumed by the web preview's
Vitest suite, so the two implementations cannot drift.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.tax import (
    InvoiceInput,
    LineInput,
    amount_in_words,
    compute_invoice,
)

_VECTORS = json.loads(
    (Path(__file__).parent / "vectors" / "tax_vectors.json").read_text(encoding="utf-8")
)


def _case_id(case: dict) -> str:
    return case["name"]


@pytest.mark.parametrize("case", _VECTORS["cases"], ids=_case_id)
def test_vector(case: dict) -> None:
    inp = InvoiceInput(
        lines=[
            LineInput(
                quantity=Decimal(str(ln["quantity"])),
                unit_rate=Decimal(str(ln["unit_rate"])),
                discount=Decimal(str(ln.get("discount", "0"))),
            )
            for ln in case["input"]["lines"]
        ],
        invoice_discount=Decimal(str(case["input"].get("invoice_discount", "0"))),
    )
    got = compute_invoice(inp)
    exp = case["expect"]

    assert str(got.subtotal) == exp["subtotal"]
    assert str(got.discount_total) == exp["discount_total"]
    assert str(got.taxable_total) == exp["taxable_total"]
    assert str(got.round_off) == exp["round_off"]
    assert str(got.grand_total) == exp["grand_total"]
    assert got.amount_in_words == exp["amount_in_words"]


@pytest.mark.parametrize(
    "amount,words",
    [
        (0, "INR Zero Only"),
        (7, "INR Seven Only"),
        (70, "INR Seventy Only"),
        (100, "INR One Hundred Only"),
        (118, "INR One Hundred Eighteen Only"),
        (1000, "INR One Thousand Only"),
        (100000, "INR One Lakh Only"),
        (10000000, "INR One Crore Only"),
        (Decimal("123.45"), "INR One Hundred Twenty Three and Forty Five Paise Only"),
        (Decimal("-5.00"), "Minus INR Five Only"),
    ],
)
def test_amount_in_words(amount: object, words: str) -> None:
    assert amount_in_words(amount) == words
