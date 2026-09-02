"""Unit tests for the reconciliation gate and the normalize pipeline."""

from __future__ import annotations

from decimal import Decimal

from app.domain.normalize import normalize_name
from app.services.inward.reconcile import reconcile


class TestReconcile:
    def test_exact(self) -> None:
        r = reconcile(
            taxable_total="35970.22",
            cgst_total="3237.32",
            sgst_total="3237.32",
            igst_total=None,
            round_off="0.14",
            grand_total="42445.00",
        )
        assert r.reconciled
        assert r.discrepancy == Decimal("0.00")

    def test_within_tolerance(self) -> None:
        r = reconcile(
            taxable_total="100.00",
            cgst_total="9.00",
            sgst_total="9.00",
            igst_total=None,
            round_off="0",
            grand_total="118.04",  # +0.04, inside ±0.05
        )
        assert r.reconciled

    def test_outside_tolerance(self) -> None:
        r = reconcile(
            taxable_total="100.00",
            cgst_total="9.00",
            sgst_total="9.00",
            igst_total=None,
            round_off="0",
            grand_total="118.50",  # +0.50
        )
        assert not r.reconciled
        assert r.discrepancy == Decimal("0.50")

    def test_igst_path(self) -> None:
        r = reconcile(
            taxable_total="1000.00",
            cgst_total=None,
            sgst_total=None,
            igst_total="180.00",
            round_off="0",
            grand_total="1180.00",
        )
        assert r.reconciled


class TestNormalize:
    def test_basic_casefold_and_punctuation(self) -> None:
        assert normalize_name("Monin  MOJITO-Mint  Syrup") == "monin mojito mint syrup"

    def test_pack_size_unified(self) -> None:
        a = normalize_name("Monin Coconut 1000Mlx6")
        b = normalize_name("monin coconut 1000ml x 6")
        c = normalize_name("MONIN COCONUT 1000ML * 6")
        assert a == b == c

    def test_synonyms_applied(self) -> None:
        syn = {"stainless": "ss", "s s": "ss", "pcs": "nos"}
        assert normalize_name("Stainless Steel Balti", syn) == "ss steel balti"
        assert normalize_name("S S Angle", syn) == "ss angle"
        assert normalize_name("Cup 12 Pcs", syn) == "cup 12 nos"

    def test_token_order_preserved(self) -> None:
        assert normalize_name("angle ss") != normalize_name("ss angle")

    def test_bartan_synonyms_english_canonical(self) -> None:
        from app.seed import SYNONYMS

        syn = dict(SYNONYMS)
        # Hindi and English spellings collapse to the same key
        assert normalize_name("Pital Balti No 3", syn) == "brass bucket no 3"
        assert normalize_name("Brass Bucket No 3", syn) == "brass bucket no 3"
        # spelling variants of the same object also converge
        assert normalize_name("SS Kadhai 10", syn) == normalize_name("SS Karahi 10", syn)

    def test_empty(self) -> None:
        assert normalize_name("") == ""
        assert normalize_name("!!!") == ""
