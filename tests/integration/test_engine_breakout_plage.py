"""Docs/06 §6.3 applied on 30/10/2026: a breakout (d2, d3, d8, a letter's) is entered with an order à plage de
déclenchement for J+1 — seuil = the detector's trigger, limite = P_max (R4, capped by the technical maximum), valid
``entry.confirm_sessions`` sessions of the value's venue, sized on the limit and the slipped stop (rule 7). The
code used to give every proposal a « limite au prix maximal », which put a breakout's limit under its trigger."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.db.models import Proposal, Signal
from app.db.session import db_session
from app.domain.indicators import Bar
from app.services import engine
from tests.integration.test_engine import DAY, ISIN, NOW, _seed, breakout_bars


def plage_bars(close: float) -> list[Bar]:
    """The synthetic d2 breakout (pivot 99.485, trigger 99.78, stop under the pivot zone 98.99) with a wick to 92 in
    the base: the measured move (≈ 107) is the first exploitable target, far enough for P_max ≥ seuil at k = 2."""
    bars = breakout_bars()
    w = bars[-15]
    bars[-15] = Bar(w.open, w.high, 92.0, w.close, w.volume)
    bars[-1] = Bar(99.2, max(close, 99.6) + 0.1, 98.9, close, 400_000)
    return bars


def _d2(s) -> Proposal:
    live = s.query(Proposal).filter(Proposal.isin == ISIN, Proposal.deleted_at.is_(None))
    props = [p for p in live if s.get(Signal, p.signal_id).detector == "d2_breakout"]
    assert len(props) == 1
    return props[0]


def test_a_d2_breakout_gets_its_plage_order_sized_on_the_limit(db_engine, config):
    with db_session() as s:
        _seed(s, plage_bars(99.7))  # closes over the pivot, under the seuil: the plage is armed for J+1
        engine.scan(s, config, DAY, now=NOW)
        p = _d2(s)
        d = p.details
        ent = d["entree"]
        assert ent["type"] == "ordre à plage de déclenchement"
        assert ent["seuil"] == pytest.approx(99.485 * 1.003)  # pivot + 0.3 % (docs/06 §6.3)
        assert ent["limite"] == d["p_max"] and ent["seuil"] <= ent["limite"] <= d["configuration"]["p_max_technique"]
        assert ent["validite_seances"] == 3 and ent["jusqu_au"] == "2026-10-26" == d["validite"]["jusqu_au"]
        assert p.entry_zone == ent
        assert d["admission"] == "admis" and not any("en attente d'un repli" in b for b in p.gates["blocked"])
        row = d["comptes"][p.account_recommended]
        assert row["p_max"] == ent["limite"] and row["titres"] > 0
        stop_exec = p.stop * (1 - config.params.risk.slippage_pct)
        assert row["montant_eur"] == pytest.approx(row["titres"] * ent["limite"], abs=0.01)  # on the limit
        assert row["risque_eur"] == pytest.approx(row["titres"] * (ent["limite"] - stop_exec), abs=0.01)
        assert row["risque_eur"] <= config.params.capital.capital_pilote_eur * config.params.risk.max_risk_per_trade_pct


def test_a_d2_breakout_above_the_limit_of_its_plage_is_not_chased(db_engine, config):
    with db_session() as s:
        _seed(s, plage_bars(100.8))  # the close already above the limit (≈ 100.2)
        engine.scan(s, config, DAY, now=NOW)
        p = _d2(s)
        blocked = p.gates["blocked"]
    assert p.action == "WATCH" and p.details.get("entree") is None and p.entry_zone is None
    assert any(
        b.startswith("WATCH : cours au-dessus de la limite de la plage — cours 100.8 > limite ") for b in blocked
    )
    assert not any("en attente d'un repli" in b for b in blocked)  # no limit waiting under the price


def test_the_order_book_shows_the_plage_and_the_distance_to_its_seuil(db_engine, config):
    from app.web.opportunities_routes import information_only, order_book

    with db_session() as s:
        _seed(s, plage_bars(99.7))
        engine.scan(s, config, DAY, now=NOW)
        p = _d2(s)
        p.action, p.gates = "BUY", {"passed": [], "blocked": []}  # the score and the gate passed (not tested here)
        it = {"p": p, "name": "TotalEnergies", "snapshot_price": {"last": 99.7}}
        rows = order_book([it])
        assert not information_only(it, 0.05)  # a valid armed breakout is never folded
    assert len(rows) == 1
    r = rows[0]
    assert r["seuil"] == pytest.approx(99.485 * 1.003) and r["p_max"] == p.details["entree"]["limite"]
    assert r["expire"] == "2026-10-26"
    assert r["distance"] == pytest.approx(99.7 / r["seuil"] - 1)  # to the seuil, not to the limit


def test_the_plage_is_valid_confirm_sessions_of_the_venue(db_engine, config):
    import copy

    config = copy.deepcopy(config)
    config.params.entry["confirm_sessions"] = 2
    with db_session() as s:
        _seed(s, plage_bars(99.7))
        engine.scan(s, config, DAY, now=NOW)
        p = _d2(s)
    assert p.details["entree"]["validite_seances"] == 2 and p.details["entree"]["jusqu_au"] == "2026-10-23"
    exp = p.expires_at if p.expires_at.tzinfo else p.expires_at.replace(tzinfo=NOW.tzinfo)
    assert exp.date() == NOW.date() + timedelta(days=1)  # the proposal follows its order's validity


def test_the_card_says_the_plage_and_that_no_order_is_placed(db_engine, config):
    from app.services.explain import explain_proposal

    with db_session() as s:
        _seed(s, plage_bars(99.7))
        engine.scan(s, config, DAY, now=NOW)
        p = _d2(s)
        ex = explain_proposal(p, s.get(Signal, p.signal_id), None, 40_000, "EUR")
        from app.services.thesis import facts_for

        facts = [f["texte"] for f in facts_for(s, p, NOW)]
    assert any(f.startswith("Entrée : ordre à plage de déclenchement J+1, seuil 99.78, limite (prix maximal) ")
               and "valable 3 séances" in f for f in facts)  # fmt: skip
    assert ex.order_line is not None
    assert ex.order_line.startswith("Ordre à plage de déclenchement J+1 : seuil 99,78 €, limite ")
    assert "valable 3 séances (jusqu'au 26/10/2026) — à saisir chez le courtier ; l'outil ne passe aucun ordre" in (
        ex.order_line
    )
    assert "seuil 99.78 € · limite" in ex.headline


def test_a_d2_breakout_whose_maximum_price_falls_under_its_seuil_gets_no_order(db_engine, config):
    """At k = 0.3 the synthetic d2 (seuil 99.78) gets P_max 99.28, above its stop but under its seuil: no plage, no
    limit waiting under the trigger, nothing in the order book, the card folded."""
    import copy

    from app.web.opportunities_routes import information_only, order_book

    config = copy.deepcopy(config)
    config.params.admission["min_reward_risk"] = 0.3
    with db_session() as s:
        _seed(s, breakout_bars())
        engine.scan(s, config, DAY, now=NOW)
        p = _d2(s)
        blocked = p.gates["blocked"]
        it = {"p": p, "name": "TotalEnergies", "snapshot_price": {"last": 100.8}}
        rows, folded = order_book([it]), information_only(it, 0.05)
    assert p.action == "WATCH" and p.details.get("entree") is None and p.entry_zone is None
    assert any(b.startswith("WATCH : ratio net < 0.3 au déclencheur (gain ") for b in blocked)
    assert p.stop < p.details["p_max"] < p.details["configuration"]["declencheur"]
    assert rows == [] and folded


def test_pullbacks_keep_their_limit_at_the_maximum_price(db_engine, config):
    """A letter's pullback (support 96, price 100.8): the limit waits at P_max, no seuil — unchanged."""
    from app.web.opportunities_routes import order_book
    from tests.integration.test_engine import seed_pullback_letter

    with db_session() as s:
        seed_pullback_letter(s)
        engine.scan(s, config, DAY, now=NOW)
        props = [p for p in s.query(Proposal).filter(Proposal.isin == ISIN)
                 if (p.details or {}).get("configuration", {}).get("type") == "repli_support"
                 and any("en attente d'un repli" in b for b in p.gates["blocked"])]  # fmt: skip
        assert props
        for p in props:
            assert "seuil" not in (p.details.get("entree") or {}) and p.details["validite"]["seances"] == 3
            waits = [b for b in p.gates["blocked"] if "en attente d'un repli" in b]
            p.gates = {"passed": [], "blocked": waits}  # the score and the gate (R10) are not tested here
            rows = order_book([{"p": p, "name": "TotalEnergies", "snapshot_price": {"last": 100.8}}])
            assert rows
            for r in rows:
                assert r["seuil"] is None and r["distance"] == pytest.approx(100.8 / r["p_max"] - 1)


