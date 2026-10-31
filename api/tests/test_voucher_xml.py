"""The voucher export parser, against the real TallyPrime 7.1 dev captures (plan P1)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.services.tally.voucher_xml import (
    CREDIT_NOTE,
    JOURNAL,
    OTHER,
    PURCHASE,
    RECEIPT,
    SALES,
    classify,
    parse_bills_receivable,
    parse_quantity,
    parse_rate,
    parse_vouchers,
)

FIX = Path(__file__).parent / "fixtures"


def _all():
    return parse_vouchers((FIX / "tally_vouchers_dev_sample.xml").read_bytes())


def test_every_voucher_in_the_capture_is_read_with_its_family():
    vs = _all()
    kinds = [v.kind for v in vs]
    assert kinds.count(SALES) == 7
    assert kinds.count(PURCHASE) == 10
    assert kinds.count(JOURNAL) == 1
    assert all(v.guid for v in vs)


def test_a_sales_voucher_gives_party_item_rate_and_the_signed_total():
    sale = next(v for v in _all() if v.kind == SALES and v.items)
    assert sale.party == "ZZTEST Customer"
    assert sale.day == date(2026, 9, 2)
    line = sale.items[0]
    assert line.item == "ZZTEST Item"
    assert line.qty == Decimal(10) and line.unit == "Nos"
    assert line.rate == Decimal("96.00") and line.rate_unit == "Nos"
    assert line.amount == Decimal("960.00")
    # Tally writes the debit negative; the unsigned size is what an invoice total is
    party = next(entry for entry in sale.ledgers if entry.is_party)
    assert party.amount == Decimal("-960.00")
    assert sale.party_amount == Decimal("960.00")


def test_the_party_line_is_not_doubled_by_the_repeated_ledger_list():
    sale = next(v for v in _all() if v.kind == SALES and v.items)
    assert sum(1 for entry in sale.ledgers if entry.ledger == "ZZTEST Customer") == 1


def test_a_journal_is_read_as_ledger_lines_without_items():
    j = next(v for v in _all() if v.kind == JOURNAL)
    assert j.items == []
    assert {entry.ledger for entry in j.ledgers} == {"ZZTEST Sales", "ZZTEST Round Off"}
    assert sum(entry.amount for entry in j.ledgers) == 0


def test_bill_allocations_are_kept_on_the_party_line():
    sale = next(v for v in _all() if v.kind == SALES and v.items)
    party = next(entry for entry in sale.ledgers if entry.is_party)
    assert party.bills and party.bills[0].bill_type == "On Account"
    assert party.bills[0].amount == Decimal("-960.00")


def test_control_characters_tally_emits_do_not_break_the_file():
    raw = (
        b"<ENVELOPE><VOUCHER><GUID>g-1</GUID><VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>"
        b"<DATE>20260902</DATE><VOUCHERNUMBER>7</VOUCHERNUMBER>"
        b"<ALLINVENTORYENTRIES.LIST><STOCKITEMNAME>X</STOCKITEMNAME>"
        b"<INDENTNO>&#4; Not Applicable</INDENTNO><AMOUNT>5.00</AMOUNT>"
        b"</ALLINVENTORYENTRIES.LIST></VOUCHER></ENVELOPE>"
    )
    (v,) = parse_vouchers(raw)
    assert v.number == "7" and v.items[0].amount == Decimal("5.00")


def test_a_receipt_with_bill_allocations_reads_credit_party_and_agst_ref():
    raw = b"""<ENVELOPE><VOUCHER><GUID>r-1</GUID><VOUCHERTYPENAME>Receipt</VOUCHERTYPENAME>
      <DATE>20260903</DATE><VOUCHERNUMBER>4</VOUCHERNUMBER><PARTYLEDGERNAME>Sharma</PARTYLEDGERNAME>
      <ALLLEDGERENTRIES.LIST><LEDGERNAME>Sharma</LEDGERNAME><ISPARTYLEDGER>Yes</ISPARTYLEDGER>
        <AMOUNT>400.00</AMOUNT>
        <BILLALLOCATIONS.LIST><NAME>INV-9</NAME><BILLTYPE>Agst Ref</BILLTYPE><AMOUNT>400.00</AMOUNT>
        </BILLALLOCATIONS.LIST></ALLLEDGERENTRIES.LIST>
      <ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME><ISPARTYLEDGER>No</ISPARTYLEDGER>
        <AMOUNT>-400.00</AMOUNT></ALLLEDGERENTRIES.LIST></VOUCHER></ENVELOPE>"""
    (v,) = parse_vouchers(raw)
    assert v.kind == RECEIPT and v.party_amount == Decimal("400.00")
    bill = v.ledgers[0].bills[0]
    assert (bill.name, bill.bill_type, bill.amount) == ("INV-9", "Agst Ref", Decimal("400.00"))


def test_flags_and_a_voucher_without_a_guid():
    raw = (
        b"<ENVELOPE><VOUCHER><GUID>c-1</GUID><VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>"
        b"<ISCANCELLED>Yes</ISCANCELLED><ISOPTIONAL>Yes</ISOPTIONAL></VOUCHER>"
        b"<VOUCHER><VOUCHERTYPENAME>Sales</VOUCHERTYPENAME></VOUCHER></ENVELOPE>"
    )
    vs = parse_vouchers(raw)
    assert len(vs) == 1 and vs[0].cancelled and vs[0].optional


def test_classify_custom_names_and_a_parent_map():
    assert classify("Sales") == SALES
    assert classify("Sales - Cash") == SALES
    assert classify("Export Sales") == SALES
    assert classify("Credit Note") == CREDIT_NOTE
    assert classify("Sales Return") == CREDIT_NOTE
    assert classify("Contra") == OTHER
    assert classify("Weird Name", {"Weird Name": "Receipt"}) == RECEIPT


def test_quantity_and_rate_text():
    assert parse_quantity(" 10 Nos") == (Decimal(10), "Nos")
    assert parse_quantity("1,250.5 Kg") == (Decimal("1250.5"), "Kg")
    assert parse_quantity("10 Nos = 2 Box") == (Decimal(10), "Nos")
    assert parse_quantity("") == (None, None)
    assert parse_rate("96.00/Nos") == (Decimal("96.00"), "Nos")
    assert parse_rate("96.00") == (Decimal("96.00"), None)


def test_pending_bills_pair_each_bill_with_its_balance_and_due_date():
    bills = parse_bills_receivable((FIX / "tally_bills_receivable_dev_sample.xml").read_bytes())
    assert len(bills) == 5
    first = bills[0]
    assert first.ref == "ZZTEST-SALES-11" and first.party == "ZZTEST Customer"
    assert first.closing == Decimal("-960.00")
    assert first.day == date(2026, 9, 2) and first.due == date(2026, 9, 2)
    assert bills[3].closing == Decimal("-1.00")


def test_a_shops_own_voucher_types_are_classified_by_their_parent_not_their_name() -> None:
    parents = {"Estimate": "Sales", "Cash Memo": "Sales", "Sales Order": "Sales Order", "Delivery Note": "Delivery Note"}
    assert classify("Estimate", parents) == SALES  # the name says nothing, the parent says Sales
    assert classify("Cash Memo", parents) == SALES
    assert classify("Sales Order", parents) == OTHER  # an order is not a sale, whatever the name contains
    assert classify("Delivery Note", parents) == OTHER


def test_without_the_parent_a_document_that_is_not_an_invoice_is_not_guessed_to_be_one() -> None:
    assert classify("Sales Order") == OTHER
    assert classify("Estimate") == OTHER
    assert classify("Delivery Note") == OTHER
    assert classify("Sales - Cash") == SALES and classify("Export Sales") == SALES

