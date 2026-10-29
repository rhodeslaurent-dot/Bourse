"""Review of 29/10/2026: a limit waiting for a pullback (WATCH) can become an entry intraday — the score, the
market gate (R10) and the events a stop does not cover (R14) are tested at that limit at creation, as for a BUY."""

from __future__ import annotations

from datetime import timedelta

from app.db.models import Instrument, Proposal
from app.db.session import db_session
from app.services import engine
from tests.integration.test_engine import DAY, ISIN, NOW, _seed, breakout_bars


def test_a_waiting_limit_carries_the_earnings_block_and_its_score(db_engine, config):
    import copy

    config = copy.deepcopy(config)
    config.params.admission["min_reward_risk"] = 0.3  # the synthetic target is close: a limit above the stop
    with db_session() as s:
        _seed(s, breakout_bars())
        s.get(Instrument, ISIN).earnings_next_date = NOW.date() + timedelta(days=1)  # tomorrow
        s.flush()
        engine.scan(s, config, DAY, now=NOW)
        waiting = [
            p
            for p in s.query(Proposal).filter(Proposal.isin == ISIN)
            if any("en attente d'un repli" in b for b in (p.gates or {}).get("blocked", []))
        ]
        assert waiting, "the synthetic run above the limit must give a waiting limit"
        for p in waiting:
            assert p.details["p_max"] > p.stop
            blocked = p.gates["blocked"]
            assert any(b.startswith("BLOQUÉ : publication de résultats") for b in blocked)
            assert "score" in p.details and "portail" in p.details
            # the score reasons come before the waiting one: the intraday follow-up ignores such a limit
            assert any(b.startswith("WATCH : ratio d'admission") for b in blocked) or p.details["score"]


def test_a_limit_under_its_own_stop_is_no_order(db_engine, config):
    with db_session() as s:
        _seed(s, breakout_bars())  # at k = 2 the limit (98.3) falls under the stop (99.0)
        engine.scan(s, config, DAY, now=NOW)
        blocked = [
            b for p in s.query(Proposal).filter(Proposal.isin == ISIN) for b in (p.gates or {}).get("blocked", [])
        ]
    assert any(b.startswith("WATCH : objectif trop proche") for b in blocked)
    assert not any("en attente d'un repli" in b for b in blocked)


def test_fees_are_estimated_in_euros_for_a_value_quoted_in_pence(db_engine, config):
    """29/10/2026: London cards showed a limit far under the stop (Seplat: 481.5 for a stop at 763.3) — the
    risk per share was left in pence, the estimate gave 2 shares and the minimum commission weighed 40 %."""
    from app.db.models import FxRate
    from app.domain.indicators import Bar

    pence = [Bar(b.open * 8, b.high * 8, b.low * 8, b.close * 8, b.volume) for b in breakout_bars()]
    with db_session() as s:
        _seed(s, pence, pea=False)
        inst = s.get(Instrument, ISIN)
        inst.currency, inst.adv_eur_20 = "GBX", 4e8
        s.add(FxRate(pair="EURGBP", day=DAY, rate=0.86, source="test"))
        s.flush()
        engine.scan(s, config, DAY, now=NOW)
        props = list(s.query(Proposal).filter(Proposal.isin == ISIN))
        assert props
        for p in props:
            for acc, row in (p.details.get("comptes") or {}).items():
                assert row["frais_aller_retour_pct"] < 0.02, (acc, row)  # a few € on ~1 000 €, not 40 %
            if p.details.get("p_max") is not None and p.stop is not None:
                waiting = any("en attente d'un repli" in b for b in p.gates.get("blocked", []))
                assert not waiting or p.details["p_max"] > p.stop  # never a limit under its own stop


def test_a_range_exit_letter_is_recalculated_as_a_breakout(db_engine, config):
    """Viridien, 29/10/2026: « sortie du range au-dessus de 100,20, objectif 109,10, invalidé sous 97 » has no buy
    level: the trigger is the entry and the range top is the obstacle a close must hold (R5)."""
    from app.db.models import NewsletterItem, NewsletterValue, Signal

    with db_session() as s:
        _seed(s, breakout_bars())
        it = NewsletterItem(source="momentum_capital", message_id="m", dedup_hash="h",
                            received_at=NOW - timedelta(hours=1), subject="sortie par le haut ?",
                            body_text="(privé)")  # fmt: skip
        s.add(it)
        s.flush()
        s.add(NewsletterValue(item_id=it.id, isin=ISIN, name="TotalEnergies", direction="buy",
                              levels={"Déclencheur": 100.2, "objectif 1": 109.1, "invalidation": 97.0}))  # fmt: skip
        s.flush()
        engine.scan(s, config, DAY, isins=[ISIN], now=NOW)
        letter = [
            p
            for p in s.query(Proposal).filter(Proposal.isin == ISIN)
            if (s.get(Signal, p.signal_id).source == "moteur_lettre")
        ]
    assert letter, "the range exit must reach the engine"
    p = letter[0]
    assert p.stop == 97.0 and p.targets[0] == 109.1
    assert p.details["admission"] in ("admis", "refuse", "degrade")
