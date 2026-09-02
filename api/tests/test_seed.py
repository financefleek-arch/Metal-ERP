"""The seed's reference data is well-formed and its loaders are idempotent."""

from __future__ import annotations

from sqlalchemy import select

from app.models import HsnCode, Synonym, Tenant
from app.seed import BARTAN_SYNONYMS, HSN_CODES, SYNONYMS, seed_hsn, seed_synonyms


def test_hsn_codes_are_wellformed() -> None:
    codes = [c for c, *_ in HSN_CODES]
    assert len(codes) == len(set(codes)), "duplicate HSN codes in seed"
    for code, desc, chapter, rate in HSN_CODES:
        assert len(code) in (4, 6, 8) and code.isdigit()
        assert code.startswith(chapter)
        assert desc
        assert rate in (0.0, 5.0, 12.0, 18.0, 28.0)


def test_synonyms_have_no_conflicting_targets() -> None:
    seen: dict[str, str] = {}
    for frm, to in SYNONYMS:
        assert frm not in seen or seen[frm] == to, f"conflicting synonym for {frm!r}"
        seen[frm] = to


def test_bartan_synonyms_present_and_english_canonical() -> None:
    m = dict(SYNONYMS)
    # a few key Hindi -> English trade-term rewrites the type-ahead relies on
    assert m["balti"] == "bucket"
    assert m["pital"] == "brass"
    assert m["kadhai"] == "wok"
    assert m["jhoola"] == "swing"
    # the bartan block is actually folded into SYNONYMS
    assert set(BARTAN_SYNONYMS).issubset(set(SYNONYMS))
    # targets are never themselves rewritten to something else (no chains)
    targets = {to for _, to in BARTAN_SYNONYMS}
    for t in targets:
        assert m.get(t, t) == t, f"synonym target {t!r} is itself rewritten"


def test_seed_hsn_is_idempotent(session) -> None:  # type: ignore[no-untyped-def]
    first = seed_hsn(session)
    session.flush()
    second = seed_hsn(session)
    assert first == len(HSN_CODES)
    assert second == 0
    assert session.scalar(select(HsnCode).where(HsnCode.code == "73239390")) is not None


def test_seed_synonyms_is_idempotent(session) -> None:  # type: ignore[no-untyped-def]
    t = Tenant(legal_name="T")
    session.add(t)
    session.flush()

    first = seed_synonyms(session, t.id)
    session.flush()
    second = seed_synonyms(session, t.id)
    assert first == len(SYNONYMS)
    assert second == 0
    rows = session.scalars(select(Synonym).where(Synonym.tenant_id == t.id)).all()
    assert len(rows) == len(SYNONYMS)
