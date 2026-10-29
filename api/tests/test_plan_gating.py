"""What each plan switches on, decided from the licence Fleek reports (services/features.py). Pure: no database, no HTTP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models import Tenant
from app.services import features
from app.services.entitlements import Licence, licence_from_payload

LIVE = features.LADDER_LIVE_AT
JUST_AFTER = LIVE + timedelta(days=1)


def tenant(signup="self", created=LIVE) -> Tenant:
    t = Tenant(legal_name="Sethia Metal Store")
    t.signup_source, t.created_at = signup, created
    return t


GROWTH = ("pdf_out", "whatsapp", "orders", "collections", "reminders", "catalog_parse", "inward_prices", "import", "slip", "voice", "tally_backup")
COMPLETE = (*GROWTH, "tally_full")


def lic(plan, features_=None, status="active") -> Licence:
    return Licence(status=status, plan_key=plan, plan_label=plan, features=features_)


def allowed(t, licence, key, now=JUST_AFTER) -> bool:
    return features.allowed(None, t, key, licence, now)  # type: ignore[arg-type]


def test_a_firm_not_linked_to_fleek_keeps_everything() -> None:
    for key in features.FEATURES:
        assert allowed(tenant(signup=None), None, key)


def test_the_licence_carries_the_plans_feature_list() -> None:
    got = licence_from_payload({"status": "active", "plan": {"key": "p"}, "features": ["pdf_out", "whatsapp"]})
    assert got.features == ("pdf_out", "whatsapp")
    assert licence_from_payload({"status": "active", "plan": {"key": "p"}}).features is None   # an older answer


def test_free_has_nothing_switched_on_except_pdf_for_seven_days() -> None:
    t, free = tenant(created=LIVE), lic(features.FREE_PLAN, [])
    for key in features.FEATURES:
        if key != "pdf_out":
            assert not allowed(t, free, key), key
    assert allowed(t, free, "pdf_out", now=LIVE + timedelta(days=6, hours=23))        # still inside the 7 days
    assert not allowed(t, free, "pdf_out", now=LIVE + timedelta(days=7, hours=1))     # then it is Starter's
    assert features.pdf_days_left(t, LIVE + timedelta(days=5)) == 2
    assert features.pdf_days_left(t, LIVE + timedelta(days=9)) == 0


def test_the_seven_days_run_from_sign_up_or_from_the_ladder_going_live_whichever_is_later() -> None:
    old = tenant(created=LIVE - timedelta(days=60))                  # on Free long before the ladder: 7 days from going live
    assert allowed(old, lic(features.FREE_PLAN, []), "pdf_out", now=LIVE + timedelta(days=3))
    assert not allowed(old, lic(features.FREE_PLAN, []), "pdf_out", now=LIVE + timedelta(days=8))
    new = tenant(created=LIVE + timedelta(days=20))                  # signs up later: 7 days from its own sign-up
    assert allowed(new, lic(features.FREE_PLAN, []), "pdf_out", now=LIVE + timedelta(days=26))
    assert not allowed(new, lic(features.FREE_PLAN, []), "pdf_out", now=LIVE + timedelta(days=28))


def test_starter_unlocks_only_the_pdf() -> None:
    t, starter = tenant(), lic("metal_erp_basic", ["pdf_out"])
    assert allowed(t, starter, "pdf_out", now=LIVE + timedelta(days=400))
    assert not allowed(t, starter, "whatsapp") and not allowed(t, starter, "orders") and not allowed(t, starter, "reminders")


@pytest.mark.parametrize("key", GROWTH)
def test_growth_unlocks_what_the_owner_listed_but_not_full_tally(key: str) -> None:
    assert allowed(tenant(), lic("metal_erp_growth", list(GROWTH)), key)
    assert not allowed(tenant(), lic("metal_erp_growth", list(GROWTH)), "tally_full")


def test_complete_unlocks_everything() -> None:
    for key in features.FEATURES:
        assert allowed(tenant(), lic("metal_erp_complete", list(COMPLETE)), key)


def test_an_older_plan_with_no_list_keeps_everything_so_nobody_loses_anything() -> None:
    for key in features.FEATURES:
        assert allowed(tenant(signup=None), lic("metal_erp_standard", None), key)


def test_a_self_signed_up_shop_whose_trial_lapsed_is_held_to_free_but_a_fleek_made_shop_is_not() -> None:
    lapsed = lic("metal_erp_growth", list(GROWTH), status="lapsed")
    assert not allowed(tenant(signup="self"), lapsed, "whatsapp")
    assert allowed(tenant(signup=None), lapsed, "whatsapp")          # Fleek's lenient rule for shops it made
    assert features.is_free(None, tenant(signup="self"), lapsed)      # type: ignore[arg-type]
    assert not features.is_free(None, tenant(signup=None), lapsed)    # type: ignore[arg-type]


def test_a_self_signed_up_shop_fleek_has_never_answered_for_is_free() -> None:
    unanswered = Licence(status="unverified")
    assert features.is_free(None, tenant(signup="self"), unanswered)  # type: ignore[arg-type]
    assert not features.is_free(None, tenant(signup=None), unanswered)  # type: ignore[arg-type]


def test_the_old_tally_key_still_answers_and_means_the_backup() -> None:
    t, growth = tenant(), lic("metal_erp_growth", list(GROWTH))
    snap = features.snapshot(None, t, growth, JUST_AFTER)  # type: ignore[arg-type]
    assert snap["tally"] is True and snap["tally_backup"] is True and snap["tally_full"] is False


def test_a_locked_screen_can_name_the_cheapest_plan_that_unlocks_it() -> None:
    ladder = [
        {"plan": "free", "price_amount": 0, "unlocks": []},
        {"plan": "basic", "price_amount": 399, "unlocks": ["pdf_out"]},
        {"plan": "growth", "price_amount": 999, "unlocks": ["pdf_out", "whatsapp", "tally_backup"]},
        {"plan": "complete", "price_amount": 1499, "unlocks": ["pdf_out", "whatsapp", "tally_backup", "tally_full"]},
    ]
    assert features.unlocking_plan("whatsapp", ladder)["plan"] == "growth"
    assert features.unlocking_plan("tally_full", ladder)["plan"] == "complete"
    assert features.unlocking_plan("pdf_out", ladder)["plan"] == "basic"
    assert features.unlocking_plan("tally", ladder)["plan"] == "growth"          # the old key, via its alias
    assert features.unlocking_plan("nonsense", ladder) is None


def test_now_defaults_to_the_present() -> None:
    assert features.allowed(None, tenant(created=datetime.now(UTC)), "pdf_out", lic(features.FREE_PLAN, []))  # type: ignore[arg-type]
