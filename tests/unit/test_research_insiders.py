# ruff: noqa: E501
"""Registration n° 5 (docs/34 §34.12): the managers' dealing declarations (AMF BDIF) as the trigger t17 — screening
(unknown amount excluded, options and off-market excluded), placement on the sessions (publication before / after
the close), one signal per value every 20 sessions, the sales / shifted / cluster controls, the frozen plan and its
sha256, the forward confirmation rule and a small end-to-end synthetic run with the A1 abstention."""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest
import yaml

from app.domain.research import insider_triggers as IT
from app.domain.research.insider_triggers import CellRule, InsiderData, InsiderRow, place, rules_from, screen
from app.domain.research.pipeline import cell_signals, prepare_group, raw_trigger, signal_rows
from app.domain.research.prereg import file_sha256, parse_prereg
from app.domain.research.registration import run_registration
from app.domain.research.report import assert_no_price_fields, build_report
from tests.unit.test_research import settings, synthetic_panel

ROOT = Path(__file__).resolve().parents[2]
REG3 = ROOT / "research" / "prereg" / "2026-10-28_reg3.yaml"
REG5 = ROOT / "research" / "prereg" / "2026-10-30_reg5.yaml"
# frozen on 29/10/2026 before any historical declaration was loaded (docs/34 §34.12, docs/15): never edited
REG5_SHA256 = "5d1e4a1df0e194dad8cc6176b2356737192267b6be81639fc4a87bc9ee512d3d"


def _load(path: Path):
    b = path.read_bytes()
    return parse_prereg(yaml.safe_load(b), file_sha256(b))


@pytest.fixture(scope="module")
def reg5():
    return _load(REG5)


@pytest.fixture(scope="module")
def rules(reg5):
    return rules_from(reg5.assumptions)


C1 = CellRule.of({"side": "purchase", "roles": "executive", "min_amount_eur": 10000, "dedup_sessions": 20})
TX = {"instrument": "Action", "nature": "acquisition", "nature_detail": "Acquisition", "venue": "Euronext Paris - XPAR"}


def _row(isin: str, ts: datetime, price: float | None = 50.0, volume: float | None = 1000.0, **kw) -> InsiderRow:
    tx = {**TX, **kw.pop("tx", {})}
    base = dict(parse_status="parsed", role="PDG", nature="acquisition", nature_detail="Acquisition",
                venue=tx["venue"], currency="EUR", quote_currency="EUR", isin_source="declaration", transactions=(tx,))  # fmt: skip
    base.update(kw)
    return InsiderRow(isin, ts, price=price, volume=volume, **base)  # type: ignore[arg-type]


def _at(day: np.datetime64, hour: int = 9, minute: int = 0) -> datetime:
    d = date.fromisoformat(str(day))
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=UTC)


# --- 1. screening: unknown is excluded, never guessed --------------------------------------------------------------


