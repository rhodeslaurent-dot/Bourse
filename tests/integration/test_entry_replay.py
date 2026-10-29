"""docs/34 §34.10 — the entry policy replay end to end on a small synthetic base: every setup gets every policy, the
placebo arm mirrors the real one, the report is French and aggregated (no ISIN, no price), the detection never looks
ahead, the speed filter never hides a setup, the stored proposals are replayed, the CLI keeps the sealed history."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from datetime import UTC, datetime, timedelta

from app.db.models import DecisionSnapshot, Instrument, PriceEod, Proposal, Signal
from app.db.session import db_session
from app.domain import entry_policies as ep
from app.domain.detectors import DETECTORS, DetectorContext
from app.domain.indicators import Bar
from app.services import entry_replay as er
from tests.integration.test_moves_study import NOW, _days, _seed, _series


def test_every_setup_gets_every_policy_and_a_placebo(db_engine, config, calendars, tmp_path):
    days = _seed()
    with db_session() as s:
        rep = er.run_setups(s, config, calendars, days[300], days[-1], seed=1)
    m = rep["meta"]
    n_setups = sum(m["configurations"].values())
    assert m["groupes"] == ["P0"] and n_setups > 0 and m["placebos"] == m["configurations"]
    assert set(m["detecteurs"]) == {"d2_breakout", "d3_range_expansion", "d4_pullback", "d7_trend_quality"}
    for pol in ep.POLICIES:
        tous = rep["synthese"]["reel"][pol]["tous"]
        assert tous["setups"] == n_setups  # the same setups for every policy
        assert tous["ordres"] + sum(tous["sans_ordre"].values()) == n_setups
        assert tous["executes"] + sum(tous["non_executes"].values()) == tous["ordres"]
        assert rep["synthese"]["placebo"][pol]["tous"]["setups"] == n_setups
        assert sum(c["setups"] for c in rep["cellules"] if c["politique"] == pol) == n_setups
        assert rep["rythme"][pol]["parametres"]["ordres_places"] <= rep["rythme"][pol]["parametres"]["ordres_possibles"]
    assert "delta_vs_p0" not in rep["synthese"]["reel"]["P0"]["tous"]
    assert "delta_vs_p0" in rep["synthese"]["reel"]["P1"]["tous"]
    j, md = er.write(rep, tmp_path)
    text = md.read_text(encoding="utf-8")
    for title in ("# Rejeu des politiques d'entrée", "## 1. Synthèse", "Sélection adverse", "livre d'ordres",
                  "## 2. Par détecteur", "## 6. Comment lire", "période scellée", "P2'", "P5'"):  # fmt: skip
        assert title in text
    assert "FR00000000" not in text and "FR00000000" not in j.read_text(encoding="utf-8")
    json.loads(j.read_text(encoding="utf-8"))


def test_no_look_ahead_the_setups_and_orders_of_a_day_ignore_the_bars_after_it(config):
    rng = random.Random(5)
    bars = [Bar(o, h, lo, c, v) for o, h, lo, c, v in _series(rng, 650)]
    days = _days(650)
    ctx = er._context(config, None, days[270], days[-1])
    ranks = [95.0] * len(bars)  # a leader every day: the detectors decide on the price alone
    full = er.ValueInput("FR0000000001", "P0", days, bars, ranks, [], *_costs(config))
    cut = 500
    trunc = er.ValueInput("FR0000000001", "P0", days[: cut + 2], bars[: cut + 2], ranks[: cut + 2], [], *_costs(config))
    a = [
        (r.detector, r.day, r.view, {k: v.order for k, v in r.results.items()})
        for r in er.replay_value(ctx, full, Counter())
    ]
    b = [
        (r.detector, r.day, r.view, {k: v.order for k, v in r.results.items()})
        for r in er.replay_value(ctx, trunc, Counter())
    ]
    assert a and [x for x in a if x[1] <= days[cut]] == [x for x in b if x[1] <= days[cut]]
    for r in er.replay_value(ctx, full, Counter()):
        for res in r.results.values():
            if res.outcome is not None and res.outcome.fill is not None:  # fills from the next session only
                assert res.span[0] > r.day.toordinal() and res.span[2] >= res.span[0]


def _costs(config):
    from app.services.moves import _costs as costs

    return costs(config, Instrument(isin="FR0000000001", mic="XPAR", currency="EUR", market_cap_eur=2e9))


def test_the_speed_filter_never_hides_a_setup(config):
    rng = random.Random(11)
    bars = [Bar(o, h, lo, c, v) for o, h, lo, c, v in _series(rng, 900)]
    dets = {k: v for k, v in config.params.detectors.items() if k in DETECTORS and k != "d8_buzz_precoce"}
    fired = 0
    for i in range(260, len(bars)):
        window = bars[max(0, i - 279) : i + 1]
        for rs in (None, 75.0, 99.0):
            ctx = DetectorContext(rs, [], 0.005, 0.25)
            for name, dp in dets.items():
                if DETECTORS[name](dp).detect(window, ctx):
                    fired += 1
                    assert er._may_fire(name, dp, window, rs), (name, i, rs)
    assert fired > 0


def _proposal(s, isin, day, p_max, stop, targets, price, action, detector="d7_trend_quality"):
    created = datetime(day.year, day.month, day.day, 17, 0, tzinfo=UTC)  # after the close in Paris
    sig = Signal(isin=isin, detector=detector, source="moteur", ts=created, timeframe="eod", data_status="eod",
                 params_version="t", entry_low=p_max + 1, entry_high=p_max, stop_initial=stop,
                 targets=targets)  # fmt: skip
    s.add(sig)
    s.flush()
    conf = {"p_max_technique": p_max + 2, "declencheur": p_max + 1}
    p = Proposal(signal_id=sig.id, isin=isin, action=action, stop=stop, targets=targets, created_at=created,
                 details={"p_max": p_max, "configuration": conf})  # fmt: skip
    s.add(p)
    s.flush()
    s.add(DecisionSnapshot(proposal_id=p.id, taken_at=created, quotes={isin: {"last": price}},
                           features={"atr14": 1.5, "mm20": 99.0}, params_version="t"))  # fmt: skip


def test_the_stored_proposals_are_replayed_with_every_policy(db_engine, config, calendars):
    days = _days(320)
    k = 300
    flat, tail = [(100, 100.5, 99.5, 100)] * k, [(104.5, 105, 104, 104.5)] * 19
    path = {  # hand-built: A comes back to its limit then reaches its target; B never comes back but rises
        "FR0000000001": flat + [(100, 100.2, 99.0, 99.8), (100, 105, 99.8, 104.5)] + tail,
        "FR0000000002": flat + [(100.2, 101, 99.9, 100.8), (101, 105, 100.5, 104.8)] + tail,
    }
    with db_session() as s:
        for isin, rows in path.items():
            s.add(Instrument(isin=isin, name=isin, mic="XPAR", currency="EUR", in_universe=True, market_cap_eur=2e9))
            for d, (o, h, lo, c) in zip(days, rows, strict=False):
                s.add(PriceEod(isin=isin, day=d, open=o, high=h, low=lo, close=c, adj_close=c, volume=100_000,
                               source="eodhd", received_at=NOW, data_status="eod"))  # fmt: skip
        s.add(Instrument(isin="US0000000001", name="X", mic="XNYS", currency="USD", in_universe=True))
        s.flush()
        _proposal(s, "FR0000000001", days[k - 1], 99.5, 97.0, [104.0, 108.0], 100.0, "WATCH")
        _proposal(s, "FR0000000002", days[k - 1], 98.0, 96.0, [104.0], 100.0, "WATCH", "external")
        _proposal(s, "US0000000001", days[k - 1], 98.0, 96.0, [104.0], 100.0, "BUY")
        rep = er.run_proposals(s, config, calendars, days[-1] + timedelta(days=1), min_n=1)
    assert rep["meta"]["rejouees"] == 2 and rep["meta"]["exclues"] == {"hors_perimetre_europeen": 1}
    p0 = rep["par_tous"]["tous"]["politiques"]["P0"]
    assert p0["propositions"] == 2 and p0["executes"] == 1 and p0["issues"] == {"target": 1}
    p1 = rep["par_tous"]["tous"]["politiques"]["P1"]
    assert p1["executes"] == 2 and p1["issues"] == {"target": 2} and p1["r_net_cto_moyen"] > 0
    assert set(rep["par_detecteur"]) == {"d7_trend_quality", "external"}
    assert set(rep["par_position"]) == {"au_dessus"}  # both quoted 100, above their limit
    assert rep["par_tous"]["tous"]["excursions"]["5"]["n"] == 2
    assert "Excursions" in er.render_fr(rep) and "FR0000000001" not in er.render_fr(rep)


def test_the_cli_keeps_the_old_european_history_for_confirmation(capsys):
    from app.cli import cmd_entry_replay

    args = argparse.Namespace(params="config/params.example.yaml", calendars="config/market_calendars.yaml",
                              from_date="2016-06-01", to=None, mode="setups")  # fmt: skip
    assert cmd_entry_replay(args) == 2
    assert "réservées à la confirmation" in capsys.readouterr().out
