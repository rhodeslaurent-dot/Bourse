# ruff: noqa: E501
"""Registration n° 6 (docs/34 §34.13): exit rules on frozen entries, paired per trade — the partial exit and the
chandelier on hand-built bars (stop before target / partial in one session, partial accounting, ratchet, no
look-ahead), the partial leg of the cost model, the frozen plan, its refusals and its run end to end."""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import yaml

from app.domain.research import exit_rules as X
from app.domain.research.costs import Account, LegInputs, economics
from app.domain.research.exits import GAP_STOP, STOP, TARGET, TIME, TRAIL, Bars, ExitSpec, Orders, simulate
from app.domain.research.pipeline import prepare_group
from app.domain.research.prereg import PreregError, check_run_start, file_sha256, parse_prereg
from app.domain.research.registration import run_registration
from app.domain.research.report import assert_no_price_fields, build_report, render_fr
from app.domain.risk import FeeSchedule
from app.services.research import replication_label
from tests.unit.test_research import settings, synthetic_panel

ROOT = Path(__file__).resolve().parents[2]
REG6 = ROOT / "research" / "prereg" / "2026-10-30_reg6.yaml"
REG6_SHA256 = "cdfe966094795cb89c996bc813e9a66fbf41115cb945ec3ef944faa9188c1dae"  # registered on 29/10/2026 evening (docs/34 §34.13, docs/15): the file must never change
PROD = {"zones": {"lookback_sessions": 250, "pivot_window": 5, "merge_atr_mult": 0.5, "min_touches": 2},
        "wick_buffer_pct": 0.005, "stop_buffer_atr_mult": 0.25, "min_reward_risk": 2.0, "expiry_sessions": 3}  # fmt: skip
SLIP = 0.002
XQ = ExitSpec("XQ", 10, trail_atr=3.0, partial_r=1.0, partial_share=0.5)


def _load(path: Path = REG6):
    b = path.read_bytes()
    return parse_prereg(yaml.safe_load(b), file_sha256(b))


@pytest.fixture(scope="module")
def reg6():
    return _load()


def _light(p):
    return dataclasses.replace(p, inference={**p.inference, "bootstrap_draws": 200, "power_reps": 5})


def _bars(rows, atr=10.0, div=None):
    """Hand-built bars, one instrument: rows of (open, high, low, close); row 0 is the signal session."""
    a = np.asarray(rows, dtype=np.float64)
    o, h, lo, c = (np.ascontiguousarray(a[:, k : k + 1]) for k in range(4))
    at = np.broadcast_to(np.asarray(atr, dtype=np.float64).reshape(-1, 1), c.shape).copy()
    z = np.zeros_like(c)
    dv = z if div is None else np.asarray(div, dtype=np.float64).reshape(-1, 1)
    return Bars(o, h, lo, c, at, z, dv, z.astype(bool), np.array([len(rows)]), None)


def _od(limit=101.0, stop=95.0, target=np.nan, n=1):
    return Orders(np.arange(n) * 0, np.zeros(n, dtype=np.int64), np.full(n, limit), np.full(n, np.nan), np.zeros(n),
                  np.full(n, -stop), np.zeros(n), np.full(n, target), np.full(n, np.nan), 1)  # fmt: skip


SIG, FILL = (100, 100, 100, 100), (100, 101, 99.5, 100.5)  # filled at the open 100; stop 95 → level 100 + 5.19
LEVEL = 100 + (100 - 95 * (1 - SLIP))


# --- 1. the engine: partial exit and chandelier on hand-built bars ---------------------------------------------------