def test_screen_keeps_on_market_executive_purchases_and_excludes_the_unknown(rules):
    ts = datetime(2024, 3, 5, 9, 0, tzinfo=UTC)
    ok = _row("FR0000000001", ts)
    assert screen(ok, rules, C1) == "retenue"
    assert screen(dataclasses.replace(ok, price=None), rules, C1) == "montant_inconnu"  # amount unknown → excluded
    assert screen(dataclasses.replace(ok, volume=None), rules, C1) == "montant_inconnu"
    assert screen(dataclasses.replace(ok, volume=150.0), rules, C1) == "sous_seuil"  # 7 500 € < 10 000 €
    assert screen(dataclasses.replace(ok, parse_status="listing_only"), rules, C1) == "non_analysee"
    assert screen(dataclasses.replace(ok, isin=None), rules, C1) == "sans_isin"
    assert screen(dataclasses.replace(ok, nature="mixte"), rules, C1) == "autre_nature"
    assert screen(dataclasses.replace(ok, role="administrateur"), rules, C1) == "fonction"
    assert screen(dataclasses.replace(ok, role="personne liée"), rules, C1) == "fonction"
    anyrole = dataclasses.replace(C1, roles="any")
    assert screen(dataclasses.replace(ok, role="administrateur"), rules, anyrole) == "retenue"
    # currency: stated euro, or not stated on a value quoted in euros; anything else is excluded
    assert screen(dataclasses.replace(ok, currency=None), rules, C1) == "retenue"
    assert screen(dataclasses.replace(ok, currency=None, quote_currency=None), rules, C1) == "devise"
    assert screen(dataclasses.replace(ok, currency="USD"), rules, C1) == "devise"
    # options, free shares, subscriptions, other instruments: excluded
    for tx in ({"instrument": "Option"}, {"nature_detail": "Acquisition définitive d'actions gratuites"},
               {"nature_detail": "Exercice de stock-options"}, {"instrument": "Bon de souscription d’actions"},
               {"instrument": None}, {"nature_detail": "Acquisition dans le cadre d'un plan d'épargne"}):  # fmt: skip
        assert screen(_row("FR0000000001", ts, tx=tx), rules, C1) == "instrument_ou_operation_exclus", tx
    assert screen(dataclasses.replace(ok, transactions=()), rules, C1) == "instrument_ou_operation_exclus"
    # venue: off-market, unknown or unrecognised → excluded
    assert (
        screen(_row("FR0000000001", ts, tx={"venue": "En dehors d'une plateforme de négociation"}), rules, C1)
        == "hors_marche"
    )
    assert screen(_row("FR0000000001", ts, tx={"venue": None}, venue=None), rules, C1) == "lieu_inconnu"
    assert screen(_row("FR0000000001", ts, tx={"venue": "Paris"}), rules, C1) == "lieu_non_reconnu"
    assert screen(_row("FR0000000001", ts, tx={"venue": "Euronext Growth Paris"}), rules, C1) == "retenue"
    # the sales control reads cessions only
    sale = dataclasses.replace(C1, side="sale")
    assert screen(ok, rules, sale) == "autre_nature"
    assert (
        screen(
            _row("FR0000000001", ts, nature="cession", tx={"nature": "cession", "nature_detail": "Cession"}),
            rules,
            sale,
        )
        == "retenue"
    )


# --- 2. placement on the value's own sessions -----------------------------------------------------------------------


def test_publication_before_the_close_signals_that_session_after_it_the_next(rules):
    p = synthetic_panel(n_inst=3, n_days=150, start="2024-01-01", group="PA")  # Monday 1 January 2024
    valid = np.ones(p.shape, dtype=bool)
    valid[7, 0] = False  # Wednesday 10 January: no bar for the value (suspended that day)
    valid[40:52, 2] = False  # a long suspension of value 2
    row = {str(d): k for k, d in enumerate(p.days)}
    isin0, isin1, isin2 = p.isins

    def at(day: str, hh: int, mm: int) -> InsiderRow:
        return _row(isin0, datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}:00+00:00"),
                    price=float(p.close[row.get(day, 0), 0]))  # fmt: skip

    cases = [
        (at("2024-01-03", 9, 0), "2024-01-03"),  # 10:00 Paris (winter, UTC+1): that session
        (at("2024-01-03", 16, 30), "2024-01-03"),  # 17:30 Paris exactly: still that session
        (at("2024-01-03", 17, 5), "2024-01-04"),  # 18:05 Paris: after the close → the next session
        (at("2024-01-05", 18, 0), "2024-01-08"),  # Friday 19:00 Paris → Monday
        (at("2024-01-06", 9, 0), "2024-01-08"),  # Saturday → Monday
        (at("2024-01-10", 8, 0), "2024-01-11"),  # no own bar that Wednesday → the value's next session
        (at("2024-06-04", 15, 45), "2024-06-05"),  # 17:45 Paris in summer (UTC+2): after the close
        (at("2024-06-04", 15, 15), "2024-06-04"),  # 17:15 Paris in summer: that session
    ]
    data = InsiderData(tuple(r for r, _ in cases), rules)
    pl = place(p, valid, data, C1)
    got = sorted(str(p.days[t]) for t in pl.ts.tolist())
    assert got == sorted(d for _, d in cases) and pl.counts == {"placees": len(cases)}
    # a suspension longer than the plan's 7 days: no placement; a value not listed in Paris: never a signal
    far = _row(isin2, _at(p.days[40]), price=float(p.close[52, 2]))
    other = _row(isin1, _at(p.days[20]), price=float(p.close[20, 1]))  # column 1: not Paris (is_xpar False)
    outside = _row("FR9999999999", _at(p.days[20]))
    garbled = _row(isin0, _at(p.days[30]), price=float(p.close[30, 0]) * 100)  # a price misread × 100
    pl2 = place(p, valid, InsiderData((far, other, outside, garbled), rules), C1)
    assert pl2.ts.size == 0
    assert pl2.counts == {"sans_seance_proche": 1, "hors_paris": 1, "hors_panneau": 1, "prix_incoherent": 1}


