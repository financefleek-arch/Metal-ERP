"""S4.1 - the key stored for a pushed voucher is Tally's real one (services/tally/push.py)."""

from __future__ import annotations

from lxml import etree

from app.services.tally.push import is_confirmed_key, voucher_guid, voucher_identity

CO = "167b31a2-6486-4396-8ee8-7fe38496052d"


def _resp(xml: str) -> etree._Element:
    return etree.fromstring(f"<RESPONSE>{xml}</RESPONSE>".encode())


def test_the_guid_is_the_company_guid_plus_eight_hex_digits_of_the_master_id() -> None:
    assert voucher_guid(CO, "28") == f"{CO}-0000001c"  # 28 = 0x1c (the live push that landed as id 28)
    assert voucher_guid(CO, "4294967295") == f"{CO}-ffffffff"
    assert voucher_guid(None, "28") is None and voucher_guid(CO, "abc") is None


def test_lastvchid_with_a_known_company_gives_the_real_guid() -> None:
    key, mid = voucher_identity(_resp("<CREATED>1</CREATED><LASTVCHID>28</LASTVCHID>"), CO)
    assert (key, mid) == (f"{CO}-0000001c", "28")


def test_lastvchid_without_the_company_guid_keeps_the_master_id() -> None:
    assert voucher_identity(_resp("<LASTVCHID>28</LASTVCHID>"), None) == ("28", "28")


def test_a_named_voucher_key_still_wins_when_there_is_no_master_id() -> None:
    assert voucher_identity(_resp("<VOUCHERKEY>abc</VOUCHERKEY>"), CO) == ("abc", None)
    assert voucher_identity(_resp("<CREATED>1</CREATED>"), CO) == (None, None)


def test_the_placeholder_is_marked_unconfirmed() -> None:
    assert is_confirmed_key(f"{CO}-0000001c") and is_confirmed_key("28")
    assert not is_confirmed_key("pushed:42") and not is_confirmed_key(None)
