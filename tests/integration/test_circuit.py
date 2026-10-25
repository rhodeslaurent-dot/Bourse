"""docs/07 §7.3 — the circuit breaker reads the equity snapshots before the period and blocks the new
entries when the week's or the month's losses pass the limit; size reductions are applied (R10)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.db.models import KpiDaily, Proposal, Trade
from app.db.session import db_session
from app.services import circuit as circuit_mod
from app.services import engine
from app.services.circuit import circuit_state
from tests.integration.test_engine import DAY, ISIN, NOW, _seed, breakout_bars

NOW_WED = datetime(2026, 10, 21, 16, 0, tzinfo=UTC)  # Wednesday 21/10/2026, week starts Monday 19/10


def _kpi(s, day: date, realized: float, latent: float) -> None:
    s.add(KpiDaily(day=day, account_id=None, equity=40000, realized_pnl_cum=realized, latent_pnl=latent,
                   computed_at=NOW_WED))  # fmt: skip


def test_no_history_means_a_zero_baseline(db_engine, config):
    with db_session() as s:
        v = circuit_state(s, config, NOW_WED, current=(0.0, -150.0))
        assert v.week_r == -0.5 and not v.tripped and v.r_unit_eur == 300.0


def test_missing_baseline_with_past_trades_is_unavailable_not_estimated(db_engine, config):
    with db_session() as s:
        s.add(Trade(account_id=1, isin=ISIN, side="buy", qty=1, price=10, ts=datetime(2026, 9, 1, tzinfo=UTC),
                    kind="confirmed", source="saxo"))  # fmt: skip
        s.flush()
        v = circuit_state(s, config, NOW_WED, current=(0.0, -5000.0))
        assert v.week_r is None and v.month_r is None and not v.tripped
        assert any("indisponible" in n for n in v.notes)


def test_weekly_loss_trips_and_blocks_new_entries(db_engine, config, monkeypatch):
    with db_session() as s:
        _kpi(s, date(2026, 9, 30), 0, 0)  # before the month
        _kpi(s, date(2026, 10, 16), 100, 200)  # Friday before the week
        s.flush()
        v = circuit_state(s, config, NOW_WED, current=(-400.0, -300.0))  # −1 000 € = −3.33 R this week
        assert v.tripped and "semaine" in v.reason and round(v.week_r, 2) == -3.33
        monkeypatch.setattr(circuit_mod, "circuit_state", lambda *a, **k: v)
        _seed(s, breakout_bars())
        engine.scan(s, config, DAY, now=NOW)
        sized = [p for p in s.query(Proposal).filter(Proposal.isin == ISIN) if (p.details or {}).get("coupe_circuit")]
        assert sized and all(
            any(b.startswith("BLOQUÉ : coupe-circuit") for b in (p.gates or {}).get("blocked", [])) for p in sized
        )


def test_previous_week_trip_halves_the_sizes(db_engine, config):
    with db_session() as s:
        _kpi(s, date(2026, 9, 30), 0, 0)
        _kpi(s, date(2026, 10, 9), 0, 0)  # before the previous week
        _kpi(s, date(2026, 10, 16), -500, -500)  # end of the previous week: −3.33 R
        s.flush()
        v = circuit_state(s, config, NOW_WED, current=(-500.0, -500.0))
        assert not v.tripped and v.size_multiplier == 0.5 and "reprise" in v.reason


def test_restricted_gate_halves_the_risk_budget(db_engine, config):
    with db_session() as s:
        _seed(s, breakout_bars())
        engine.scan(s, config, DAY, now=NOW)
        p = next(x for x in s.query(Proposal).filter(Proposal.isin == ISIN) if (x.details or {}).get("comptes"))
        assert p.details["contexte_marche"]["portail"] in ("restreint", "restreint_plus")
        assert p.details["reductions_taille"]["portail restreint (R10)"] == 0.5
        pea = p.details["comptes"]["pea"]
        if pea.get("risque_eur") is not None:
            assert pea["risque_eur"] <= 300 * 0.5 + 1e-6  # budget 0.75 % × 40 000 € × 0.5