# --- 3. one signal per value every 20 sessions, and the controls -------------------------------------------------------


def _panel_with(rows_spec, n_days=600, seed=4, n_inst=6):
    """Synthetic Paris panel and declarations at (calendar row, column, role, nature) — price = the close there."""
    p = synthetic_panel(n_inst=n_inst, n_days=n_days, seed=seed, start="2020-06-01", group="PA")
    rows = []
    for t, c, role, nature in rows_spec:
        tx = {"nature": nature, "nature_detail": "Cession" if nature == "cession" else "Acquisition"}
        rows.append(_row(p.isins[c], _at(p.days[t]), price=float(p.close[t, c]), volume=5000.0, role=role,
                         nature=nature, tx=tx))  # fmt: skip
    return p, rows


def _cell(reg5, cid):
    return next(c for c in reg5.cells if c.id == cid)


def test_one_signal_per_value_every_20_sessions_and_the_sales_shift_and_cluster_controls(reg5, rules):
    spec = [(300, 0, "PDG", "acquisition"), (305, 0, "directeur général délégué", "acquisition"),
            (319, 0, "PDG", "acquisition"), (320, 0, "PDG", "acquisition"), (345, 0, "PDG", "acquisition"),
            (400, 2, "membre du directoire", "acquisition"), (412, 2, "PDG", "acquisition"),
            (450, 4, "administrateur", "acquisition"), (460, 4, "PDG", "cession"),
            (480, 1, "PDG", "acquisition")]  # fmt: skip
    p, rows = _panel_with(spec)
    st = dataclasses.replace(settings(), insiders=InsiderData(tuple(rows), rules))
    g = prepare_group(0, p, st)

    def sig(cid):
        m, _ = raw_trigger(g, _cell(reg5, cid), st)
        return sorted((int(t), int(c)) for t, c in zip(*np.nonzero(m), strict=True))

    # the first purchase, nothing until 20 sessions later (305, 319 dropped; 320, 345 kept; 412 dropped, 12 after
    # 400), value 1 is not listed in Paris
    assert sig("C1") == [(300, 0), (320, 0), (345, 0), (400, 2)] == sig("C2")
    assert sig("X2.t17.h20") == [(460, 4)]  # executive sales only
    assert sig("X3.t17.h20") == [(320, 0), (340, 0), (365, 0), (420, 2)]  # +20 own sessions
    # clusters: two distinct executive roles within 10 sessions (300 PDG + 305 DGD → 305); 400 + 412 are 12 apart
    assert sig("X4.t17.h20") == [(305, 0)]
    assert sig("X5.t17.h20") == [(300, 0), (320, 0), (345, 0), (400, 2), (450, 4)]  # any role
    # through the cell lockout of the pipeline: C1 at h 20 keeps the same signals
    s1 = cell_signals(g, _cell(reg5, "C1"), st, (0, p.shape[0] - 1))
    assert sorted(zip(s1.ts.tolist(), s1.cols.tolist(), strict=True)) == sig("C1")
    # … and the discovery window of the plan (from 2021-11-01, the BDIF retention) keeps the later ones only
    lo, hi = signal_rows(p.days, date(2021, 11, 1), date(2026, 10, 29), 65)
    assert str(p.days[lo]) == "2021-11-01" and 345 < lo <= 400
    assert cell_signals(g, _cell(reg5, "C1"), st, (lo, hi)).ts.tolist() == [400]
    # without the archive nothing is known: no signal (never « no purchase »)
    st0 = dataclasses.replace(st, insiders=None)
    assert not raw_trigger(prepare_group(0, p, st0), _cell(reg5, "C1"), st0)[0].any()


