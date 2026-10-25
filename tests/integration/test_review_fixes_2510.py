"""Regression tests for the adversarial review of 25/10/2026 (circuit breaker latch, stale baseline,
R21 limited to the caps, unavailable breaker, radar news window anchored on the session, missing volume)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.db.models import FeatureDaily, Instrument, KpiDaily, NewsItem, PriceEod, Proposal, Trade, WeakSignalHit
from app.db.session import db_session
from app.domain.weak_signals import relative_volume
from app.services import circuit as circuit_mod
from app.services import engine
from app.services.circuit import CircuitView, circuit_state
from app.services.weak_signals import compute_hits
from tests.integration.test_engine import DAY, ISIN, NOW, _seed, breakout_bars

WED = datetime(2026, 10, 21, 16, 0, tzinfo=UTC)
THU = datetime(2026, 10, 22, 16, 0, tzinfo=UTC)


def _kpi(s, day, realized, latent, incomplete=False):
    s.add(KpiDaily(day=day, account_id=None, equity=40000, realized_pnl_cum=realized, latent_pnl=latent,
                   incomplete=incomplete, computed_at=WED))  # fmt: skip


def test_a_tripped_week_stays_tripped_when_the_latent_recovers(db_engine, config):
    with db_session() as s:
        _kpi(s, date(2026, 9, 30), 0, 0)
        _kpi(s, date(2026, 10, 16), 0, 0)
        _kpi(s, date(2026, 10, 20), 0, -1300)  # Tuesday evening: −4.33 R
        s.flush()
        v = circuit_state(s, config, THU, current=(0.0, -800.0))  # Thursday: back to −2.67 R
        assert v.tripped and "semaine" in v.reason


def test_a_stale_or_incomplete_baseline_is_unavailable(db_engine, config):
    with db_session() as s:
        s.add(Trade(account_id=1, isin=ISIN, side="buy", qty=1, price=10, ts=datetime(2026, 6, 1, tzinfo=UTC),
                    kind="confirmed", source="saxo"))  # fmt: skip
        _kpi(s, date(2026, 7, 1), 0, 2000)  # months old: never a week's baseline
        s.flush()
        v = circuit_state(s, config, WED, current=(0.0, 1000.0))
        assert not v.tripped and v.week_r is None and v.size_multiplier == 0.75  # reduced until measured


def test_override_is_never_offered_against_the_circuit_breaker(db_engine, config, monkeypatch):
    view = CircuitView(tripped=True, reason="coupe-circuit : test", size_multiplier=0.0, r_unit_eur=300)
    monkeypatch.setattr(circuit_mod, "circuit_state", lambda *a, **k: view)
    with db_session() as s:
        _seed(s, breakout_bars())
        engine.scan(s, config, DAY, now=NOW)
        for p in s.query(Proposal).filter(Proposal.isin == ISIN):
            assert not ((p.details or {}).get("derogation") or {}).get("proposable")


def test_an_unavailable_breaker_turns_entries_into_watch(db_engine, config, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(circuit_mod, "circuit_state", boom)
    with db_session() as s:
        _seed(s, breakout_bars())
        engine.scan(s, config, DAY, now=NOW)
        props = s.query(Proposal).filter(Proposal.isin == ISIN).all()
        assert props and all(p.action != "BUY" for p in props)
        assert all(any("coupe-circuit indisponible" in b for b in p.gates["blocked"]) for p in props)


def test_a_friday_release_still_counts_on_monday_and_labels_are_dated_on_their_session(db_engine, config):
    fri, mon = date(2026, 10, 23), datetime(2026, 10, 26, 5, 40, tzinfo=UTC)
    with db_session() as s:
        s.add(Instrument(isin="FR0000000003", name="Petite", mic="XPAR", currency="EUR", in_universe=True,
                         small_cap=True, active=True))  # fmt: skip
        for k in range(61):
            d = fri - timedelta(days=60 - k)
            s.add(PriceEod(isin="FR0000000003", day=d, open=10, high=10.1, low=9.9, close=10.0,
                           volume=400_000 if k == 60 else 100_000, source="eodhd", received_at=mon,
                           data_status="eod"))  # fmt: skip
        s.add(FeatureDaily(isin="FR0000000003", day=fri, atr14=0.3, sessions=61))
        s.add(NewsItem(source="actusnews", guid="g", dedup_hash="h", title="Résultats", isins=["FR0000000003"],
                       fetched_at=datetime(2026, 10, 23, 16, 0, tzinfo=UTC)))  # fmt: skip
        s.flush()
        hits = compute_hits(s, config, date(2026, 10, 25), now=mon)  # Sunday = run_date − 1 on Monday
        assert not any(h.label == "volume_sans_mouvement" for h in hits)  # the Friday news corroborates
        assert all(h.day == fri for h in s.query(WeakSignalHit))


def test_a_missing_last_volume_is_not_a_zero():
    assert relative_volume([100.0] * 60 + [None]) is None


def test_a_correlated_open_line_caps_the_cluster_risk(db_engine, config, monkeypatch):
    from app.db.models import PositionRow

    monkeypatch.setattr(circuit_mod, "circuit_state", lambda *a, **k: CircuitView(r_unit_eur=300))
    with db_session() as s:
        _seed(s, breakout_bars())
        twin = "FR0000000007"  # the same price path as the candidate: ρ = 1
        for r in s.query(PriceEod).filter(PriceEod.isin == ISIN).all():
            s.add(PriceEod(isin=twin, day=r.day, open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume,
                           source="eodhd", received_at=NOW, data_status="eod"))  # fmt: skip
        s.add(PositionRow(account_id=1, isin=twin, qty_held=100, avg_price=95.0, last_price=100.0, stop_current=93.0,
                          execution_state="filled", protection_state="protected"))  # fmt: skip
        s.flush()
        engine.scan(s, config, DAY, now=NOW)
        props = s.query(Proposal).filter(Proposal.isin == ISIN).all()
        sized = [p for p in props if (p.details or {}).get("grappe_correlee")]
        assert sized and sized[0].details["grappe_correlee"]["lignes"][0]["correlation"] == 1.0
        assert all(
            any("grappe corrélée" in b for b in p.gates["blocked"]) for p in sized
        )  # 700 € at risk + the candidate > 2 R


def test_correlation_needs_enough_shared_sessions():
    from app.domain.risk import aligned_correlation

    a = {date(2026, 1, 1) + timedelta(days=k): 10 + (k % 3) for k in range(30)}
    assert aligned_correlation(a, a) is None
    b = {date(2026, 1, 1) + timedelta(days=k): 10 + (k % 3) for k in range(80)}
    assert abs(aligned_correlation(b, b) - 1.0) < 1e-9