def test_partial_sale_at_plus_one_r_then_the_stop_at_the_entry_and_the_accounting_of_both_legs():
    b = _bars([SIG, FILL, (101, 106, 100.5, 105), (104, 104.5, 99.8, 100)])
    r = simulate(b, _od(), XQ, SLIP)
    assert r.partial_pos[0] == 2 and r.partial_px[0] == pytest.approx(LEVEL) and r.partial_qty[0] == 0.5
    assert r.exit_pos[0] == 3 and r.reason[0] == TRAIL and r.exit_px[0] == pytest.approx(100 * (1 - SLIP))
    assert r.gross_return[0] == pytest.approx((0.5 * LEVEL + 0.5 * 100 * (1 - SLIP)) / 100 - 1)
    # an open above the level sells there; a low back to the entry in the sale's session stops the rest at once
    g = simulate(
        _bars([SIG, FILL, (106, 107, 104, 106), (105, 106, 104, 105)]), _od(), dataclasses.replace(XQ, max_hold=3), SLIP
    )
    assert g.partial_px[0] == 106 and g.partial_pos[0] == 2 and g.reason[0] == TIME
    rows = [SIG, FILL, (101, 106, 99.9, 100.5), (101, 102, 100.5, 101)]
    s = simulate(_bars(rows), _od(), XQ, SLIP)
    assert s.partial_pos[0] == 2 and s.exit_pos[0] == 2 and s.reason[0] == TRAIL and s.exit_px[0] == pytest.approx(99.8)
    # order unknown (low 99.9 under the entry, close above it): the optimistic bound keeps the rest; a close under the
    # entry stops it under every rule; ohlc: the dip (1.1) nearer the open than the high (5) came first → kept
    assert simulate(_bars(rows), _od(), XQ, SLIP, "target_first").exit_pos[0] != 2
    assert simulate(_bars(rows), _od(), XQ, SLIP, "ohlc").exit_pos[0] != 2
    under = [SIG, FILL, (101, 106, 99.0, 99.5), (101, 102, 100.5, 101)]
    assert all(
        simulate(_bars(under), _od(), XQ, SLIP, r).exit_pos[0] == 2 for r in ("stop_first", "ohlc", "target_first")
    )
    # dividends after the sale are credited on the remaining half only
    d = simulate(_bars([SIG, FILL, (101, 106, 100.5, 105), (104, 105, 103, 104), (103, 104, 102, 103)],
                       div=[0, 0, 0, 1.0, 0]), _od(), dataclasses.replace(XQ, max_hold=4), SLIP)  # fmt: skip
    assert d.div_cash[0] == pytest.approx(0.5) and d.partial_qty[0] == 0.5


def test_the_stop_comes_first_in_one_session_for_the_partial_and_for_the_target():
    rows = [SIG, FILL, (101, 106, 94.0, 100), (100, 101, 99, 100)]
    for rule in ("stop_first", "ohlc", "target_first"):  # stop and partial level in one session: stopped, no sale
        r = simulate(_bars(rows), _od(), XQ, SLIP, rule)
        assert r.reason[0] == STOP and r.partial_qty[0] == 0 and np.isnan(r.partial_px[0]) and r.exit_pos[0] == 2
    # XP: stop and target 1 inside one session → the stop under the primary rule, the target only under target_first
    xp = ExitSpec("XP", 10, bracket_at_fill=True)
    assert simulate(_bars(rows), _od(target=105.0), xp, SLIP).reason[0] == STOP
    assert simulate(_bars(rows), _od(target=105.0), xp, SLIP, "target_first").reason[0] == TARGET
    # a gap through the stop: the whole position at the open, slipped
    gp = simulate(_bars([SIG, FILL, (93, 107, 92, 100)]), _od(), XQ, SLIP)
    assert gp.reason[0] == GAP_STOP and gp.partial_qty[0] == 0


def test_no_partial_sale_on_a_session_filled_intraday_at_the_limit():
    # open 100.5 above the limit 100, low 99.9 under it: filled at 100 during the session; its high 106 may precede it
    rows = [SIG, (100.5, 106, 99.9, 101), (101, 104, 101, 102), (102, 103, 101.5, 102)]
    r = simulate(_bars(rows), _od(limit=100.0), dataclasses.replace(XQ, max_hold=3), SLIP)
    assert r.entry[0] == 100 and not r.at_open[0] and r.partial_qty[0] == 0 and r.reason[0] == TIME