# --- 4. the frozen plan --------------------------------------------------------------------------------------------------


def test_plan_5_is_frozen_with_its_cells_and_the_thresholds_of_plan_3(reg5):
    b = REG5.read_bytes()
    assert hashlib.sha256(b).hexdigest() == REG5_SHA256 == reg5.sha256  # any edit is a new registration
    assert REG5_SHA256 in (ROOT / "docs" / "34-hausses-manquees.md").read_text(encoding="utf-8")
    assert REG5_SHA256[:8] in (ROOT / "docs" / "15-status.md").read_text(encoding="utf-8")
    assert reg5.id == "2026-10-30_reg5" and reg5.seed == 20261030 and reg5.venue_groups == {"PA": ("PA",)}
    assert reg5.counts() == {"confirmatoires": 2, "exploratoires_etape0": 6, "etape1_plafond": 0,
                             "diagnostics_f0": 0, "etape2_plafond": 3, "total": 11}  # fmt: skip
    assert sorted(c.id for c in reg5.cells) == ["C1", "C2", "X1.t00.h10", "X1.t00.h20", "X2.t17.h20", "X3.t17.h20",
                                                "X4.t17.h20", "X5.t17.h20"]  # fmt: skip
    c1, c2 = reg5.confirmatory
    assert (c1.trigger, c1.h, c2.h, c1.exit, c1.entry_cap_atr, c1.validity) == ("t17", 20, 10, "X3", 1.0, 1)
    assert c1.accounts == ("cto", "pea") and c1.params["min_amount_eur"] == 10000 and c1.params["dedup_sessions"] == 20
    assert reg5.replication is None and reg5.segments.discovery == (date(2021, 11, 1), date(2026, 10, 29))
    assert reg5.sealed_window[0] == date(2026, 10, 30)  # the forward period, never read by a run
    reg3 = _load(REG3)
    for k in ("a1", "a2", "a3", "a4", "a5", "a9", "a10", "a12", "a13", "gate_t_week", "watchlist_bh_q", "power_min",
              "report_min_n", "candidates_max", "stage1", "existing_detectors_ci_high_max"):  # fmt: skip
        assert reg5.thresholds[k] == reg3.thresholds[k], k  # A1–A13 verbatim from registration n° 3
    assert reg5.inference == reg3.inference
    r = rules_from(reg5.assumptions)
    from app.data.providers.amf_bdif import ROLES

    assert r.executive_roles <= set(ROLES) and len(r.executive_roles) == 5  # a sub-list of the archive's closed list
    raw = yaml.safe_load(b)
    fw = raw["forward_confirmation"]
    assert (fw["cell"], fw["min_months_after_verdict"], fw["min_shadow_signals"]) == ("C1", 12, 100)


