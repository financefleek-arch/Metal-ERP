"""Name cleanup, group rules, pricing: pure functions."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.catalog_groups import classify
from app.services.catalog.names import (
    brand_of,
    display_name,
    infer_brands,
    size_of,
)
from app.services.catalog.pricing import effective_multiplier, sell_price

# --- display names ------------------------------------------------------------


def test_carton_phrase_and_box_type_are_dropped_and_qty_kept() -> None:
    name, carton = display_name(
        "TKB512G DS 190 ML JUICE GLASS COL BOX 6 PC COL BOX 12 SET CTN", "TKB512G"
    )
    assert carton == 12
    assert "CTN" not in name
    assert name.startswith("DS 190 ML Juice Glass")


def test_supplier_code_is_stripped_from_front_and_back() -> None:
    assert display_name("AJ-1003 G K 7 PC PUDDING SET - 6 SET CTN", "AJ-1003")[0] == (
        "G K 7 PC Pudding SET"
    )
    name, _ = display_name("DELI 5 PCS PLATE SNACK SET DS1036-L5", "DS1036-L5", {"DELI"})
    assert name.startswith("DELI 5")


def test_code_with_spaced_hyphen_in_the_name_text_is_still_stripped() -> None:
    raw = "TXZ-02- L4-HA 3 PCS TANK 2 LTR ON WOOD STAND 6 SET CTN"
    name, carton = display_name(raw, "TXZ-02-L4-HA")
    assert name == "3 PCS Tank 2 LTR ON Wood Stand"
    assert carton == 6


def test_sizes_and_quotes_keep_their_case() -> None:
    name, _ = display_name('YJ-2010 5"INCH 8"INCH 7 PCS BOWL SET', "YJ-2010")
    assert '5"INCH' in name and "BOWL" not in name  # "Bowl"


def test_short_initialisms_and_brands_stay_upper() -> None:
    assert display_name("X1 DS 190 ML JUICE GLASS", "X1", {"DELI"})[0] == "DS 190 ML Juice Glass"
    assert display_name("X1 DELI TUMBLER", "X1", {"DELI"})[0] == "DELI Tumbler"


def test_name_is_never_empty() -> None:
    assert display_name("ABC1", "ABC1")[0] == "ABC1"


# --- brand / size -------------------------------------------------------------


def test_brands_are_leading_tokens_that_rarely_appear_elsewhere() -> None:
    raws = (
        [f"DELI {i} ML JUICE GLASS" for i in range(30)]
        + [f"YUJING {i} ML BEER MUG" for i in range(20)]
        # GLASS leads some names but appears everywhere else too: not a brand
        + [f"GLASS JAR {i}" for i in range(10)]
        + [f"KD STEM WINE GLASS {i}" for i in range(40)]
        + [f"WINE GLASS {i}" for i in range(8)]
    )
    codes = [f"C{i}" for i in range(len(raws))]
    assert infer_brands(raws, codes) == {"DELI", "YUJING"}


def test_a_tiny_catalog_still_finds_a_brand_with_three_hits() -> None:
    raws = ["DELI A", "DELI B", "DELI C", "OTHER D"]
    assert infer_brands(raws, ["1", "2", "3", "4"]) == {"DELI"}


def test_brand_of_uses_the_leading_token_after_the_code() -> None:
    assert brand_of("SJ013 DELI SPECIAL", "SJ013", {"DELI"}) == "DELI"
    assert brand_of("SJ013 PLAIN GLASS", "SJ013", {"DELI"}) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("DS 190 ML Juice Glass", "190 ML"),
        ("Water Jug 1.4 LTR", "1.4 LTR"),
        ("Bowl 5 inch", "5 INCH"),
        ('Bowl 5" Inch', '5"'),
        ("Glass 6 PC", None),
    ],
)
def test_size_of(text: str, expected: str | None) -> None:
    assert size_of(text) == expected


# --- groups -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "group"),
    [
        ("Y5706-1 YUJING 480 ML BEER MUG 6 PCS", "Beer Mugs"),  # before generic MUG
        ("COFFEE MUG 300 ML", "Cups & Mugs"),
        ("DM PREMIUM WHISKY GLASS 360 ML", "Whisky Glasses"),
        ("KD STEM WINE GLASS 60 ML", "Wine Glasses"),
        ("40 ML SHOT GLASS", "Shot Glasses"),
        ("KD 235 ML MARGHARITA 6 PC", "Cocktail & Dessert Glasses"),
        ("BLINKMAX ICE CREAM CUP", "Cocktail & Dessert Glasses"),  # before CUP
        ("2 PC 890 ML TEA SUGAR JAR", "Jars & Storage"),
        ("GLASS TANK LID TAP", "Jars & Storage"),
        ("OIL BOTTLE 500 ML", "Bottles"),
        ("1.4 LTR WATER JUG", "Jugs & Water Sets"),
        ("7 PCS WATER SET ON TRAY", "Jugs & Water Sets"),  # before TRAY
        ("5 PCS BOWL SET", "Bowls & Bowl Sets"),
        ("7 PC CASSEROLE 1600 ML", "Casseroles & Serveware"),
        ("STACKED SPHERE FLOWER VASE", "Vases & Decor"),
        ("DELI 5 PCS PLATE SNACK SET", "Plates & Dinner Sets"),
        ("DELI 210 ML JUICE GLASS", "Juice & Water Glasses"),
        ("1.7 LTR PREMIUM WATER", "Jugs & Water Sets"),
        ("PLAIN GLASS 6 PC", "Drinking Glasses"),
    ],
)
def test_group_rules(name: str, group: str) -> None:
    assert classify(name) == group


def test_unknown_product_has_no_group() -> None:
    assert classify("MYSTERY ITEM 5 PCS") is None


def test_classification_is_case_insensitive() -> None:
    assert classify("beer mug 400 ml") == "Beer Mugs"


# --- pricing ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cost", "mult", "step", "expected"),
    [
        ("190", "1.25", 1, "238.00"),  # 237.5 rounds half up
        ("190", "1.25", 5, "240.00"),
        ("190", "1.25", 10, "240.00"),
        ("113", "1.000", 1, "113.00"),
        ("99", "1.40", 1, "139.00"),  # 138.6
        ("105", "1.5", 10, "160.00"),  # 157.5 -> nearest 10
        ("0", "1.25", 1, "0.00"),
    ],
)
def test_sell_price(cost: str, mult: str, step: int, expected: str) -> None:
    assert sell_price(Decimal(cost), Decimal(mult), step) == Decimal(expected)


def test_half_up_not_bankers_rounding() -> None:
    # 2.5 and 3.5 both round UP (banker's would give 2 and 4)
    assert sell_price(Decimal("2"), Decimal("1.25"), 1) == Decimal("3.00")
    assert sell_price(Decimal("2.8"), Decimal("1.25"), 1) == Decimal("4.00")


def test_no_float_drift() -> None:
    assert sell_price(Decimal("0.1"), Decimal("3"), 1) == Decimal("0.00")
    assert sell_price(Decimal("199.99"), Decimal("1.1"), 1) == Decimal("220.00")


def test_bad_step_and_multiplier_rejected() -> None:
    with pytest.raises(ValueError):
        sell_price(Decimal("10"), Decimal("1"), 7)
    with pytest.raises(ValueError):
        sell_price(Decimal("10"), Decimal("0"), 1)
    with pytest.raises(ValueError):
        sell_price(Decimal("10"), Decimal("-1"), 1)


def test_override_beats_catalog_multiplier() -> None:
    assert effective_multiplier(Decimal("1.4"), Decimal("1.25")) == Decimal("1.4")
    assert effective_multiplier(None, Decimal("1.25")) == Decimal("1.25")