def _live(last: float):
    from app.data.dto import DataStatus, MarketPerimeter, PriceType, Quote

    return Quote(source="test", market_perimeter=list(MarketPerimeter)[0], received_at=NOW, processed_at=NOW,
                 data_status=DataStatus("realtime"), symbol="TTE", isin=ISIN, market_timestamp=NOW,
                 price_type=PriceType("last"), last=last)  # fmt: skip


@pytest.mark.parametrize("last", [99.3, 98.5])  # under the obstacle low 99.485; under the stop 98.99
def test_a_breakout_given_back_on_a_live_quote_gets_no_plage(db_engine, config, last):
    """Review of 30/10/2026 (M1): tested at its seuil, a d2 confirmed on the close and quoted under its obstacle —
    or under its stop — still got a plage order. R5 is tested on the real price: no entry."""
    with db_session() as s:
        _seed(s, plage_bars(99.7))
        engine.scan(s, config, DAY, now=NOW, quotes={ISIN: _live(last)})
        p = _d2(s)
        blocked = p.gates["blocked"]
    assert p.action == "NO_TRADE" and p.details.get("entree") is None and p.entry_zone is None
    assert any(b.startswith("configuration dégradée : ") for b in blocked)


def test_a_targeted_analysis_in_session_without_a_quote_is_to_verify(db_engine, config):
    """Review of 30/10/2026 (M3): the API reception and the manual review analysed on the stored close while Paris
    traded — « entrée possible » on a close a session old. A targeted analysis of the day in session without a
    quote: stale, « à vérifier ». A full scan (late catch-up, rescan after a risk level change) keeps computing the
    day's J+1 orders on the close (counter-review: the whole day would never recover)."""
    in_session = NOW.replace(hour=8, minute=30)  # 10:30 Paris, Thursday 22/10/2026
    assert engine._in_session("XPAR", in_session) and not engine._in_session("XPAR", NOW)
    with db_session() as s:
        _seed(s, plage_bars(99.7))
        engine.scan(s, config, DAY, now=in_session)  # full scan of the previous session's close
        full = _d2(s)
        assert not full.to_verify and "séance en cours" not in full.details["source_cours"]
        full.deleted_at = in_session
        s.flush()
        engine.scan(s, config, in_session.date(), isins=[ISIN], now=in_session)
        p = _d2(s)
    assert p.to_verify
    assert "séance en cours" in p.details["source_cours"]


def test_a_proposal_to_verify_has_no_order_in_the_book(db_engine, config):
    """Review of 31/10/2026 (rule 2): a stale or delayed quote in session gives no order to post."""
    from app.web.opportunities_routes import order_book

    with db_session() as s:
        _seed(s, plage_bars(99.7))
        engine.scan(s, config, DAY, now=NOW)
        p = _d2(s)
        p.action, p.gates = "BUY", {"passed": [], "blocked": []}
        it = {"p": p, "name": "TotalEnergies", "snapshot_price": {"last": 99.7}}
        assert len(order_book([it])) == 1
        p.to_verify = True
        assert order_book([it]) == []