def test_the_chandelier_ratchets_up_from_the_next_session_and_is_never_lowered():
    rows = [SIG, FILL, (101, 110, 98.5, 109), (108.5, 109, 107.5, 108), (107.8, 108, 106, 107)]
    atr = [1.0, 1.0, 1.0, 5.0, 1.0]  # a wider ATR on session 3 would lower the candidate: ignored
    r = simulate(_bars(rows, atr=atr), _od(stop=90.0), ExitSpec("X4", 10, trail_atr=3.0), SLIP)
    # stop 98 after the fill session (101 − 3); session 2: its own high 110 does not raise its own stop (its low 98.5,
    # under 107, is no exit); 107 from session 3 on
    assert r.exit_pos[0] == 4 and r.reason[0] == TRAIL and r.exit_px[0] == pytest.approx(107 * (1 - SLIP))
    xq = simulate(_bars(rows, atr=atr), _od(stop=90.0), XQ, SLIP)  # +1 R = 110.2 never reached: XQ ≡ X4 here
    assert xq.partial_qty[0] == 0 and xq.exit_pos[0] == 4 and xq.exit_px[0] == r.exit_px[0]


def test_xq_equals_x4_on_every_trade_that_never_reached_plus_one_r():
    rng = np.random.default_rng(5)
    n, length = 400, 30
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, (length, n)), axis=0))
    o = np.vstack([c[:1], c[:-1]]) * np.exp(rng.normal(0, 0.005, (length, n)))
    h, lo = (
        np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.01, (length, n)))),
        np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.01, (length, n)))),
    )
    z = np.zeros_like(c)
    b = Bars(o, h, lo, c, np.full_like(c, 2.0), z, z, z.astype(bool), np.full(n, length), None)
    od = Orders(np.arange(n), np.zeros(n, dtype=np.int64), o[1] + 1, np.full(n, np.nan), np.zeros(n), -(o[1] * 0.94),
                np.zeros(n), np.full(n, np.nan), np.full(n, np.nan), 1)  # fmt: skip
    q = simulate(b, od, dataclasses.replace(XQ, max_hold=25), SLIP)
    x4 = simulate(b, od, ExitSpec("X4", 25, trail_atr=3.0), SLIP)
    never = q.partial_qty == 0
    assert 0 < never.sum() < n
    for k in ("exit_pos", "exit_px", "reason", "entry", "hi", "lo"):
        assert np.array_equal(getattr(q, k)[never], getattr(x4, k)[never], equal_nan=True)
    assert (q.partial_px[~never] >= q.entry[~never]).all()
    off = simulate(b, od, dataclasses.replace(XQ, max_hold=25, partial_r=0.0), SLIP)  # no partial: X4 exactly
    assert np.array_equal(off.exit_px, x4.exit_px, equal_nan=True) and (off.partial_qty == 0).all()


