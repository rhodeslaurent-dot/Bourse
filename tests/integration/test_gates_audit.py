"""Audit of the decision funnel (29/10/2026): B1 (R10 compares the ratio net of fees), B2 (score basis of the
restricted gate), B3 (reserved risk of pending orders in the caps), caps on the interface capital, B4 (macro event
of the day in block M), missing data on the card, R14 measured for earnings and for macro families with a loaded
history, risk « élevé » policy, gates-coverage."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.data.providers.macro_history import PastEvent
from app.db.models import Account, EarningsEvent, Instrument, MacroEvent, MarketRegimeRow, Order, PriceEod, Proposal
from app.db.session import db_session
from app.domain.admission import Setup as SetupKind
from app.domain.detectors import Setup
from app.domain.indicators import compute_daily_features
from app.domain.scoring import Score
from app.services import engine, gates_coverage, macro_history
from tests.integration.test_engine import DAY, ISIN, NOW, _seed, breakout_bars
from tests.integration.test_opportunities_page import _proposal, client  # noqa: F401 — fixture reused

CIRCUIT = SimpleNamespace(tripped=False, reason="", as_dict=lambda: {"ok": True})


def _cfg(config, **mg):
    c = copy.deepcopy(config)
    c.params.admission["min_reward_risk"] = 2.8
    c.params.pea["no_withdrawal_before_5y"] = False  # PEA younger than 5 years, unknown opening: PFU (≈ 31 %)
    c.params.market_gate.update(mg)
    return c


def _fake(total=80.0, ratio=0.9, risk="faible", seen=None):
    real = engine.score

    def fake(si, *a, **kw):
        if seen is not None:
            seen.append(si)
        sc = real(si, *a, **kw)
        return Score(sc.blocks, total, sc.attainable, ratio, 4, risk, sc.explanations)

    return fake


def _evaluate(s, config, stop=95.0, target=121.0, gate="restreint", ref=None):
    setup = Setup("d4_pullback", SetupKind.PULLBACK, 100.0, 101.0, stop, "structure", 99.0, [target], "swing_2_6w")
    inst = s.get(Instrument, ISIN)
    return engine.evaluate(s, config, inst, setup, "moteur", 100.0, "eod", engine.Book([], {}, {}), gate, "green",
                           None, NOW, None, circuit=CIRCUIT, ref=ref)  # fmt: skip


@pytest.fixture
def pea_only(monkeypatch):
    monkeypatch.setattr(engine, "_account_options", lambda inst, config: ["pea"])


def test_b1_restricted_gate_compares_the_ratio_net_of_fees(db_engine, config, monkeypatch, pea_only):
    monkeypatch.setattr(engine, "score", _fake())
    with db_session() as s:
        _seed(s, breakout_bars())
        ev = _evaluate(s, _cfg(config))
    g, pea = ev.details["portail_restreint"], ev.details["comptes"]["pea"]
    assert ev.account == "pea" and g["ratio_net_frais"] >= 2.5 and pea["ratio_net"] < 2.0  # ≈ 2.7 vs ≈ 1.9
    assert g["ok"] and ev.action == "BUY", ev.blocked
    assert not any("portail restreint" in b for b in ev.blocked)


def test_b2_restricted_score_basis(db_engine, config, monkeypatch, pea_only):
    with db_session() as s:
        _seed(s, breakout_bars())
        monkeypatch.setattr(engine, "score", _fake(total=70.0, ratio=0.9))
        ev = _evaluate(s, _cfg(config))  # default « total »: 70 < 75
        assert ev.action == "WATCH" and any(b.startswith("WATCH : portail restreint — ") for b in ev.blocked)
        ev = _evaluate(s, _cfg(config, restricted_score_basis="admission_ratio"))  # 0.90 ≥ 0.85
        assert ev.action == "BUY" and ev.details["portail_restreint"]["base"] == "admission_ratio"
        monkeypatch.setattr(engine, "score", _fake(total=95.0, ratio=0.8))
        ev = _evaluate(s, _cfg(config, restricted_score_basis="admission_ratio"))
        assert ev.action == "WATCH" and "ratio d'admission 0,80" in " ".join(ev.blocked)


def test_high_risk_policy_watch_or_half_size(db_engine, config, monkeypatch, pea_only):
    adr = compute_daily_features(breakout_bars()).adr20
    assert adr
    stop = round(100 * (1 - adr), 2)  # ≈ 1 ADR: allowed for the half size
    target = round(3.8 * 100 / 0.98 - 2.8 * stop + 1, 1)  # P_max above the price, PEA fees included
    with db_session() as s:
        _seed(s, breakout_bars())
        monkeypatch.setattr(engine, "score", _fake())
        full = _evaluate(s, _cfg(config), stop=stop, target=target)
        monkeypatch.setattr(engine, "score", _fake(risk="eleve"))
        watch = _evaluate(s, _cfg(config), stop=stop, target=target)
        assert watch.action == "WATCH" and any(b.startswith("WATCH : risque élevé (") for b in watch.blocked)
        c = _cfg(config)
        c.params.scoring["high_risk_policy"] = "half_size"
        half = _evaluate(s, c, stop=stop, target=target)
        far = _evaluate(s, c)  # stop at 95: far more than 1.5 ADR → WATCH
    assert full.action == "BUY" and half.action == "BUY", half.blocked
    assert 0 < half.shares <= full.shares // 2 + 1 and half.details["comptes"]["pea"]["titres"] == half.shares
    assert half.details["reductions_taille"]["risque élevé"] == 0.5
    assert far.action == "WATCH" and any("ADR exigé" in b for b in far.blocked)


def test_b4_and_missing_data(db_engine, config, monkeypatch, pea_only):
    seen: list = []
    monkeypatch.setattr(engine, "score", _fake(seen=seen))
    with db_session() as s:
        _seed(s, breakout_bars())
        ev = _evaluate(s, _cfg(config))
        assert seen[-1].major_macro_event_today is False
        s.add(MacroEvent(ts=NOW + timedelta(hours=8), currency="USD", title="Federal Funds Rate", impact="High",
                         source="forexfactory", received_at=NOW))  # fmt: skip
        s.flush()
        s.info.clear()
        _evaluate(s, _cfg(config))
        assert seen[-1].major_macro_event_today is True  # docs/06 §6.5: no « +2 » on a Fed day
    miss = {m["donnee"]: m for m in ev.details["donnees_manquantes"]}
    assert miss["date des prochains résultats"]["points"] > 0 and "force du secteur" in miss
    assert "fondamentaux" in miss and "positions courtes" in miss


def test_caps_count_pending_orders_on_the_interface_capital(db_engine, config):
    setup = Setup("d4_pullback", SetupKind.PULLBACK, 100.0, 101.0, 95.0, "structure", 99.0, [116.0], "swing_2_6w")
    with db_session() as s:
        _seed(s, breakout_bars())
        inst = s.get(Instrument, ISIN)
        ev = engine.Evaluation(ISIN, "d2_breakout", "moteur", setup, "WATCH", 100.0, "eod")
        assert engine._portfolio_caps(s, config, inst, engine.Book([], {}, {}), 150.0, "cto_cash", "restreint",
                                      300.0, ev, 20_000.0) == []  # fmt: skip
        pct = config.params.risk.max_open_risk_pct_by_gate["restreint"]
        assert ev.details["plafonds"]["plafond_eur"] == pytest.approx(20_000 * pct)  # the interface capital
        acc = Account(broker="saxo", type="cto_cash", label="CTO")
        s.add(acc)
        s.flush()
        other = "FR0000131104"  # another value: the candidate's own pending order is replaced, not added (m5)
        prop = Proposal(signal_id=1, isin=other, action="BUY", stop=95.0)
        s.add(prop)
        s.flush()
        s.add(Order(account_id=acc.id, isin=other, side="buy", qty=180, order_type="limite", limit_price=100.0,
                    state="entered", proposal_id=prop.id, source="web", entered_at=NOW))  # fmt: skip
        s.add(Order(account_id=acc.id, isin=ISIN, side="buy", qty=50, order_type="limite", limit_price=100.0,
                    state="filled", qty_filled=50, source="web", entered_at=NOW))  # fmt: skip
        s.flush()
        caps = engine._portfolio_caps(s, config, inst, engine.Book([], {}, {}), 150.0, "cto_cash", "restreint", 300.0,
                                      ev, 20_000.0)  # fmt: skip
    pl = ev.details["plafonds"]
    assert pl["ordres_en_attente"] == 1 and 180 * (100 - 95 * 0.998) < pl["risque_reserve_eur"] < 990
    assert any(c.startswith("BLOQUÉ : risque ouvert après entrée") for c in caps)  # 0 € reserved before (B3)


def _days(s, n: int, step: int = 5):
    days = [d for (d,) in s.query(PriceEod.day).filter(PriceEod.isin == ISIN).order_by(PriceEod.day)]
    return [days[-60 + k * step] for k in range(n)]


def _ev():
    setup = Setup("d4_pullback", SetupKind.PULLBACK, 100.0, 101.0, 95.0, "structure", 99.0, [116.0], "swing_2_6w")
    return engine.Evaluation(ISIN, "d2_breakout", "moteur", setup, "WATCH", 100.0, "eod")


@pytest.mark.parametrize("n", [2, 8])
def test_r14_earnings_exception_measured(db_engine, config, n):
    with db_session() as s:
        _seed(s, breakout_bars())
        inst = s.get(Instrument, ISIN)
        inst.earnings_next_date = (NOW + timedelta(days=1)).date()
        for d in _days(s, n):
            s.add(EarningsEvent(isin=ISIN, day=d, when="amc", source="yahoo", received_at=NOW))
        s.flush()
        ev = _ev()
        out = engine._events_blocks(s, config, inst, 100.0, 95.0 * 0.998, NOW, ev)
    if n < 4:
        assert any(b.startswith("BLOQUÉ : publication de résultats") and "(2/4)" in b for b in out)
    else:
        assert not out and any("exception R14" in x for x in ev.passed)
        assert ev.details["resultats"]["historique"] == 8 and "faible" in ev.details["resultats"]["exposition"]


@pytest.mark.parametrize("n", [2, 8])
def test_r14_macro_measured_on_the_loaded_family_history(db_engine, config, n):
    with db_session() as s:
        _seed(s, breakout_bars())
        inst = s.get(Instrument, ISIN)
        s.add(MacroEvent(ts=NOW + timedelta(hours=10), currency="USD", title="FOMC Statement", impact="High",
                         source="forexfactory", received_at=NOW))  # fmt: skip
        added, _ = macro_history.store(s, config, [PastEvent("fed_decision", "USD", d, None, "fed_calendrier_fomc")
                                                   for d in _days(s, n)], now=NOW)  # fmt: skip
        assert added == n
        ev = _ev()
        out = engine._events_blocks(s, config, inst, 100.0, 95.0 * 0.998, NOW, ev)
    if n < 4:
        assert out and "historique en cours de constitution (2/4)" in out[0]
    else:
        assert out == [] and "faible" in ev.details["annonces"][0]["exposition"]


def test_macro_history_load_chain_and_dedup(db_engine, config):
    from pathlib import Path

    fix = Path(__file__).resolve().parents[1] / "fixtures" / "macro_history"
    pages = {"ecb": "ecb_mopo.html", "federalreserve.gov/monetarypolicy/fomccalendars": "fed_fomccalendars.html",
             "bls.gov/bls/news-release/cpi": "bls_cpi.html"}  # fmt: skip

    def fetch(url):
        for k, f in pages.items():
            if k in url and "index_include" not in url and "historical" not in url:
                return (fix / f).read_text()
        return None  # 404

    class Refused:
        def _get(self, path, **kw):
            from app.data.base import ProviderError

            raise ProviderError("EODHD 403 on /economic-events")

    with db_session() as s:
        rep = macro_history.load(s, config, datetime(2017, 1, 1).date(), DAY, eodhd=Refused(), fetch=fetch, now=NOW)
        assert rep.unavailable and "EODHD" in rep.unavailable[0]
        assert rep.added == {"bce_decisions_officielles": 3, "fed_calendrier_fomc": 4, "bls_archives": 3}
        again = macro_history.load(s, config, datetime(2017, 1, 1).date(), DAY, eodhd=Refused(), fetch=fetch, now=NOW)
        assert sum(again.added.values()) == 0 and again.already_known == 10
        row = s.query(MacroEvent).filter(MacroEvent.source == "fed_calendrier_fomc").first()
        assert row.title in ("Federal Funds Rate", "FOMC Meeting Minutes") and row.received_at is not None
        cov = macro_history.coverage(s, config, NOW)
    assert cov["fed_decision"]["dates"] == 2 and not cov["fed_decision"]["mesurable"]


def test_gates_coverage_report(db_engine, config):
    with db_session() as s:
        _seed(s, breakout_bars())
        s.add(MarketRegimeRow(day=DAY, market="EU", ts=NOW, regime="orange"))
        s.add(Proposal(signal_id=1, isin=ISIN, action="WATCH", created_at=NOW,
                       details={"score": {"ratio_admission": 0.7}, "contexte_marche": {"regime": "orange"}},
                       gates={"blocked": ["WATCH : ratio d'admission 0.70 < 0.80 (scoring)",
                                          "BLOQUÉ : USD Federal Funds Rate dans les 30 h — x"]}))  # fmt: skip
        s.flush()
        rep = gates_coverage.coverage(s, config, DAY - timedelta(days=3), DAY + timedelta(days=1))
    assert rep["par_place"]["XPAR"]["valeurs"] == 1 and rep["regimes"]["EU"]["orange"] == 1.0
    assert rep["ratio_admission_par_regime"]["orange"]["n"] == 1
    assert rep["blocages"]["WATCH : ratio d'admission <"] == 1 and "Couverture" in gates_coverage.text(rep)


def test_missing_data_line_on_the_card(client):  # noqa: F811
    with db_session() as s:
        s.add(Instrument(isin="FR0000120271", name="TotalEnergies", ticker_local="TTE", mic="XPAR"))
        p = _proposal(s)
        p.details = {"donnees_manquantes": [
            {"donnee": "date des prochains résultats", "bloc": "R", "points": 4, "note": "inconnue → +0"},
            {"donnee": "fondamentaux", "bloc": "F", "points": None, "note": "1/5 critères (< 2 : bloc F non attribué)"},
        ]}  # fmt: skip
    html = client.get("/opportunites").text
    assert "Données manquantes : " in html and "date des prochains résultats −4 pt R" in html and "1/5" in html


def test_waiting_limit_caps_use_the_interface_capital(db_engine, config):
    ref = SimpleNamespace(capital_eur=20_000.0, risk_pct=0.0075, max_trade_pct=0.01)
    c = copy.deepcopy(config)
    c.params.admission["min_reward_risk"] = 0.3
    with db_session() as s:
        _seed(s, breakout_bars())
        ev = engine.evaluate(s, c, s.get(Instrument, ISIN), Setup("d4_pullback", SetupKind.PULLBACK, 100.0, 101.0,
                             95.0, "structure", 99.0, [103.0], "swing_2_6w"), "moteur", 102.0, "eod",
                             engine.Book([], {}, {}), "restreint", "green", None, NOW, None, circuit=CIRCUIT,
                             ref=ref)  # fmt: skip
    assert any("attente d'un repli" in b for b in ev.blocked)
    pct = config.params.risk.max_open_risk_pct_by_gate["restreint"]
    assert ev.details["plafonds"]["capital_eur"] == 20_000.0
    assert ev.details["plafonds"]["plafond_eur"] == pytest.approx(20_000 * pct)
    assert datetime.now(UTC) > NOW


def test_a_proposal_marked_order_entered_reserves_its_risk(db_engine, config):
    """Review of 30/10/2026 (M2): « Ordre saisi » creates no order row; the proposal reserves its risk at its limit
    until it expires, unless an order is declared for it or the value is held since. The candidate's own pending
    entry is never counted twice (m5)."""
    from app.services.decisions import record_decision

    with db_session() as s:
        _seed(s, breakout_bars())
        inst = s.get(Instrument, ISIN)
        other = "FR0000131104"
        taken = Proposal(signal_id=1, isin=other, action="BUY", stop=95.0, risk_eur=400.0, account_recommended="pea",
                         expires_at=NOW + timedelta(days=2))  # fmt: skip
        old = Proposal(signal_id=2, isin=other, action="BUY", stop=95.0, risk_eur=300.0, account_recommended="pea",
                       expires_at=NOW - timedelta(hours=1))  # fmt: skip
        same = Proposal(signal_id=3, isin=ISIN, action="BUY", stop=95.0, risk_eur=250.0, account_recommended="pea",
                        expires_at=NOW + timedelta(days=2))  # fmt: skip
        s.add_all([taken, old, same])
        s.flush()
        for p in (taken, old, same):
            record_decision(s, p.id, "order_entered", "web")
        ev = _ev()
        book = engine.Book([], {}, {}, as_of=NOW)
        engine._portfolio_caps(s, config, inst, book, 150.0, "pea", "restreint", 300.0, ev, 20_000.0)
        pl = ev.details["plafonds"]
        assert pl["risque_reserve_eur"] == pytest.approx(400.0)  # the expired one and the value's own excluded
        assert pl["ordre_en_attente_remplace_eur"] == pytest.approx(250.0)
        assert {r.get("proposition") for r in book.reserved_orders} == {taken.id, same.id}
        acc = Account(broker="boursobank", type="pea", label="PEA")
        s.add(acc)
        s.flush()
        s.add(Order(account_id=acc.id, isin=other, side="buy", qty=10, order_type="limite", limit_price=100.0,
                    state="filled", qty_filled=10, proposal_id=taken.id, source="web", entered_at=NOW))  # fmt: skip
        s.flush()
        total, rows = engine._reserved_risk(s, config, 300.0, NOW)
    assert all(r.get("proposition") != taken.id for r in rows)  # its order is declared: the order counts, once


def test_a_taken_proposal_is_not_counted_twice_with_its_unlinked_order_or_fill(db_engine, config):
    """Counter-review of 30/10/2026: « Ordre saisi » then « /ordre » on Telegram (no proposal link) reserved ~2 R; a
    fill imported with a date only (midnight) did not end the reservation."""
    from app.db.models import PositionRow
    from app.services.decisions import record_decision

    with db_session() as s:
        other = "FR0000131104"
        acc = Account(broker="boursobank", type="pea", label="PEA")
        s.add(acc)
        a = Proposal(signal_id=1, isin=other, action="BUY", stop=95.0, risk_eur=400.0, account_recommended="pea",
                     expires_at=NOW + timedelta(days=2), created_at=NOW - timedelta(hours=2))  # fmt: skip
        b = Proposal(signal_id=2, isin=ISIN, action="BUY", stop=95.0, risk_eur=300.0, account_recommended="pea",
                     expires_at=NOW + timedelta(days=2), created_at=NOW - timedelta(hours=2))  # fmt: skip
        s.add_all([a, b])
        s.flush()
        record_decision(s, a.id, "order_entered", "web")
        record_decision(s, b.id, "order_entered", "web")
        s.add(Order(account_id=acc.id, isin=other, side="buy", qty=4, order_type="limite", limit_price=100.0,
                    state="entered", source="telegram", entered_at=NOW - timedelta(hours=1)))  # fmt: skip
        midnight = NOW.replace(hour=0, minute=0)  # a broker CSV: the day only
        s.add(PositionRow(account_id=acc.id, isin=ISIN, qty_held=3, avg_price=100.0, opened_at=midnight))
        s.flush()
        total, rows = engine._reserved_risk(s, config, 300.0, NOW)
    assert [r.get("proposition") for r in rows] == [None]  # the declared order only, the fill ends b
