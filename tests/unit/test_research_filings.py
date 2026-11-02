# ruff: noqa: E501
"""Registration n° 8 (docs/34 §34.15): the official AMF releases (lot L1) as the trigger t19 — screening (other family,
rectification, foreign ISIN, no time), placement on the sessions (release before / after the close, a date without
an hour after it), one signal per value every 10 sessions, the shifted control, no archive → no signal, the frozen
descriptive plan and a small end-to-end synthetic run."""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest
import yaml

from app.domain.research import filing_triggers as FT
from app.domain.research.filing_triggers import CellRule, FilingData, FilingRow, place, rules_from, screen
from app.domain.research.pipeline import cell_signals, prepare_group, raw_trigger
from app.domain.research.prereg import PreregError, file_sha256, parse_prereg
from app.domain.research.registration import run_registration
from app.domain.research.report import assert_no_price_fields, build_report
from tests.unit.test_research import settings, synthetic_panel

ROOT = Path(__file__).resolve().parents[2]
REG5 = ROOT / "research" / "prereg" / "2026-10-30_reg5.yaml"
REG8 = ROOT / "research" / "prereg" / "2026-11-02_reg8.yaml"
# frozen on 02/11/2026 before any run of the bench on the releases (docs/34 §34.15, docs/15): never edited
REG8_SHA256 = "85f0949e370d523e129469b600af06b65a54cbf31c9d2b98229e6cdf6a2627ec"


def _load(path: Path):
    b = path.read_bytes()
    return parse_prereg(yaml.safe_load(b), file_sha256(b))


@pytest.fixture(scope="module")
def reg8():
    return _load(REG8)


@pytest.fixture(scope="module")
def rules(reg8):
    return rules_from(reg8.assumptions)


IP = CellRule.of({"family": "info_privilegiee", "dedup_sessions": 10})


def _fr(panel):
    """The synthetic panel with French ISINs (``XX…`` → ``FR…``): the flow only covers French issuers."""
    return dataclasses.replace(panel, isins=tuple("FR" + x[2:] for x in panel.isins))


def _row(isin: str, ts: datetime | None, family: str = "info_privilegiee", **kw) -> FilingRow:
    return FilingRow(isin, ts, family, **kw)


def _at(day: np.datetime64, hour: int = 9, minute: int = 0) -> datetime:
    d = date.fromisoformat(str(day))
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=UTC)


# --- 1. screening ---------------------------------------------------------------------------------------------------


def test_screen_keeps_first_notices_of_the_family_with_a_french_isin_and_a_time():
    ts = datetime(2024, 3, 5, 9, 0, tzinfo=UTC)
    ok = _row("FR0000120107", ts)
    assert screen(ok, IP) == "retenue"
    assert screen(dataclasses.replace(ok, family="resultats"), IP) == "autre_famille"
    assert screen(dataclasses.replace(ok, update_status="REC"), IP) == "rectificatif_ou_complement"
    assert screen(dataclasses.replace(ok, update_status="ADD"), IP) == "rectificatif_ou_complement"
    assert screen(dataclasses.replace(ok, update_status="NEW"), IP) == "retenue"
    assert screen(dataclasses.replace(ok, isin="NL0000235190"), IP) == "sans_isin_francais"
    assert screen(dataclasses.replace(ok, isin=None), IP) == "sans_isin_francais"
    assert screen(dataclasses.replace(ok, market_timestamp=None), IP) == "sans_heure"
    assert screen(dataclasses.replace(ok, time_precision="illisible"), IP) == "sans_heure"
    assert screen(dataclasses.replace(ok, time_precision="jour"), IP) == "retenue"  # placed after the close


def test_cell_rule_and_plan_refuse_an_unknown_family_or_missing_rules(reg8):
    with pytest.raises(ValueError, match="famille"):
        CellRule.of({"family": "rumeur"})  # removed on 02/11/2026: not in the closed list
    with pytest.raises(ValueError, match="dedup"):
        FT.check_filing_params({"family": "resultats", "dedup_sessions": 0})
    raw = yaml.safe_load(REG8.read_bytes())
    bad = {**raw, "confirmatory": [{**raw["confirmatory"][0], "params": {"family": "inconnue"}}]}
    with pytest.raises(PreregError, match="C1"):
        parse_prereg(bad, "x")
    no_rule = {**raw, "assumptions": {k: v for k, v in raw["assumptions"].items() if k != "filing_max_gap_days"}}
    with pytest.raises(PreregError, match="filing_max_gap_days"):
        parse_prereg(no_rule, "x")