def test_the_partial_leg_of_the_cost_model():
    one = np.ones(1)

    def leg(pq, pp):
        return LegInputs(entry=one * 100, exit_px=one * 110, stop_exec=one * 95, div_cash=one * 0, fx_in=one, fx_out=one,
                         ttf=one * 0, ftt=one * 0, fx_fee=one * 0, haircut=one * 0, hs_in=one * 0.001, hs_out=one * 0.001,
                         days=one * 10, unit_frac=one * 0.1, stop_exit=np.zeros(1, dtype=bool), exit_slip=one * 0,
                         partial_px=one * pp, partial_qty=one * pq, fx_part=one, hs_part=one * 0.001)  # fmt: skip

    acct = Account("cto", FeeSchedule("x", 0.001, 2.0))
    none = economics(dataclasses.replace(leg(0.0, np.nan), partial_px=None, partial_qty=None), acct, 300.0, 1e9)
    assert economics(leg(0.0, np.nan), acct, 300.0, 1e9).r_stop[0] == none.r_stop[0]  # no sale: the old legs exactly
    e = economics(leg(0.5, 105.0), acct, 300.0, 1e9)
    s = float(e.notional_frac[0]) / 100  # fractional shares: sizing unchanged (initial slipped stop)
    assert s == pytest.approx(float(none.notional_frac[0]) / 100)
    sell_p, sell_r = s * 0.5 * 105, s * 0.5 * 110
    fees = max(2.0, 0.001 * s * 100) + max(2.0, 0.001 * sell_p) + max(2.0, 0.001 * sell_r)
    net = sell_p + sell_r - s * 100 - fees - 0.001 * (s * 100 + sell_p + sell_r)
    assert e.r_stop[0] == pytest.approx(net / (s * 5), rel=1e-9)


# --- 2. the frozen plan ------------------------------------------------------------------------------------------------


def test_plan_6_is_the_registered_file(reg6):
    """The plan is frozen: its sha256 is the one registered in docs/34 §34.13 and docs/15 (29/10/2026 evening)."""
    assert file_sha256(REG6.read_bytes()) == REG6_SHA256 == reg6.sha256
    assert REG6_SHA256 in (ROOT / "docs" / "34-hausses-manquees.md").read_text(encoding="utf-8")
    assert REG6_SHA256[:8] in (ROOT / "docs" / "15-status.md").read_text(encoding="utf-8")


def test_plan_6_parses_with_its_cells_families_and_sealed_replication(reg6):
    assert reg6.id == "2026-10-30_reg6" and reg6.is_exit_plan and not reg6.is_entry_plan
    assert reg6.counts() == {"confirmatoires": 2, "exploratoires_etape0": 27, "etape1_plafond": 0,
                             "diagnostics_f0": 0, "etape2_plafond": 3, "total": 32} and reg6.max_total_cells == 34  # fmt: skip
    c1, c2 = reg6.confirmatory
    assert (c1.trigger, c1.entry, c1.exit, c1.baseline_exit, c1.h, c1.ref_h, c1.unit) == (
        "d7x",
        "P1",
        "X4",
        "XP",
        60,
        60,
        "r_stop",
    )
    assert (c2.exit, c2.baseline_exit) == ("XQ", "XP")
    assert sorted({c.family for c in reg6.cells if c.diagnostic}) == ["X1", "X3", "X4", "X6", "X8", "X9"]
    tested = [c for c in reg6.exploratory if not c.diagnostic]
    assert len(tested) == 13 and {c.family for c in tested} == {"X2", "X5", "X7"}
    assert {c.unit for c in reg6.cells if c.family == "X7"} == {"eur_300"} and not any(
        c.trigger == "t17" for c in reg6.cells
    )
    x2 = [c for c in reg6.cells if c.family == "X2"]
    assert {c.h for c in x2} == {20, 60} and {c.ref_h for c in x2} == {60}
    assert reg6.replication == (date(2007, 1, 2), date(2015, 9, 30))
    assert (
        reg6.assumptions["replication_shared_with"] == ["2026-10-30_reg4"]
        and reg6.assumptions["dsr_prior_trials"] == 13
    )
    with pytest.raises(PreregError):
        check_run_start(reg6, date(2015, 9, 30))
    # refusals: a missing paired threshold, an unknown baseline, r_stop across different stops, t14 under XP
    for edit, match in (
        (lambda r: r["thresholds"]["paired"].pop("max_dd_worse_ratio"), "appariés"),
        (lambda r: r["confirmatory"][0].update(baseline_exit="X9"), "inconnue"),
        (lambda r: r["exploratory"][8].update(unit="r_stop"), "r_stop"),
        (lambda r: r["exploratory"][3].update(baseline_exit="XP"), "t14"),
        (lambda r: r["confirmatory"][0].update(entry="P0"), "P1 ou P2"),
    ):
        raw = yaml.safe_load(REG6.read_bytes())
        edit(raw)
        with pytest.raises(PreregError, match=match):
            parse_prereg(raw, "x")


