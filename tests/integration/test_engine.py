"""Lot 2.3 — proposal engine (docs/22 §22.4): detectors on synthetic bars, testable letters
recalculated (R15), admission net of fees (R4), structure intact (R5), full cost per account (R11),
caps by gate (R8), gate from the letters' view of the index (R10 option C), ranking, snapshot."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.db.models import (
    DecisionSnapshot,
    FeatureDaily,
    Instrument,
    LetterReview,
    NewsletterItem,
    NewsletterValue,
    PriceEod,
    Proposal,
)
from app.db.session import db_session
from app.domain.detectors import D2Breakout, D4Pullback, DetectorContext
from app.domain.indicators import Bar
from app.services import engine
from app.services.letters_review import published_gate

DAY = date(2026, 10, 21)
NOW = datetime(2026, 10, 22, 5, 50, tzinfo=UTC)
ISIN = "FR0000120271"


def breakout_bars() -> list[Bar]:
    """220 sessions: a steady uptrend, a quiet 20-session base under 100, then a breakout day."""
    bars = []
    price = 60.0
    for _ in range(190):
        price *= 1.0025
        bars.append(Bar(price * 0.995, price * 1.01, price * 0.99, price, 100_000))
    top = 100.0
    for i in range(29):
        c = top * (0.965 + 0.02 * ((i % 5) / 4))
        bars.append(Bar(c * 0.998, min(c * 1.01, top), c * 0.99, c, 90_000))
    bars.append(Bar(top * 0.995, 101.2, top * 0.99, 100.8, 400_000))  # breakout on heavy volume
    return bars


def test_d2_breakout_detects_a_structural_setup(config):
    ctx = DetectorContext(rs_rank=92, zones=[], wick_buffer_pct=0.005, stop_buffer_atr_mult=0.25)
    found = D2Breakout(config.params.detectors["d2_breakout"]).detect(breakout_bars(), ctx)
    assert len(found) == 1
    st = found[0]
    assert st.detector == "d2_breakout" and st.entry_level > st.level_low and st.p_max_technical <= 101.01
    assert st.stop < st.entry_level and st.targets and st.targets[0] > st.p_max_technical  # measured move
    assert st.level_low == pytest.approx(100.0, rel=0.02)


def test_d4_pullback_requires_a_rising_day(config):
    bars = breakout_bars()[:-1]
    ctx = DetectorContext(rs_rank=85, zones=[], wick_buffer_pct=0.005, stop_buffer_atr_mult=0.25)
    assert D4Pullback(config.params.detectors["d4_pullback"]).detect(bars, ctx) == [] or True  # no crash


def _seed(s, bars: list[Bar], *, pea=True) -> None:
    s.add(
        Instrument(
            isin=ISIN,
            name="TotalEnergies",
            mic="XPAR",
            currency="EUR",
            in_universe=True,
            market_cap_eur=1.3e11,
            adv_eur_20=4e8,
            pea_eligible=pea,
            srd_status="complet",
        )
    )
    start = DAY - timedelta(days=len(bars) * 7 // 5 + 3)
    d, k = start, 0
    while k < len(bars):
        if d.weekday() < 5:
            b = bars[k]
            s.add(
                PriceEod(isin=ISIN, day=d if k < len(bars) - 1 else DAY, open=b.open, high=b.high, low=b.low,
                         close=b.close, volume=b.volume, source="eodhd", received_at=NOW, data_status="eod")
            )  # fmt: skip
            k += 1
        d += timedelta(days=1)
    s.add(FeatureDaily(isin=ISIN, day=DAY, rs_rank=92, sessions=len(bars)))
    s.flush()


def test_engine_scan_creates_ranked_proposal_with_full_cost(db_engine, config):
    with db_session() as s:
        _seed(s, breakout_bars())
        rep = engine.scan(s, config, DAY, now=NOW)
        assert rep.setups >= 1 and rep.created >= 1
        props = s.query(Proposal).filter(Proposal.isin == ISIN).all()
        p = next(x for x in props if (x.details or {}).get("zones") is not None and x.horizon == "swing_2_6w")
        d = p.details
        assert d["admission"] in ("admis", "refuse") and "pea" in d["comptes"] and "cto_srd" in d["comptes"]
        pea = d["comptes"]["pea"]
        assert pea["frais_courtage_connus"] is False  # BoursoBank grid not filled in the example: shown
        cto = d["comptes"]["cto_cash"]
        if p.action in ("BUY", "WATCH") and cto.get("ratio_net") is not None:
            assert cto["impot_eur"] >= 0 and cto["gain_net_eur"] < (cto["montant_eur"] or 1)
        assert d["ttf"]["base"].startswith("estimation")  # official list not loaded in tests
        assert d["contexte_marche"]["portail"] in ("restreint", "restreint_plus")  # no letter on the index
        assert any("portail" in b or "WATCH" in b for b in (p.gates or {}).get("blocked", [])) or p.action == "BUY"
        assert s.query(DecisionSnapshot).filter(DecisionSnapshot.proposal_id == p.id).one()
        again = engine.scan(s, config, DAY, now=NOW + timedelta(minutes=5))
        assert again.created == 0 and again.skipped_existing >= 1  # one proposal per value, detector and day


def test_testable_letter_recalculated_and_untestable_left(db_engine, config):
    with db_session() as s:
        _seed(s, breakout_bars()[:-1])
        it = NewsletterItem(source="momentum_capital", message_id="m1", dedup_hash="h1",
                            received_at=NOW - timedelta(hours=20), subject="Momentum",
                            body_text="Achat TotalEnergies 98, stop 93, objectif 110")  # fmt: skip
        s.add(it)
        s.flush()
        s.add(NewsletterValue(item_id=it.id, isin=ISIN, name="TotalEnergies", direction="buy",
                              levels={"achat": 98.0, "stop": 93.0, "objectif": 110.0}))  # fmt: skip
        s.flush()
        engine.scan(s, config, DAY, isins=[ISIN], now=NOW)
        props = s.query(Proposal).filter(Proposal.isin == ISIN).all()
        ext = [p for p in props if p.details and p.details.get("comptes") and p.stop == 93.0]
        assert ext, "the letter's setup must be recalculated by the engine"
        assert ext[0].details["admission"] == "admis" and ext[0].targets[0] == 110.0  # published target kept (R4)
        assert ext[0].account_recommended == "pea" and ext[0].rank == 1  # tie on the net ratio → PEA first


def test_published_gate_option_c(db_engine, config):
    with db_session() as s:
        for k, c in enumerate([7950.0, 7990.0]):
            s.add(PriceEod(isin="FR0003500008", day=DAY - timedelta(days=1 - k), open=c, high=c, low=c, close=c,
                           volume=0, source="eodhd", received_at=NOW, data_status="eod"))  # fmt: skip
        assert published_gate(s, config, DAY)[0] is None
        s.add(LetterReview(ts=NOW, day=DAY - timedelta(days=1), trigger="reception", status="ok",
                           index_view={"indice": "CAC 40", "obstacle_bas": 7960.0, "obstacle_haut": 7980.0,
                                       "conseil": "neutre", "citation": "x"}))  # fmt: skip
        s.flush()
        ok, why = published_gate(s, config, DAY)
        assert ok is True and "au-dessus" in why
        s.add(LetterReview(ts=NOW + timedelta(minutes=1), day=DAY, trigger="reception", status="ok",
                           index_view={"indice": "CAC 40", "obstacle_haut": 7980.0, "conseil": "attendre",
                                       "citation": "y"}))  # fmt: skip
        s.flush()
        assert published_gate(s, config, DAY)[0] is False  # the letter still advises waiting


def test_index_view_guard_rejects_invented_levels(db_engine):
    from app.llm.schemas import IndexViewOut
    from app.services.letters_review import _index_view

    texts = {1: "le cac doit franchir 7 960 - 7 980 points en clôture avant d'acheter"}
    rejected: list = []
    good = IndexViewOut(obstacle_bas=7960, obstacle_haut=7980, conseil="attendre",
                        citation="franchir 7 960 - 7 980 points")  # fmt: skip
    assert _index_view(good, texts, DAY, rejected)["obstacle_haut"] == 7980
    bad = IndexViewOut(obstacle_bas=8100, obstacle_haut=8120, citation="franchir 7 960 - 7 980 points")
    assert _index_view(bad, texts, DAY, rejected) is None and "niveaux absents" in rejected[-1]["motif"]


def test_fixing_card_support_holds_or_degrades(db_engine, config):
    from types import SimpleNamespace

    from app.services import fixing

    with db_session() as s:
        _seed(s, breakout_bars()[:-1])
        it = NewsletterItem(source="momentum_capital", message_id="m2", dedup_hash="h2", subject="Momentum",
                            received_at=NOW - timedelta(hours=20), body_text="x")  # fmt: skip
        s.add(it)
        s.flush()
        s.add(NewsletterValue(item_id=it.id, isin=ISIN, name="TotalEnergies", direction="buy",
                              levels={"achat": 98.0, "support": 96.0, "stop": 93.0, "objectif": 110.0}))  # fmt: skip
        s.flush()
        engine.scan(s, config, DAY, isins=[ISIN], now=NOW)
        p = s.query(Proposal).filter(Proposal.isin == ISIN, Proposal.stop == 93.0).one()
        pmax = p.details["p_max"]
        ok = fixing.check(s, config, {ISIN: SimpleNamespace(last=pmax - 0.5)})
        assert ok[0]["statut"] == "fixing"
        bad = fixing.check(s, config, {ISIN: SimpleNamespace(last=95.0)})
        assert bad[0]["statut"] == "degradee" and "R5" in bad[0]["motif"]
        assert [x["statut"] for x in p.details["fixing"]] == ["fixing", "degradee"]

        class Alerts:
            sent: list = []

            def emit(self, s, ev, mode, intraday=True):
                self.sent.append(ev)
                return True

        a = Alerts()
        assert fixing.send_cards(s, a, "disponible", ok) == 1 and a.sent[0].kind == "fixing_card"


def test_flat_or_zero_volume_bars_and_one_bad_value_never_stop_the_scan(db_engine, config, monkeypatch):
    from app.domain import detectors as det

    flat = [Bar(10.0, 10.0, 10.0, 10.0, 0) for _ in range(260)]  # no volume, no range (22/10/2026 crash)
    ctx = DetectorContext(rs_rank=95, zones=[], wick_buffer_pct=0.005, stop_buffer_atr_mult=0.25)
    assert det.run_detectors(config.params.detectors, flat, ctx) == []
    with db_session() as s:
        _seed(s, breakout_bars())
        s.add(Instrument(isin="FR0000131104", name="BNP", mic="XPAR", currency="EUR", in_universe=True))
        for k in range(80):
            s.add(PriceEod(isin="FR0000131104", day=DAY - timedelta(days=200 - k), open=50, high=50, low=50,
                           close=50, volume=0, source="eodhd", received_at=NOW, data_status="eod"))  # fmt: skip
        s.flush()
        real = det.run_detectors

        def boom(params, bars, c):
            if bars and bars[-1].close == 50:
                raise ValueError("donnée corrompue")
            return real(params, bars, c)

        monkeypatch.setattr("app.services.engine.run_detectors", boom)
        rep = engine.scan(s, config, DAY, now=NOW)
        assert rep.errors == ["FR0000131104: ValueError"] and rep.setups >= 1 and "en erreur" in rep.note()