# --- 2. placement on the value's own sessions -----------------------------------------------------------------------


def test_release_before_the_close_signals_that_session_after_it_or_without_an_hour_the_next(rules):
    p = _fr(synthetic_panel(n_inst=3, n_days=150, start="2024-01-01", group="PA"))  # Monday 1 January 2024
    valid = np.ones(p.shape, dtype=bool)
    valid[7, 0] = False  # Wednesday 10 January: no bar for the value
    valid[40:52, 2] = False  # a long suspension of value 2
    isin0, isin1, isin2 = p.isins

    def at(day: str, hh: int, mm: int, **kw) -> FilingRow:
        return _row(isin0, datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}:00+00:00"), **kw)

    cases = [
        (at("2024-01-03", 6, 30), "2024-01-03"),  # 07:30 Paris (winter): before the open → that session
        (at("2024-01-03", 16, 30), "2024-01-03"),  # 17:30 Paris exactly: still that session
        (at("2024-01-03", 16, 45), "2024-01-04"),  # 17:45 Paris: after the close → the next session
        (at("2024-01-05", 18, 0), "2024-01-08"),  # Friday 19:00 Paris → Monday
        (at("2024-01-06", 9, 0), "2024-01-08"),  # Saturday → Monday
        (at("2024-01-10", 8, 0), "2024-01-11"),  # no own bar that Wednesday → the value's next session
        (at("2024-06-04", 15, 45), "2024-06-05"),  # 17:45 Paris in summer (UTC+2): after the close
        (at("2024-01-15", 23, 0, time_precision="jour"), "2024-01-17"),  # 00:00 Paris on 16/01, no hour → next session
    ]
    data = FilingData(tuple(r for r, _ in cases), rules)
    pl = place(p, valid, data, IP)
    assert sorted(str(p.days[t]) for t in pl.ts.tolist()) == sorted(d for _, d in cases)
    assert pl.counts == {"placees": len(cases)}
    far = _row(isin2, _at(p.days[40]))  # suspended more than the plan's 7 days
    other = _row(isin1, _at(p.days[20]))  # column 1: not Paris (is_xpar False)
    outside = _row("FR9999999999", _at(p.days[20]))
    pl2 = place(p, valid, FilingData((far, other, outside), rules), IP)
    assert pl2.ts.size == 0 and pl2.counts == {"sans_seance_proche": 1, "hors_paris": 1, "hors_panneau": 1}


# --- 3. one signal per value every 10 sessions, the shift, no archive ------------------------------------------------


def _panel_with(spec, n_days=600, seed=4, n_inst=6, family="info_privilegiee"):
    p = _fr(synthetic_panel(n_inst=n_inst, n_days=n_days, seed=seed, start="2020-06-01", group="PA"))
    return p, [
        _row(p.isins[c], _at(p.days[t]), fam) for t, c, fam in ((*x, family) if len(x) == 2 else x for x in spec)
    ]


def _cell(plan, cid):
    return next(c for c in plan.cells if c.id == cid)


def test_one_signal_per_value_every_10_sessions_the_shift_and_the_other_families(reg8, rules):
    spec = [(300, 0), (305, 0), (310, 0), (312, 0), (400, 2), (404, 2), (450, 4, "resultats"), (480, 1)]
    p, rows = _panel_with(spec)
    st = dataclasses.replace(settings(), filings=FilingData(tuple(rows), rules))
    g = prepare_group(0, p, st)

    def sig(cid):
        m, _ = raw_trigger(g, _cell(reg8, cid), st)
        return sorted((int(t), int(c)) for t, c in zip(*np.nonzero(m), strict=True))

    # the first release, nothing until 10 sessions later (305 dropped; 310 kept; 312 dropped; 404 dropped), value 1 is
    # not listed in Paris, the results release belongs to another family
    assert sig("C1") == [(300, 0), (310, 0), (400, 2)] == sig("C2") == sig("C3")
    assert sig("X2.t19.h10") == [(320, 0), (330, 0), (420, 2)]  # +20 own sessions
    assert sig("X3.t19.h10") == [(450, 4)]  # results
    s1 = cell_signals(g, _cell(reg8, "C1"), st, (0, p.shape[0] - 1))
    assert sorted(zip(s1.ts.tolist(), s1.cols.tolist(), strict=True)) == sig("C1")
    st0 = dataclasses.replace(st, filings=None)  # without the archive nothing is known: no signal
    assert not raw_trigger(prepare_group(0, p, st0), _cell(reg8, "C1"), st0)[0].any()