def test_the_unseal_verdict_and_the_shared_replication_label(reg6):
    th = reg6.thresholds
    ok = X.exit_unseal_verdict({"n": 400, "delta_moyen": 0.04, "ic": [-0.02, 0.1]}, {"mean_excess": 0.08}, th, "propre")
    assert ok["verdict_a"] and "demi-risque" not in ok["verdict"]
    semi = X.exit_unseal_verdict(
        {"n": 400, "delta_moyen": 0.04, "ic": [-0.02, 0.1]}, {"mean_excess": 0.08}, th, "semi-propre"
    )
    assert semi["verdict_a"] and "demi-risque" in semi["verdict"]
    assert not X.exit_unseal_verdict({"delta_moyen": 0.04, "ic": [-0.06, 0.1]}, {"mean_excess": 0.08}, th, "propre")[
        "verdict_a"
    ]
    assert not X.exit_unseal_verdict({"delta_moyen": -0.01, "ic": [-0.02, 0.1]}, {"mean_excess": 0.08}, th, "propre")[
        "verdict_a"
    ]
    rows = [{"timestamp": "20270312_101500", "kind": "unseal", "prereg_id": "2026-10-30_reg4"}]
    assert replication_label(rows, reg6, "20270312") == "propre"  # the same day: one session
    assert replication_label(rows, reg6, "20270313").startswith("semi-propre")
    assert replication_label([{**rows[0], "kind": "run"}], reg6, "20270313") == "propre"


# --- 3. the plan end to end on synthetic data ------------------------------------------------------------------------


