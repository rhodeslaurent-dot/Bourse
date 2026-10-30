"""docs/37 lot L0 — graduated verdict behind ``verdict.enabled`` (OFF by default, decisions D9–D15 awaiting the user).

- the group absent, present but disabled, or not yet in effect: the engine's output is byte-for-byte the same;
- enabled: a d7 setup (ADR-013 watch only) becomes a speculative BUY at half size, entered by the plan n° 4 plage
  (P2) with a stop 2.5 ATR under the entry (D15); caps and R14 still block, a stale quote in session stays
  « à vérifier », a letter's breakout not crossed still waits; a crossed breakout failing R4 on target 1 only uses
  its last target (D12); Telegram sends a speculative card only with ``notify_speculative``;
- ``verdict-preview`` recomputes, read only, the level of each stored proposal of a day.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
import yaml
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from app.config import validate_params
from app.db.models import (
    DecisionSnapshot,
    FeatureDaily,
    Instrument,
    NewsletterItem,
    NewsletterValue,
    PriceEod,
    Proposal,
    Signal,
)
from app.db.session import configure_engine, create_all_for_tests, db_session
from app.domain.entry_policies import tick_size
from app.domain.indicators import Bar, compute_daily_features
from app.services import engine
from tests.integration.test_engine import DAY, ISIN, NOW, breakout_bars
from tests.integration.test_engine_breakout_plage import plage_bars

ISIN_D7 = "FR0000121014"
SPECULATIVE = ["d2_breakout", "d7_trend_quality"]
ON = {"enabled": True, "effective_from": "2026-10-01"}  # the fixtures are dated 21/10/2026


def d7_bars() -> list[Bar]:
    """225 sessions: a long rise (MM50 > MM200, both rising), a nine-session pullback to the MM50, a bounce day that
    closes up — the d7 « tendance + qualité » setup (entry ≈ 108.75, R1 stop ≈ 105.17, 52-week high ≈ 115.16)."""
    bars, price = [], 60.0
    for _ in range(215):
        price *= 1.0030
        bars.append(Bar(price * 0.996, price * 1.008, price * 0.99, price, 100_000))
    for _ in range(9):
        price *= 0.993
        bars.append(Bar(price * 1.004, price * 1.006, price * 0.992, price, 90_000))
    bars.append(Bar(price * 0.997, price * 1.012, price * 0.985, price * 1.008, 120_000))
    return bars


def _seed_value(s, isin: str, name: str, bars: list[Bar]) -> None:
    s.add(Instrument(isin=isin, name=name, mic="XPAR", currency="EUR", in_universe=True, market_cap_eur=1.3e11,
                     adv_eur_20=4e8, pea_eligible=True, srd_status="complet"))  # fmt: skip
    d, k = DAY - timedelta(days=len(bars) * 7 // 5 + 3), 0
    while k < len(bars):
        if d.weekday() < 5:
            b = bars[k]
            day = d if k < len(bars) - 1 else DAY
            s.add(PriceEod(isin=isin, day=day, open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume,
                           source="eodhd", received_at=NOW, data_status="eod"))  # fmt: skip
            k += 1
        d += timedelta(days=1)
    s.add(FeatureDaily(isin=isin, day=DAY, rs_rank=92, sessions=len(bars)))
    s.flush()


def _seed_both(s) -> None:
    _seed_value(s, ISIN, "TotalEnergies", plage_bars(99.7))  # a d2 breakout, its plage armed for J+1
    _seed_value(s, ISIN_D7, "LVMH", d7_bars())  # a d7 pullback


def _cfg(raw: dict, *, verdict: dict | None | str = "keep", watch_only: list[str] | None = None):
    out = copy.deepcopy(raw)
    out["scoring"]["watch_only_detectors"] = list(SPECULATIVE if watch_only is None else watch_only)  # ADR-013
    if verdict is None:
        out.pop("verdict", None)
    elif isinstance(verdict, dict):
        out["verdict"] = {**out["verdict"], **verdict}
    return validate_params(out, sha256="test")


def _fresh_db():
    eng = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_all_for_tests(eng)
    configure_engine(eng)
    return eng


def _snapshot(s) -> list[str]:
    """Everything the engine stored, timestamps of the database rows aside, as JSON text (order of keys kept)."""
    out = []
    for p in s.scalars(select(Proposal).order_by(Proposal.isin, Proposal.id)):
        sig = s.get(Signal, p.signal_id)
        snap = s.scalar(select(DecisionSnapshot).where(DecisionSnapshot.proposal_id == p.id))
        row = {
            "isin": p.isin, "detecteur": sig.detector, "action": p.action, "gates": p.gates, "details": p.details,
            "entry_zone": p.entry_zone, "stop": p.stop, "targets": p.targets, "shares": p.shares,
            "size_eur": p.size_eur, "risk_eur": p.risk_eur, "account": p.account_recommended, "rank": p.rank,
            "to_verify": p.to_verify, "horizon": p.horizon, "expires_at": p.expires_at, "cost": p.cost_estimate,
            "snapshot": [snap.quotes, snap.features, snap.regime, snap.portfolio_state],
            "signal": [sig.evidence, sig.entry_low, sig.entry_high, sig.stop_initial, sig.targets, sig.horizon,
                       sig.data_status, sig.source],
        }  # fmt: skip
        out.append(json.dumps(row, default=str, ensure_ascii=False))
    return out


def _run(cfg, seed=_seed_both, **scan_kw) -> tuple[list[str], str]:
    eng = _fresh_db()
    try:
        with db_session() as s:
            seed(s)
            rep = engine.scan(s, cfg, DAY, now=NOW, **scan_kw)
            return _snapshot(s), rep.note()
    finally:
        eng.dispose()


def test_absent_disabled_or_future_scale_changes_nothing(raw):
    """The proof asked before any decision: with the ``verdict`` group absent, present with ``enabled: false``, or
    enabled but not yet in effect, the engine stores exactly the same proposals, gates, details and snapshots."""
    assert raw["verdict"]["enabled"] is False  # the example ships the scale OFF
    absent = _run(_cfg(raw, verdict=None))
    disabled = _run(_cfg(raw))
    future = _run(_cfg(raw, verdict={"enabled": True, "effective_from": "2099-01-01"}))
    assert absent[0], "the fixtures must produce proposals (d2 and d7)"
    assert {json.loads(r)["detecteur"] for r in absent[0]} >= {"d2_breakout", "d7_trend_quality"}
    assert all("(ADR-013)" in " ".join(json.loads(r)["gates"]["blocked"]) for r in absent[0])  # watch only, as today
    assert absent == disabled == future
    assert all("verdict_niveau" not in json.loads(r)["details"] for r in absent[0])


@pytest.fixture
def on(raw):
    return _cfg(raw, verdict={"enabled": True, "effective_from": "2026-10-01"})  # the fixtures are dated 21/10


def _d7(s) -> Proposal:
    (p,) = [
        x
        for x in s.scalars(select(Proposal).where(Proposal.isin == ISIN_D7, Proposal.deleted_at.is_(None)))
        if s.get(Signal, x.signal_id).detector == "d7_trend_quality"
    ]
    return p


def test_a_d7_setup_becomes_a_speculative_buy_at_half_size_entered_by_the_plan_4_plage(db_engine, on):
    from app.web.opportunities_routes import order_book

    with db_session() as s:
        _seed_value(s, ISIN_D7, "LVMH", d7_bars())
        engine.scan(s, on, DAY, now=NOW)
        p = _d7(s)
        d, gates = p.details, p.gates
        sig = s.get(Signal, p.signal_id)
        bars = d7_bars()
        f = compute_daily_features(bars)
        # D10: ADR-013 lifted, a BUY labelled speculative, the measured history said (no backtest here: not measured)
        assert p.action == "BUY" and d["verdict_niveau"] == "speculative", (gates, d.get("verdict_gradue"))
        assert not any("(ADR-013)" in b for b in gates["blocked"]) and gates["blocked"] == []
        sp = d["speculative"]
        assert sp["etiquette"].startswith("Spéculative — espérance historique non mesurée")
        assert "plans n° 4 et 6" in sp["etiquette"] and sp["taille"] == "½"
        # D15: the plan n° 4 P2 order — seuil = max(trigger, high) + 1 tick, limit + 0.5 ATR, stop 2.5 ATR, time exit
        trig = bars[-1].high * 1.002
        seuil = trig + tick_size(trig)
        ent = d["entree"]
        assert ent["type"] == "ordre à plage de déclenchement" and ent["politique"] == "P2 (plan n° 4)"
        assert ent["seuil"] == pytest.approx(seuil)
        assert ent["limite"] == pytest.approx(seuil * (1 + 0.5 * f.atr14 / bars[-1].close)) == d["p_max"]
        assert ent["validite_seances"] == 3 and ent["sortie_au_temps_seances"] == 20 and ent["jusqu_au"]
        assert p.stop == pytest.approx(seuil - 2.5 * f.atr14) == sig.stop_initial
        assert ent["annulation_ouverture_au_stop_structurel"] < bars[-1].close  # the R1 stop cancels the order
        assert p.horizon == "swing_2_6w" and sig.evidence["entree_p2"]["sortie_au_temps_seances"] == 20
        assert any("R4 non appliqué" in x for x in gates["passed"])
        # rule 7: sized on the limit and the slipped stop, at half the budget
        row = d["comptes"][p.account_recommended]
        slip = on.params.risk.slippage_pct
        assert row["risque_eur"] == pytest.approx(row["titres"] * (ent["limite"] - p.stop * (1 - slip)), abs=0.02)
        rt = d["reductions_taille"]
        assert rt["spéculative"] == 0.5
        budget = d["niveau_risque"]["r_reference_eur"] * rt["total"]
        assert 0 < row["risque_eur"] <= budget
        # it goes to the order book as a plage, distance measured to its seuil
        rows = order_book([{"p": p, "name": "LVMH", "snapshot_price": {"last": bars[-1].close}}])
        assert rows and rows[0]["seuil"] == pytest.approx(seuil) and not rows[0]["blocked"]


def test_half_size_is_half_the_budget_of_the_same_card(raw):
    half = _run(_cfg(raw, verdict=ON), seed=lambda s: _seed_value(s, ISIN_D7, "LVMH", d7_bars()))
    full = _run(_cfg(raw, verdict={**ON, "speculative_size_multiplier": 1.0}),
                seed=lambda s: _seed_value(s, ISIN_D7, "LVMH", d7_bars()))  # fmt: skip
    (h,) = [json.loads(r) for r in half[0]]
    (f,) = [json.loads(r) for r in full[0]]
    assert h["details"]["reductions_taille"]["total"] == pytest.approx(f["details"]["reductions_taille"]["total"] / 2)
    assert 0 < h["shares"] < f["shares"] and h["risk_eur"] <= f["risk_eur"] / 2 + 1e-6


def test_caps_still_block_a_speculative_card(db_engine, on, monkeypatch):
    monkeypatch.setattr(engine, "_reserved_risk", lambda *a, **k: (1e6, [{"isin": "X", "risque_eur": 1e6}]))
    with db_session() as s:
        _seed_value(s, ISIN_D7, "LVMH", d7_bars())
        engine.scan(s, on, DAY, now=NOW)
        p = _d7(s)
    assert p.action == "WATCH" and p.details["verdict_niveau"] == "ecartee"
    assert any(b.startswith("BLOQUÉ : risque ouvert après entrée") for b in p.gates["blocked"])
    assert "plafond" in p.details["verdict_gradue"]["codes"] and "derogation" not in p.details


def test_the_macro_freeze_r14_still_blocks_a_speculative_card(db_engine, on):
    """User decision C of 29/10/2026: an announcement a stop does not cover still freezes the entry (« écartée »)."""
    from app.db.models import MacroEvent

    with db_session() as s:
        _seed_value(s, ISIN_D7, "LVMH", d7_bars())
        s.add(MacroEvent(ts=NOW + timedelta(hours=6), currency="USD", title="Federal Funds Rate", impact="High",
                         source="test", received_at=NOW))  # fmt: skip
        s.flush()
        engine.scan(s, on, DAY, now=NOW)
        p = _d7(s)
    assert p.action == "WATCH" and p.details["verdict_niveau"] == "ecartee"
    assert any("(R14)" in b for b in p.gates["blocked"]) and "r14" in p.details["verdict_gradue"]["codes"]


def test_stale_data_in_session_stays_to_verify_and_is_never_sent(db_engine, on):
    class Alerts:
        def __init__(self):
            self.events = []

        def emit(self, s, ev, mode, intraday=True):
            self.events.append(ev)
            return True

    on.params.verdict["notify_speculative"] = True
    in_session = NOW.replace(hour=8, minute=30)  # 10:30 Paris, Thursday 22/10/2026
    with db_session() as s:
        _seed_value(s, ISIN_D7, "LVMH", d7_bars())
        engine.scan(s, on, in_session.date(), isins=[ISIN_D7], now=in_session)
        p = _d7(s)
        assert p.to_verify and "séance en cours" in p.details["source_cours"]
        g = p.details["verdict_gradue"]
        assert g["niveau"] == "a_surveiller" and g["niveau_hors_verification"] == "speculative"
        assert g["raisons"][0].startswith("à vérifier")
        alerts = Alerts()
        assert engine.notify_top(s, on, alerts, "disponible", datetime.now(UTC).date()) == 0


def test_telegram_sends_a_speculative_card_only_when_asked(db_engine, on):
    class Alerts:
        def __init__(self):
            self.events = []

        def emit(self, s, ev, mode, intraday=True):
            self.events.append(ev)
            return True

    with db_session() as s:
        _seed_value(s, ISIN_D7, "LVMH", d7_bars())
        engine.scan(s, on, DAY, now=NOW)
        p = _d7(s)
        assert p.action == "BUY" and p.rank == 1
        alerts = Alerts()
        today = datetime.now(UTC).date()
        assert engine.notify_top(s, on, alerts, "disponible", today) == 0  # notify_speculative: false
        on.params.verdict["notify_speculative"] = True
        assert engine.notify_top(s, on, alerts, "disponible", today) == 1
        ev = alerts.events[0]
        assert len(ev.lines) <= 11 and any(x.startswith("Spéculative — ") and "taille ½" in x for x in ev.lines)
        assert any("sortie au temps, objectifs = repères" in x for x in ev.lines)


def test_a_letter_breakout_not_crossed_still_waits(db_engine, on):
    with db_session() as s:
        _seed_value(s, ISIN, "TotalEnergies", breakout_bars()[:-1])  # the range under 100, not crossed
        it = NewsletterItem(source="momentum_capital", message_id="m1", dedup_hash="h1", subject="Momentum",
                            received_at=NOW - timedelta(hours=20), body_text="(privé)")  # fmt: skip
        s.add(it)
        s.flush()
        s.add(
            NewsletterValue(
                item_id=it.id,
                isin=ISIN,
                name="TotalEnergies",
                direction="buy",
                levels={"declencheur": 100.2, "objectif": 102.5, "objectif_2": 125.0, "stop": 96.0},
            )
        )
        s.flush()
        engine.scan(s, on, DAY, isins=[ISIN], now=NOW)
        (p,) = [x for x in s.scalars(select(Proposal)) if s.get(Signal, x.signal_id).source == "moteur_lettre"]
    assert p.action == "WATCH" and p.details["verdict_niveau"] == "a_surveiller"
    assert "speculative" not in p.details  # its D12 try still waits for the crossing: the original card is kept
    blocked = " ".join(p.gates["blocked"])
    assert "au déclencheur" in blocked or "déclencheur non franchi" in blocked


def test_d12_a_crossed_letter_breakout_failing_r4_on_target_1_uses_its_last_target(db_engine, on):
    with db_session() as s:
        _seed_value(s, ISIN, "TotalEnergies", breakout_bars())  # crossed: close 100.8 over the trigger 100.2
        it = NewsletterItem(source="momentum_capital", message_id="m1", dedup_hash="h1", subject="Momentum",
                            received_at=NOW - timedelta(hours=20), body_text="(privé)")  # fmt: skip
        s.add(it)
        s.flush()
        s.add(
            NewsletterValue(
                item_id=it.id,
                isin=ISIN,
                name="TotalEnergies",
                direction="buy",
                levels={"declencheur": 100.2, "objectif": 102.5, "objectif_2": 125.0, "stop": 96.0},
            )
        )
        s.flush()
        off_cfg = copy.deepcopy(on)
        off_cfg.params.verdict["enabled"] = False
        engine.scan(s, off_cfg, DAY, isins=[ISIN], now=NOW)
        (before,) = [x for x in s.scalars(select(Proposal)) if s.get(Signal, x.signal_id).source == "moteur_lettre"]
        assert before.action == "WATCH" and before.details["admission"] == "refuse"  # R4 on 102.5: no order today
        before.deleted_at = NOW  # withdrawn (as a rescan does) to let the scale grade the same day again
        s.flush()
        engine.scan(s, on, DAY, isins=[ISIN], now=NOW)
        (p,) = [
            x
            for x in s.scalars(select(Proposal).where(Proposal.deleted_at.is_(None)))
            if s.get(Signal, x.signal_id).source == "moteur_lettre"
        ]
    d = p.details
    assert d["verdict_niveau"] == "speculative" and p.action == "BUY", (p.gates, d.get("verdict_gradue"))
    assert d["objectif_r4"] == 125.0 and d["speculative"]["objectif_final"] == {
        "objectif_1": 102.5,
        "objectif_final": 125.0,
    }
    assert any("(D12)" in x for x in p.gates["passed"]) and p.gates["blocked"] == []
    assert d["reductions_taille"]["spéculative"] == 0.5 and p.shares


def _write_params(tmp_path, cfg) -> str:
    path = tmp_path / "params.yaml"
    path.write_text(yaml.safe_dump(cfg.raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return str(path)


def test_verdict_preview_works_with_the_scale_off(db_engine, raw, tmp_path, capsys):
    from app.cli import cmd_verdict_preview
    from app.services import verdict as verdict_svc

    off = _cfg(raw)  # the example: verdict.enabled false, ADR-013 on d2 and d7
    with db_session() as s:
        _seed_both(s)
        engine.scan(s, off, DAY, now=NOW)
        props = list(s.scalars(select(Proposal)))
        assert props and all(p.action != "BUY" for p in props)  # today: nothing to buy
        created = props[0].created_at.replace(tzinfo=UTC).astimezone(verdict_svc.PARIS).date()
        stored = {p.id: (p.action, dict(p.gates), dict(p.details)) for p in props}
    assert cmd_verdict_preview(argparse.Namespace(params=_write_params(tmp_path, off), date=created.isoformat(),
                                                  json=False)) == 0  # fmt: skip
    out = capsys.readouterr().out
    assert "lecture seule" in out and "échelle désactivée" in out
    d7_line = next(ln for ln in out.splitlines() if "LVMH · d7_trend_quality" in ln)
    assert "WATCH → Spéculative (taille ½)" in d7_line, out
    assert "ADR-013 levé (D10)" in out and "entrée à plage P2 (D15), R4 non appliqué (estimée : seuil" in out
    with db_session() as s:  # read only: nothing changed
        assert {p.id: (p.action, dict(p.gates), dict(p.details)) for p in s.scalars(select(Proposal))} == stored
        rep = verdict_svc.preview(s, off, created)
    assert sum(rep["comptes"].values()) == len(stored) and rep["comptes"]["speculative"] >= 1


def test_the_stored_level_is_the_preview_level(db_engine, on):
    from app.services import verdict as verdict_svc

    with db_session() as s:
        _seed_both(s)
        engine.scan(s, on, DAY, now=NOW)
        props = list(s.scalars(select(Proposal)))
        created = props[0].created_at.replace(tzinfo=UTC).astimezone(verdict_svc.PARIS).date()
        rep = verdict_svc.preview(s, on, created)
    levels = {r["id"]: r["niveau"] for r in rep["lignes"]}
    assert levels == {p.id: p.details["verdict_niveau"] for p in props}
    assert "activée" in rep["parametres"]


def test_the_opportunities_page_groups_by_level(db_engine, on, calendars, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with db_session() as s:
        _seed_both(s)
        engine.scan(s, on, DAY, now=NOW)
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_WRITES", "1")
    app = create_app(on, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        html = c.get(f"/opportunites?day={datetime.now(UTC).date().isoformat()}").text
    assert "Spéculative — espérance historique non mesurée" in html and "taille ½" in html
    assert "Entrée à plage du plan n° 4 (P2)" in html
    assert ">Spéculative<" in html  # the group header and the badge (docs/37 §37.2, amber)
    assert "Actionnables aujourd'hui" not in html  # the level groups replace the former ones