# --- 4. the frozen descriptive plan ---------------------------------------------------------------------------------


def test_plan_8_is_frozen_descriptive_with_its_cells_and_the_thresholds_of_plan_5(reg8):
    b = REG8.read_bytes()
    assert hashlib.sha256(b).hexdigest() == REG8_SHA256 == reg8.sha256  # any edit is a new registration
    assert REG8_SHA256 in (ROOT / "docs" / "34-hausses-manquees.md").read_text(encoding="utf-8")
    assert REG8_SHA256[:8] in (ROOT / "docs" / "15-status.md").read_text(encoding="utf-8")
    assert reg8.id == "2026-11-02_reg8" and reg8.descriptive and not reg8.sealed_confirmation
    assert reg8.venue_groups == {"PA": ("PA",)} and reg8.replication is None
    assert reg8.segments.discovery == (date(2017, 1, 2), date(2026, 10, 30))
    assert reg8.counts()["total"] == 14
    c1, c2, c3 = reg8.confirmatory
    assert (c1.trigger, c1.h, c2.h, c3.h, c1.exit, c1.entry_cap_atr, c1.validity) == ("t19", 5, 10, 20, "X3", 1.0, 1)
    assert c1.params == {"family": "info_privilegiee", "dedup_sessions": 10}
    assert set(reg8.decision) == {"ordre_de_lecture", "derive_retenue", "effet_de_profil", "pea"}
    reg5 = _load(REG5)
    for k in ("a1", "a2", "a3", "a4", "a5", "a9", "a10", "a12", "a13", "gate_t_week", "watchlist_bh_q", "power_min",
              "report_min_n", "candidates_max", "stage1", "existing_detectors_ci_high_max"):  # fmt: skip
        assert reg8.thresholds[k] == reg5.thresholds[k], k
    assert reg8.inference == reg5.inference
    r = rules_from(reg8.assumptions)
    assert (r.close_cutoff.hour, r.close_cutoff.minute, r.max_gap_days) == (17, 30, 7)


# --- 5. a small end-to-end synthetic run ----------------------------------------------------------------------------


def test_registration_8_end_to_end_signals_controls_and_coverage(reg8, rules):
    n_days = 2700  # 2016-06-01 → ~2026-12
    row = {str(d): k for k, d in enumerate(synthetic_panel(n_inst=1, n_days=n_days, start="2016-06-01").days)}
    spec = [(t, (k % 4) * 2) for k, t in enumerate(range(row["2017-01-03"], row["2026-07-01"], 13))]
    p = _fr(synthetic_panel(n_inst=8, n_days=n_days, seed=7, start="2016-06-01", group="PA"))
    rows = [_row(p.isins[c], _at(p.days[t])) for t, c in spec]
    rows += [_row(p.isins[0], _at(p.days[row["2018-03-01"]]), update_status="REC")]  # rectification: excluded
    st = dataclasses.replace(settings(), filings=FilingData(tuple(rows), rules))
    inf = {**reg8.inference, "bootstrap_draws": 200, "permutation_draws": 50, "power_reps": 5}
    plan = dataclasses.replace(reg8, inference=inf)
    res = run_registration([p], plan, st, stages="0", f0=False)
    cells = res["cellules"]
    assert 0 < cells["C1"]["signaux"] <= len(spec) and cells["C1"]["signaux"] == cells["C3"]["signaux"]
    assert cells["X2.t19.h10"]["signaux"] > 0 and cells["X1.t00.h5"]["signaux"] > 0
    cov = FT.coverage(rows, IP)
    assert cov["entonnoir"]["retenue"] == len(spec) and cov["entonnoir"]["rectificatif_ou_complement"] == 1
    cov["placement"] = place(p, prepare_group(0, p, st).f.valid, st.filings, IP).counts
    txt = FT.coverage_text(cov)
    assert "info_privilegiee" in txt and "retenues par mois" in txt and "2018" in txt
    report = build_report(plan, res, {"limites": []})
    assert_no_price_fields(report)


def test_a_descriptive_plan_prints_its_reading_rule_and_never_the_sealed_wording(reg8):
    from app.services.research import DESCRIPTIVE_ONLY, _decision_text

    txt = _decision_text(reg8)
    assert "Règle de lecture écrite dans le plan" in txt and "derive_retenue" in txt and "effet_de_profil" in txt
    assert "contrôles appariés" in DESCRIPTIVE_ONLY and "scellée" not in DESCRIPTIVE_ONLY
    assert _decision_text(_load(REG5)) == ""  # a plan without a decision rule prints nothing