def _trending_panel(n_inst=40, n_days=1600, seed=3):
    extra = np.zeros((n_days, n_inst))
    extra[:, : n_inst // 2] = 0.0012
    return synthetic_panel(n_inst=n_inst, n_days=n_days, seed=seed, lr_extra=extra)


def test_both_arms_share_every_fill_and_the_excursions_never_read_after_the_exit(reg6):
    st = settings()
    g = prepare_group(0, _trending_panel(), st)
    ep, xp = X.entry_params(reg6, PROD), X.exit_params(reg6)
    c1 = reg6.confirmatory[0]
    ts = np.arange(400, 1400, 9, dtype=np.int64)
    cols = (np.arange(ts.size) % 40).astype(np.int64)
    close = g.p.close[ts, cols]
    from app.domain.research.entry_policies import SetupTable

    su = SetupTable(ts, cols, g.p.high[ts, cols] * 1.002, g.p.high[ts, cols] * 1.008, close * 0.94, close * 1.12,
                    close * 1.3, np.full(ts.size, 0.004))  # fmt: skip
    base = X.entry_orders("P1", g, su, c1, ep)
    defs = X.arm_defs(c1)
    a, sa = X.run_arm(g, c1, su, base, defs["sortie"], st, xp)
    b, sb = X.run_arm(g, c1, su, base, defs["reference"], st, xp)
    ok = sb.fill_pos >= 0
    assert ok.any() and np.array_equal(sa.fill_pos[ok], sb.fill_pos[ok]) and np.array_equal(sa.entry[ok], sb.entry[ok])
    assert (b["invalid"] == (sa.fill_pos >= 0) & (sb.fill_pos < 0)).all()  # XP's frame only removes fills
    assert np.allclose(sa.stop_exec_init[ok], sb.stop_exec_init[ok])  # the same initial stop: r_stop comparable
    ex = X.excursion_rows(g, su, base, sa, a)
    poisoned = dataclasses.replace(g.bars, high=g.bars.high.copy(), low=g.bars.low.copy())
    cl = np.nonzero(a["closed"].astype(bool) & np.isfinite(a["r"]))[0]
    last = {int(su.cols[k]): k for k in cl[np.argsort(sa.exit_pos[cl])].tolist()}  # the last trade of each value
    for col, k in last.items():  # every bar after its exit pushed far away
        poisoned.high[sa.exit_pos[k] + 1 :, col] *= 3.0
        poisoned.low[sa.exit_pos[k] + 1 :, col] *= 0.3
    ex2 = X.excursion_rows(dataclasses.replace(g, bars=poisoned), su, base, sa, a)
    keep = np.isin(cl, list(last.values()))
    assert keep.sum() >= 10
    for k in ("mfe_5", "mfe_60", "mae_60", "mfe_tout"):
        assert np.allclose(ex[k][keep], ex2[k][keep], equal_nan=True)
    assert (ex["mfe_60"] >= ex["mfe_5"] - 1e-12).all() and (ex["mae_60"] <= 1e-12).all()
    summ = X.excursion_summary(ex)
    assert set(summ["mfe_r_20_seances"]) == {"q10", "q25", "q50", "q75", "q90"}
    assert_no_price_fields(summ)


def test_plan_6_runs_end_to_end_placebo_first_and_reports_aggregates_only(reg6):
    p = _trending_panel()
    st = dataclasses.replace(settings(), min_earnings_coverage=0.5)
    res = run_registration([p], _light(reg6), st, (), PROD, "all", None, None, False)
    cells = res["cellules"]
    assert set(cells) == {c.id for c in reg6.cells}
    order = res["regles_de_sortie"]["ordre_de_lecture"]
    assert order[:4] == sorted(c.id for c in reg6.cells if c.trigger == "t00") and order[4:6] == ["C1", "C2"]
    assert order[-3:] == ["X4.t14.exitX1", "X4.t14.exitX4", "X4.t14.exitX6"]
    for cid in ("C1", "C2"):
        s = cells[cid]
        assert s["n"] > 0 and s["p_holm"] is not None and s["niveau"] in ("CANDIDAT", "REJET")
        assert s["decision_a"].startswith("règle de sortie : ") and set(s["delta_par_regle"]) == {
            "stop_first",
            "ohlc",
            "target_first",
        }
        assert s["exces_sur_placebo"]["placebo"] == ("X1.t00.exitX4" if cid == "C1" else "X1.t00.exitXQ")
        assert s["dsr_essais"] == 13 + 14 and set(s["criteres"]) >= {
            "A1",
            "dsr",
            "regles_parcours",
            "couts_stresses",
            "recul_portefeuille",
        }
        assert set(s["bras"]) == {"sortie", "reference"} and "delta_eur_300" in s
    assert cells["C2"]["bras"]["sortie"]["part_vente_partielle"] >= 0
    assert all(cells[c.id]["niveau"] == "DIAGNOSTIC" for c in reg6.cells if c.diagnostic)
    assert cells["X2.d7x.exitXS.h20"].get("p_romano_wolf") is not None and "p_romano_wolf" not in cells["X1.t00.exitX4"]
    assert "pbo_famille" in cells["X7.d7x.exitX8"] and cells["X7.d7x.exitX8"]["unite"] == "eur_300"
    ex = cells["X8.d7x.exitXS"]
    assert {"excursions_P1", "excursions_P0", "bras_P1", "bras_P0"} <= set(ex)
    assert set(res["etape2"]) == {"XP", "X4", "XQ"} and res["titre"]
    rep = build_report(_light(reg6), res, {"limites": []})
    assert_no_price_fields(rep)
    assert rep["A_resume"]["titre"] == res["titre"] and set(rep["A_resume"]["confirmatoires"]) == {"C1", "C2"}
    txt = render_fr(rep)
    assert "S — Règles de sortie" in txt and txt.index("X1.t00.exitX4 [placebo") < txt.index("  C1 [confirmatoire]")
