"""parse_product_line + generated_name — rules-first, no I/O.

The corpus in tests/fixtures/real_bill_lines.py is the acceptance bar:
every line from the 5 real bills must parse its asserted fields correctly.
"""

from __future__ import annotations

import pytest

from app.domain.product_parse import generated_name, parse_product_line
from app.models._mixins import RateMode
from tests.fixtures.real_bill_lines import BRANDS, CORPUS, SYNONYMS


@pytest.mark.parametrize("raw,expect", CORPUS, ids=[c[0][:40] for c in CORPUS])
def test_corpus_line(raw: str, expect: dict) -> None:
    p = parse_product_line(raw, brands=BRANDS, synonyms=SYNONYMS)
    for field, want in expect.items():
        if want is None:
            continue
        got = getattr(p, field)
        assert got == want, f"{field}: got {got!r}, want {want!r}  (line: {raw!r})"


def test_confidence_is_reasonable_on_clean_lines() -> None:
    clean = "Dhara Kettly 10cup 6PC 425"
    p = parse_product_line(clean, brands=BRANDS, synonyms=SYNONYMS)
    assert p.is_confident, f"expected confident parse, got {p.confidence}"


def test_bare_number_size_is_low_confidence() -> None:
    p = parse_product_line("BK 4 13.600 x 400", brands=BRANDS)
    assert p.size == "4" and p.size_kind == "bare"
    assert not p.is_confident


def test_empty_input() -> None:
    p = parse_product_line("", brands=BRANDS)
    assert p.confidence == 0.0 and p.brand is None


def test_decimal_qty_implies_kg() -> None:
    p = parse_product_line("Something 12.5 x 100", brands=[])
    assert p.rate_mode is RateMode.kg


def test_integer_qty_with_times_implies_piece() -> None:
    p = parse_product_line("Something 3 x 100", brands=[])
    assert p.rate_mode is RateMode.piece


def test_per_kgs_column_wins() -> None:
    p = parse_product_line("Thing 5 per KGS 200", brands=[])
    # "5" here has no PC/decimal signal but the column marker forces kg
    assert p.rate_mode is RateMode.kg


def test_nxn_size() -> None:
    p = parse_product_line("ST STORAGE BOX 20 X 24 100 KGS 186", brands=BRANDS)
    assert p.size == "20x24" and p.size_kind == "nxn" and p.size_sort == 20.0


def test_default_rate_mode_used_when_nothing_else() -> None:
    p = parse_product_line("Mystery Widget", brands=[], default_rate_mode=RateMode.piece)
    assert p.rate_mode is RateMode.piece


# --------------------------------------------------------------------------
# generated_name
# --------------------------------------------------------------------------


def test_generated_name_with_sku() -> None:
    n = generated_name(
        category_name="Mintage", group_name="Mintage Casserole", sku="3499", size_label="5 Litre"
    )
    assert n == "Mintage 3499 5 Litre"


def test_generated_name_without_sku() -> None:
    n = generated_name(
        category_name="ST", group_name="ST Storage Box", sku=None, size_label="12X18"
    )
    assert n == "ST Storage Box 12X18"


def test_generated_name_falls_back_to_group() -> None:
    n = generated_name(
        category_name=None, group_name="Loose Thing", sku=None, size_label=None
    )
    assert n == "Loose Thing"
