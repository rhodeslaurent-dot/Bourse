"""docs/34 — the study of the missed rises runs end to end on a small synthetic base: episodes found, the tool
replayed with the production policy, the funnel adds up, the control gives a base rate, the report is French and
aggregated, the CLI refuses the European history kept for confirmation."""

from __future__ import annotations

import math
import random
from datetime import UTC, date, datetime, timedelta

from app.db.models import Instrument, PriceEod
from app.db.session import db_session
from app.services import moves

T0 = date(2023, 1, 2)
NOW = datetime(2026, 10, 28, tzinfo=UTC)


def _days(n: int) -> list[date]:
    out, d = [], T0
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _series(rng: random.Random, n: int) -> list[tuple[float, float, float, float, int]]:
    """Random walk with quiet bases followed by rises (the shape the detectors look for), and pullbacks."""
    px, out = 50.0, []
    k = 0
    while len(out) < n:
        phase = k % 3
        steps = rng.randint(15, 30)
        for _ in range(steps):
            drift = {0: 0.0, 1: 0.012, 2: -0.006}[phase]
            r = drift + rng.gauss(0, 0.006 if phase == 0 else 0.012)
            o = px * (1 + rng.gauss(0, 0.003))
            px = max(1.0, px * math.exp(r))
            hi, lo = max(o, px) * (1 + abs(rng.gauss(0, 0.004))), min(o, px) * (1 - abs(rng.gauss(0, 0.004)))
            vol = int(200_000 * (2.5 if phase == 1 and rng.random() < 0.3 else 1.0) * (0.7 + rng.random()))
            out.append((o, hi, lo, px, vol))
        k += 1
    return out[:n]


def _seed(n_values: int = 12, n_days: int = 700) -> list[date]:
    rng = random.Random(3)
    days = _days(n_days)
    with db_session() as s:
        for v in range(n_values):
            isin = f"FR00000000{v:02d}"
            s.add(Instrument(isin=isin, name=f"Valeur {v}", mic="XPAR", currency="EUR", in_universe=True,
                             market_cap_eur=2e9, adv_eur_20=2e7, pea_eligible=True, srd_status="complet"))  # fmt: skip
            for d, (o, h, lo, c, vol) in zip(days, _series(rng, n_days), strict=True):
                s.add(PriceEod(isin=isin, day=d, open=o, high=h, low=lo, close=c, adj_close=c, volume=vol,
                               source="eodhd", received_at=NOW, data_status="eod"))  # fmt: skip
    return days


def test_the_study_replays_the_tool_on_each_rise(db_engine, example_raw, calendars):
    from app.config import validate_params

    config = validate_params(example_raw)  # the production policy: d3/d4 disabled, d2/d7 watch-only
    days = _seed()
    with db_session() as s:
        rep = moves.run_study(s, config, calendars, days[300], days[-1], max_episodes=150, control_values=3, seed=1)
    m = rep["meta"]
    assert m["hausses_reperees"] > 0 and m["hausses_rejouees"] == min(150, m["hausses_reperees"])
    assert m["groupes"] == ["P0"] and m["politique"]["d3_range_expansion"].startswith("désactivé")
    assert m["politique"]["d2_breakout"].startswith("surveillance")
    for key in ("entonnoir_production", "entonnoir_tous_detecteurs"):
        assert abs(sum(v["k"] for v in rep[key].values()) - m["hausses_rejouees"]) == 0
    # the production policy can only lose stages compared with every detector active
    assert rep["entonnoir_production"]["ordre_execute"]["k"] <= rep["entonnoir_tous_detecteurs"]["ordre_execute"]["k"]
    c = rep["controle"]
    assert c["jours"] > 0 and c["taux_de_base"]["n"] > 0
    text = moves.render_fr(rep)
    for title in ("1. PROFIL DES HAUSSES", "2. ENTONNOIR", "3. ENTONNOIR", "6. CONTRÔLE", "7. LIFT"):
        assert title in text
    assert "New York, Londres, Zurich exclues" in text


def test_the_cli_keeps_the_old_european_history_for_confirmation(capsys):
    import argparse

    from app.cli import cmd_moves_study

    args = argparse.Namespace(params="config/params.example.yaml", calendars="config/market_calendars.yaml",
                              from_date="2010-01-04", to=None)  # fmt: skip
    assert cmd_moves_study(args) == 2
    assert "réservées à la confirmation" in capsys.readouterr().out
