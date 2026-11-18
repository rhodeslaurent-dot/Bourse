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


def test_a_missing_measure_blocks_every_new_entry(db_engine, config, monkeypatch):
    """Decision of 04/11/2026 (analysis of the cards: « coupe-circuit indisponible ») — without a complete equity
    snapshot before the week or the month, the guard is not measured: BLOQUÉ (an absolute rule of the graduated
    verdict), never a reduced size; ``missing_baseline_blocks: false`` restores the reduction."""
    import copy

    with db_session() as s:
        s.add(Trade(account_id=1, isin=ISIN, side="buy", qty=1, price=10, ts=datetime(2026, 9, 1, tzinfo=UTC),
                    kind="confirmed", source="saxo"))  # fmt: skip
        s.flush()
        v = circuit_state(s, config, NOW_WED, current=(0.0, 0.0))
        assert v.unavailable and v.blocks_when_unavailable and v.size_multiplier == 1.0
        monkeypatch.setattr(circuit_mod, "circuit_state", lambda *a, **k: v)
        _seed(s, breakout_bars())
        engine.scan(s, config, DAY, now=NOW)
        sized = [p for p in s.query(Proposal).filter(Proposal.isin == ISIN) if (p.details or {}).get("coupe_circuit")]
        why = "BLOQUÉ : coupe-circuit — mesure indisponible"
        assert sized and all(any(b.startswith(why) for b in (p.gates or {}).get("blocked", [])) for p in sized)
        assert all(p.action != "BUY" for p in sized)
        c = copy.deepcopy(config)
        c.params.risk.circuit_breaker.model_extra["missing_baseline_blocks"] = False
        old = circuit_state(s, c, NOW_WED, current=(0.0, 0.0))
        assert old.unavailable and old.size_multiplier == 0.75 and "indisponible" in old.reason


def _pea_rows(s, days, *, pnl_flag):  # type: ignore[no-untyped-def]
    from app.db.models import Account

    pea = Account(broker="boursobank", type="pea", label="PEA")  # cash never declared
    s.add(pea)
    s.add(Trade(account_id=1, isin=ISIN, side="buy", qty=1, price=10, ts=datetime(2026, 9, 1, tzinfo=UTC),
                kind="confirmed", source="saxo"))  # fmt: skip
    s.flush()
    for d in days:
        s.add(KpiDaily(day=d, account_id=None, equity=None, realized_pnl_cum=0, latent_pnl=0, incomplete=True,
                       pnl_incomplete=pnl_flag, computed_at=NOW_WED))  # fmt: skip
        s.add(KpiDaily(day=d, account_id=pea.id, cash=None, incomplete=True, pnl_incomplete=pnl_flag,
                       computed_at=NOW_WED))  # fmt: skip
    s.flush()


def test_the_trace_says_which_baseline_is_missing_and_why(db_engine, config):
    """18/11/2026: every proposal said « mesure indisponible » — the trace names the snapshot and its cause."""
    with db_session() as s:
        _pea_rows(s, (date(2026, 9, 30), date(2026, 10, 16)), pnl_flag=None)  # written before migration 0039
        s.add(KpiDaily(day=date(2026, 10, 20), account_id=None, equity=1, realized_pnl_cum=0, latent_pnl=-600,
                       computed_at=NOW_WED))  # fmt: skip
        s.flush()
        out = circuit_mod.trace(s, config, NOW_WED)
    text = "\n".join(out)
    assert out[0].startswith("coupe-circuit au 21/10 : BLOQUANT — mesure indisponible")
    assert ("référence semaine (avant le 19/10) : 16/10 : résultat INCOMPLET (instantané d'avant le 18/11/2026 : lu "
            "comme l'équité), équité partielle (pea : liquidités inconnues)") in text  # fmt: skip
    assert "référence mois (avant le 01/10) : 30/09 : résultat INCOMPLET" in text
    assert "premier complet : 20/10" in text and "liquidités jamais renseignées : pea" in text
    assert "en R depuis le 30/09 : 30/09 +0.0 · 16/10 +0.0 · 20/10 -2.0" in text  # 600 € = 2 R of 300 €
    assert "40000" not in text and "€" not in text  # no amount printed


def test_unknown_cash_no_longer_hides_the_measure(db_engine, config):
    """A declarative PEA whose cash was never declared leaves the gains and losses whole: the measure is there."""
    with db_session() as s:
        _pea_rows(s, (date(2026, 9, 30), date(2026, 10, 16)), pnl_flag=False)
        v = circuit_state(s, config, NOW_WED, current=(0.0, -150.0))
    assert not v.unavailable and v.week_r == -0.5 and v.month_r == -0.5


def test_a_rebase_reads_only_the_snapshots_from_its_day(db_engine, config, raw):
    """A line valued with another company's prices until 20/10 (a wrong tie): the user's rebase from 20/10."""
    from app.config import validate_params

    with db_session() as s:
        _pea_rows(s, (date(2026, 9, 30), date(2026, 10, 16)), pnl_flag=None)
        s.add(KpiDaily(day=date(2026, 10, 20), account_id=None, equity=1, realized_pnl_cum=0, latent_pnl=-4800,
                       pnl_incomplete=False, computed_at=NOW_WED))  # fmt: skip
        s.flush()
        assert circuit_state(s, config, NOW_WED, current=(0.0, -4800.0)).unavailable  # no decision: still blocked
        raw["risk"]["circuit_breaker"]["baseline_from"] = "2026-10-20"
        v = circuit_state(s, validate_params(raw), NOW_WED, current=(0.0, -4800.0))
    assert not v.unavailable and v.week_r == 0.0 and v.month_r == 0.0 and not v.tripped
    assert any("mesure rebasée au 20/10" in n for n in v.notes)