def test_the_forward_period_opens_only_after_12_months_and_100_shadow_signals(reg5):
    fw = yaml.safe_load(REG5.read_bytes())["forward_confirmation"]
    ledger = [{"timestamp": "20261105_210000", "kind": "run", "prereg_sha256": reg5.sha256},
              {"timestamp": "20261201_210000", "kind": "run", "prereg_sha256": reg5.sha256}]  # fmt: skip
    assert IT.forward_open_from(date(2026, 11, 5), 12, 65) == date(2028, 2, 14)
    with pytest.raises(ValueError, match="aucun verdict"):
        IT.check_forward(fw, reg5.sha256, [], date(2030, 1, 1), 500)
    with pytest.raises(ValueError, match="2028-02-14"):
        IT.check_forward(fw, reg5.sha256, ledger, date(2027, 11, 30), 500)  # the first run is the verdict
    with pytest.raises(ValueError, match="ombre"):
        IT.check_forward(fw, reg5.sha256, ledger, date(2028, 3, 1), 99)
    IT.check_forward(fw, reg5.sha256, ledger, date(2028, 3, 1), 100)


# --- 5. a small end-to-end synthetic run ---------------------------------------------------------------------------------


def test_registration_5_end_to_end_reports_coverage_and_abstains_below_a1(reg5, rules):
    n_days = 1700  # 2020-06-01 → ~2027-01
    row = {str(d): k for k, d in enumerate(synthetic_panel(n_inst=1, n_days=n_days, start="2020-06-01").days)}
    spec = []
    for k, t in enumerate(range(row["2021-11-02"], row["2026-07-01"], 25)):
        spec.append((t, (k % 4) * 2, "PDG", "acquisition"))  # Paris columns 0, 2, 4, 6
        if k % 3 == 0:
            spec.append((t + 2, ((k + 1) % 4) * 2, "directeur général", "cession"))
    spec += [(row["2021-06-01"], 0, "PDG", "acquisition"), (row["2026-11-10"], 2, "PDG", "acquisition")]  # outside
    p, rows = _panel_with(spec, n_days=n_days, seed=7, n_inst=8)
    rows.append(dataclasses.replace(rows[0], price=None))  # unknown amount: excluded, counted
    st = dataclasses.replace(settings(), insiders=InsiderData(tuple(rows), rules))
    inf = {**reg5.inference, "bootstrap_draws": 200, "permutation_draws": 50, "power_reps": 5}
    plan = dataclasses.replace(reg5, inference=inf)
    res = run_registration([p], plan, st, stages="0", f0=False)
    cells = res["cellules"]
    inside = sum(1 for t, _, _, nat in spec if row["2021-11-02"] <= t <= row["2026-07-01"] and nat == "acquisition")
    assert 0 < cells["C1"]["signaux"] <= inside and cells["C1"]["signaux"] == cells["C2"]["signaux"]
    assert cells["X2.t17.h20"]["signaux"] > 0 and cells["X1.t00.h20"]["signaux"] > 0
    assert cells["X3.t17.h20"]["signaux"] > 0
    assert not cells["C1"]["criteres"]["A1"] and cells["C1"]["niveau"] == "REJET"  # far below 300 events
    cov = IT.coverage(rows, rules, IT.CellRule.of(reg5.confirmatory[0].params))
    cov["placement"] = place(
        p, prepare_group(0, p, st).f.valid, st.insiders, IT.CellRule.of(reg5.confirmatory[0].params)
    ).counts
    assert cov["declarations"] == len(rows) and cov["entonnoir"]["montant_inconnu"] == 1
    assert cov["entonnoir"]["autre_nature"] == sum(1 for s in spec if s[3] == "cession")
    assert cov["placement"]["placees"] == cov["retenues_regle_c1"]  # every retained purchase lands on a session
    report = build_report(plan, res, {"limites": []})
    report, txt = IT.insider_report(report, res, plan.thresholds, cov)
    assert report["A_resume"]["titre"].startswith("NON CONCLUANT")
    assert report["A_resume"]["verdicts_familles"]["C1"].startswith("non concluant : échantillon sous A1")
    assert "couverture de l'archive" in txt and "par mois" in txt and "C2 : non concluant" in txt
    assert report["entete"]["declarations_dirigeants"]["retenues_regle_c1"] == cov["retenues_regle_c1"]
    assert_no_price_fields(report)  # counts only: no price, no ISIN
