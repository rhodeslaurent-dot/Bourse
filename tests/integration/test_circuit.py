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
        v = circuit_state(s, config, NOW_WED, current=(-700.0, -300.0))  # −1 300 € = −4.33 R this week
        assert v.tripped and "semaine" in v.reason and round(v.week_r, 2) == -4.33
        monkeypatch.setattr(circuit_mod, "circuit_state", lambda *a, **k: v)
        _seed(s, breakout_bars())
        engine.scan(s, config, DAY, now=NOW)
        sized = [p for p in s.query(Proposal).filter(Proposal.isin == ISIN) if (p.details or {}).get("coupe_circuit")]
        assert sized and all(
            any(b.startswith("BLOQUÉ : coupe-circuit") for b in (p.gates or {}).get("blocked", [])) for p in sized
        )


def test_previous_week_trip_reduces_the_sizes(db_engine, config):
    with db_session() as s:
        _kpi(s, date(2026, 9, 30), 0, 0)
        _kpi(s, date(2026, 10, 9), 0, 0)  # before the previous week
        _kpi(s, date(2026, 10, 16), -700, -600)  # end of the previous week: −4.33 R
        s.flush()
        v = circuit_state(s, config, NOW_WED, current=(-700.0, -600.0))
        assert not v.tripped and v.size_multiplier == 0.75 and "reprise" in v.reason


def test_the_day_level_sizes_the_lines_and_the_unknown_gate_no_longer_halves(db_engine, config):
    from app.db.models import RiskDialDaily

    with db_session() as s:
        _seed(s, breakout_bars())
        engine.scan(s, config, DAY, now=NOW)
        p = next(x for x in s.query(Proposal).filter(Proposal.isin == ISIN) if (x.details or {}).get("comptes"))
        assert p.details["contexte_marche"]["portail"] in ("restreint", "restreint_plus")
        lv = p.details["niveau_risque"]
        row = s.query(RiskDialDaily).one()
        assert lv["niveau_applique"] == row.recommended and lv["r_reference_eur"] == 300.0
        assert "portail restreint (R10)" not in (p.details.get("reductions_taille") or {})
        assert row.explanation and row.explanation[0].startswith("Niveau de risque conseillé")


def test_line_reductions_are_floored_and_never_turn_a_buy_into_watch(db_engine, config, monkeypatch):
    from app.services.circuit import CircuitView

    view = CircuitView(size_multiplier=0.5, reason="recul depuis le plus haut", r_unit_eur=300.0)
    monkeypatch.setattr(circuit_mod, "circuit_state", lambda *a, **k: view)
    with db_session() as s:
        _seed(s, breakout_bars())
        engine.scan(s, config, DAY, now=NOW)
        for p in s.query(Proposal).filter(Proposal.isin == ISIN):
            assert not any("plancher" in b for b in (p.gates or {}).get("blocked", []))
            rt = (p.details or {}).get("reductions_taille") or {}
            if rt:
                assert rt["total"] >= 0.5 * 0.999  # level floor (× 0.5) — dilution, not immobility
