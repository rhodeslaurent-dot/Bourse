"""Intraday re-evaluation (user request of 28/10/2026): the four transitions, rule 2 on the quote's market age,
only changes stored, notifications only on an actionable entry after another state or on a degraded setup."""

from datetime import UTC, datetime, timedelta

from app.domain.admission import Setup
from app.domain.reevaluation import ReevalInput, ReevalStatus, classify, notify_kind, should_store

NOW = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)


def _inp(price: float, setup: Setup = Setup.PULLBACK, status: str = "realtime", age_min: float = 0.5, **kw):
    base = dict(
        price=price,
        data_status=status,
        market_timestamp=NOW - timedelta(minutes=age_min),
        now=NOW,
        target=120.0,
        stop=90.0,
        setup=setup,
        level_low=95.0,
        fee_fraction=0.01,
        min_reward_risk=2.0,
        p_max=99.0,
        trigger=100.0 if setup == Setup.BREAKOUT else None,
        max_market_age_minutes=3,
        delay_minutes=15 if status == "delayed" else 0,
    )
    base.update(kw)
    return ReevalInput(**base)


def test_pullback_reached_with_a_fresh_realtime_quote_is_actionable():
    r = classify(_inp(98.5))
    assert r.status == ReevalStatus.ENTRY_REACHED and r.actionable


def test_delayed_quote_is_to_verify_never_actionable():
    r = classify(_inp(98.5, status="delayed", age_min=15))
    assert r.status == ReevalStatus.ENTRY_REACHED and not r.actionable
    assert "à vérifier : cours différé 15 min" in r.reasons


def test_realtime_quote_older_than_the_market_age_limit_is_not_actionable():
    r = classify(_inp(98.5, age_min=4))
    assert r.status == ReevalStatus.ENTRY_REACHED and not r.actionable


def test_breakout_trigger_crossed_under_pmax_and_waiting_below_it():
    assert (
        classify(_inp(99.0, Setup.BREAKOUT, target=130.0, p_max=102.0, level_low=97.0)).status == ReevalStatus.WAITING
    )
    r = classify(_inp(101.0, Setup.BREAKOUT, target=130.0, p_max=102.0, level_low=97.0))
    assert r.status == ReevalStatus.TRIGGER_CROSSED and r.actionable


def test_structure_lost_is_degraded_and_above_pmax_is_refused():
    assert classify(_inp(94.0)).status == ReevalStatus.DEGRADED
    r = classify(_inp(99.5))
    assert r.status == ReevalStatus.ABOVE_PMAX and not r.actionable


def test_stored_pmax_caps_the_recomputed_limit():
    # with fees 0 the R4 price would be 100; the proposal's limit (99) stays the cap
    assert classify(_inp(99.5, fee_fraction=0.0)).status == ReevalStatus.ABOVE_PMAX


def test_blocked_or_to_verify_proposal_is_never_actionable():
    assert not classify(_inp(98.5, blocked=("BLOQUÉ : plafond",))).actionable
    assert not classify(_inp(98.5, to_verify=True)).actionable


def test_only_changes_are_stored():
    r = classify(_inp(98.5))
    assert should_store(None, r)
    assert not should_store(("entree_atteinte", True), r)
    assert should_store(("entree_atteinte", False), r)


def test_notifications():
    entry = classify(_inp(98.5))
    assert notify_kind(("limite_depassee", False), None, entry) == "reeval_entry"
    assert notify_kind(("entree_atteinte", True), None, entry) is None
    assert notify_kind(None, "entree_atteinte", entry) is None  # a BUY already at its entry: no new alert
    assert notify_kind(None, "limite_depassee", entry) == "reeval_entry"
    delayed = classify(_inp(98.5, status="delayed", age_min=15))
    assert notify_kind(("limite_depassee", False), None, delayed) is None  # « à vérifier »: no order card
    degraded = classify(_inp(94.0, status="delayed", age_min=15))
    assert notify_kind(("entree_atteinte", False), None, degraded) == "reeval_degraded"  # always
    assert notify_kind(("configuration_degradee", False), None, degraded) is None


def test_a_price_at_or_under_the_stop_is_never_an_entry():
    # review of 29/10/2026: with no structure level stored, admission alone called it « structure intacte »
    r = classify(_inp(89.0, level_low=None))
    assert r.status == ReevalStatus.DEGRADED and not r.actionable and "stop" in r.reasons[0]


def test_without_a_structure_level_an_entry_is_to_verify():
    r = classify(_inp(98.5, level_low=None))
    assert r.status == ReevalStatus.ENTRY_REACHED and not r.actionable
    assert any(x.startswith("à vérifier : niveau de structure") for x in r.reasons)


def test_an_intraday_break_of_the_support_is_worded_as_a_session_warning():
    r = classify(_inp(94.0))
    assert r.status == ReevalStatus.DEGRADED
    assert "en séance (à confirmer en clôture)" in r.reasons[0] and "perdu en clôture" not in r.reasons[0]


def test_a_letter_trigger_crossed_intraday_is_an_alert_never_an_entry():
    """Review of 30/10/2026 (M4): a letter's breakout not crossed at analysis waits for a close above; its crossing
    intraday is an alert (P3, no order line), never ``reeval_entry``."""
    from app.domain.reevaluation import CLOSE_FIRST

    inp = _inp(101.0, Setup.BREAKOUT, p_max=103.0, target=140.0, wait_for_close=True)
    r = classify(inp)
    assert r.status == ReevalStatus.TRIGGER_CROSSED and not r.actionable and r.reasons[0] == CLOSE_FIRST
    assert notify_kind(("en_attente", False), None, r) == "reeval_trigger_alert"
    assert notify_kind(("declencheur_franchi", False), None, r) is None  # once per crossing
    plain = classify(_inp(101.0, Setup.BREAKOUT, p_max=103.0, target=140.0))
    assert plain.actionable and notify_kind(("en_attente", False), None, plain) == "reeval_entry"


def test_a_letter_alert_under_its_stop_keeps_waiting_intraday():
    """Counter-review of 30/10/2026: no order exists for a letter's breakout not crossed — under its stop it is still
    waiting, never « configuration dégradée : revoir l'ordre en attente »."""
    r = classify(_inp(89.0, Setup.BREAKOUT, p_max=103.0, target=140.0, level_low=None, wait_for_close=True))
    assert r.status == ReevalStatus.WAITING and not r.actionable
    assert classify(_inp(89.0, Setup.BREAKOUT, p_max=103.0, target=140.0)).status == ReevalStatus.DEGRADED
