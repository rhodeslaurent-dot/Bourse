"""docs/31 — the research harness runs end to end on a seeded synthetic database: calibration against a stored
backtest, run-2 decomposition, a registration run (report + ledger), refusals (cell set, holdout, seed)."""

from __future__ import annotations

import csv
import json
import random
from datetime import UTC, date, datetime, timedelta

import pytest

from app.cli import main
from app.db.models import FxRate, Instrument, PriceEod
from app.db.session import db_session
from app.domain.research.report import assert_no_price_fields

PARAMS = "config/params.example.yaml"
PREREG = "research/prereg/2026-10-27_reg1.yaml"


def _series(isin: str, n: int, drift: float, seed: int, start: date = date(2016, 1, 4), dividend_at: int | None = None):
    rng = random.Random(seed)
    c, rows, d = 50.0, [], start
    closes = []
    for _ in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        o = c * (1 + rng.gauss(0, 0.004))
        c = c * (1 + drift + rng.gauss(0, 0.018))
        hi, lo = max(o, c) * (1 + abs(rng.gauss(0, 0.006))), min(o, c) * (1 - abs(rng.gauss(0, 0.006)))
        vol = int(200000 * (1 + abs(rng.gauss(0, 0.8))))
        closes.append((d, o, hi, lo, c, vol))
        d += timedelta(days=1)
    # a 2 % dividend on one session: the adjusted closes before it are scaled down (total return series)
    for k, (day, o, hi, lo, cl, vol) in enumerate(closes):
        f = 0.98 if dividend_at is not None and k < dividend_at else 1.0
        rows.append(PriceEod(isin=isin, day=day, open=o, high=hi, low=lo, close=cl, adj_close=cl * f, volume=vol,
                             source="eodhd", received_at=datetime.now(UTC), data_status="eod"))  # fmt: skip
    return rows


@pytest.fixture
def research_db(db_engine, config):
    with db_session() as s:
        venues = [("XPAR", "EUR", "FR"), ("XETR", "EUR", "DE"), ("XSTO", "SEK", "SE")]
        for k in range(24):
            mic, cur, cc = venues[k % 3]
            isin = f"{cc}00000000{k:02d}"
            s.add(Instrument(isin=isin, name=f"V{k}", mic=mic, currency=cur, in_universe=k < 18, market_cap_eur=2e9,
                             pea_eligible=True, kind="stock"))  # fmt: skip
            px = _series(isin, 760, 0.0010 if k % 2 == 0 else -0.0003, seed=k, dividend_at=400 if k == 0 else None)
            if cur == "SEK":  # quoted in SEK: prices ×10
                for r in px:
                    r.open, r.high, r.low, r.close, r.adj_close = (
                        x * 10 for x in (r.open, r.high, r.low, r.close, r.adj_close)
                    )
            s.add_all(px)
        for isin in ("FR0003500008", "EU0009658202"):
            s.add(Instrument(isin=isin, name=isin, mic="XPAR", currency="EUR", kind="index"))
            s.add_all(_series(isin, 760, 0.0004, seed=99))
        s.add(FxRate(pair="EURSEK", day=date(2017, 6, 1), rate=10.0, source="ecb"))
        s.flush()
        from app.services.backtest import run_backtest

        run = run_backtest(s, config, date(2017, 1, 2), date(2018, 9, 28), [f"FR00000000{k:02d}" for k in (0, 3, 6)])
        run_id = run.id
    return run_id


def test_research_commands_end_to_end(research_db, tmp_path, capsys):
    out = str(tmp_path / "research")
    run_id = research_db
    # calibration: the vectorised engine replays the stored backtest trade by trade
    assert (
        main(["--params", PARAMS, "research", "calibrate", "--run", str(run_id), "--prereg", PREREG, "--out", out]) == 0
    )
    cal = json.loads(next((tmp_path / "research").glob("calibration_*.json")).read_text())
    assert cal["verdict"] == "VALIDE" and cal["trades_localises"] == cal["trades_run2"]
    # decomposition of the stored run
    assert main(["--params", PARAMS, "research", "decompose", "--run", str(run_id), "--out", out]) == 0
    assert list((tmp_path / "research").glob("decomposition_run*.json"))
    # the registration run (small universe, fast)
    args = ["--params", PARAMS, "research", "run", "--prereg", PREREG, "--universe", "all", "--max-instruments", "100",
            "--to", "2018-12-31", "--out", out, "--max-f0-candidates", "300"]  # fmt: skip
    assert main(args) == 0
    reports = sorted(p for p in (tmp_path / "research").glob("2*.json") if "candidates" not in p.name)
    assert reports
    rep = json.loads(reports[-1].read_text())
    assert_no_price_fields(rep)  # statistics only
    h = rep["entete"]
    assert h["plan"] == "2026-10-27_reg1" and h["univers"] == "all" and h["instruments"]["P0"] > 0
    assert h["calibration"].startswith("VALIDE") and h["cellules_enregistrees"]["total"] == 195
    assert h["calibration_verdict"] == "VALIDE" and f"run {run_id}" in h["calibration"]
    # the SEK rate starts on 2017-06-01: the days before it use the plan's static rate (listed), never that rate
    assert h["jours_devise_taux_statique"]["SEK"]["du"] == "2016-01-04"
    assert h["jours_devise_taux_statique"]["SEK"]["au"] < "2017-06-01"
    assert rep["A_resume"]["titre"]
    for row in rep["I_classement"].values():
        assert row["niveau"] in ("CANDIDAT", "SURVEILLANCE", "REJET")
    assert "run2_decomposition" not in rep and rep["B_diagnostics_run2"]["decomposition"]
    txt = reports[-1].with_suffix(".txt").read_text()
    assert "RECHERCHE DE DÉCLENCHEURS" in txt and h["plan_sha256"] in txt
    with open(tmp_path / "research" / "ledger.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[-1]["kind"] == "run" and rows[-1]["prereg_sha256"] == h["plan_sha256"]
    assert rows[-1]["calibration"] == "VALIDE"
    # explain: the strongest cells with the criteria they miss, from the report alone (no data read, no ledger line)
    n_ledger = len(rows)
    assert main(["--params", PARAMS, "research", "explain", "--out", out, "--top", "3"]) == 0
    shown = capsys.readouterr().out
    assert shown.count("■ ") == 3 and "critères NON remplis" in shown and reports[-1].name in shown
    with open(tmp_path / "research" / "ledger.csv", encoding="utf-8") as fh:
        assert len(list(csv.DictReader(fh))) == n_ledger
    # costs never improve a trade: gross R ≥ net R on average in every reported cell
    for row in rep["G_couts_et_comptes"].values():
        if row.get("cout_moyen_r") is not None:
            assert row["cout_moyen_r"] >= 0
    # a calibration of another plan or of another reference backtest does not count: the run is refused before any
    # work (26/10/2026: a weekly backtest had become the latest run, the report came out INVALIDE after the full run)
    n_reports = len(list((tmp_path / "research").glob("2*.json")))
    cal_path = next((tmp_path / "research").glob("calibration_*.json"))
    cal_path.write_text(json.dumps({**cal, "plan_sha256": "autre plan"}), encoding="utf-8")
    assert main(args) == 2
    assert len(list((tmp_path / "research").glob("2*.json"))) == n_reports  # no report, no ledger line
    cal_path.write_text(json.dumps({**cal, "run": run_id + 1}), encoding="utf-8")
    assert main([*args, "--run", str(run_id)]) == 2  # calibrated on another backtest than the one named
    # a calibration made before the end of the data still produces a report, INVALIDE, with no candidate frozen
    cal_path.write_text(json.dumps({**cal, "fin_donnees": "2017-01-02"}), encoding="utf-8")
    assert main(args) == 0
    last = sorted(p for p in (tmp_path / "research").glob("2*.json") if "candidates" not in p.name)[-1]
    rep2 = json.loads(last.read_text())
    assert rep2["entete"]["calibration_verdict"] == "INVALIDE" and rep2["A_resume"]["titre"].startswith("INVALIDE")
    assert not list((tmp_path / "research").glob(f"{last.stem}_candidates.yaml"))
    with open(tmp_path / "research" / "ledger.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[-1]["calibration"] == "INVALIDE" and rows[-1]["candidates_sha256"] == ""
    # same plan, another cell set in the ledger → refused
    rows[-1]["cell_set_sha256"] = "autre"
    with open(tmp_path / "research" / "ledger.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    assert main(args) == 2
    # the sealed period cannot be opened without a frozen candidate file, nor with another seed
    assert main(["--params", PARAMS, "research", "unseal", "--prereg", PREREG, "--out", out]) == 2
    assert main([*args, "--seed", "1"]) == 2


def test_registration_2_draft_runs_on_one_venue_groups(research_db, tmp_path, capsys):
    """docs/33 §33.3: the draft of registration n° 2 — groups US / LSE / SW, each judged on its own index, CTO
    only, the placebo first. Runs end to end on a synthetic base; the calibration still replays run 2 on the
    P0 / P1 / P2 groups (the reference backtest is European)."""
    plan = "research/prereg/2026-10-28_reg2.yaml"
    out = str(tmp_path / "research")
    with db_session() as s:
        for k, (mic, cur, cc) in enumerate([("XNYS", "USD", "US"), ("XLON", "GBP", "GB"), ("XSWX", "CHF", "CH")] * 4):
            isin = f"{cc}10000000{k:02d}"
            s.add(Instrument(isin=isin, name=f"W{k}", mic=mic, currency=cur, in_universe=False, active=k % 4 != 0,
                             market_cap_eur=5e9, kind="stock"))  # fmt: skip
            s.add_all(_series(isin, 760, 0.0008 if k % 2 else -0.0002, seed=100 + k))
        for idx in ("IDX.GSPC", "IDX.FTSE", "IDX.SSMI"):
            s.add(Instrument(isin=idx, name=idx, mic="XNYS", currency="USD", kind="index"))
            s.add_all(_series(idx, 760, 0.0004, seed=7))
        for pair, rate in (("EURUSD", 1.1), ("EURGBP", 0.85), ("EURCHF", 1.08)):
            s.add(FxRate(pair=pair, day=date(2016, 1, 4), rate=rate, source="ecb"))
    assert main(["--params", PARAMS, "research", "calibrate", "--run", str(research_db), "--prereg", plan,
                 "--out", out]) == 0  # fmt: skip
    args = ["--params", PARAMS, "research", "run", "--prereg", plan, "--universe", "all", "--to", "2018-12-31",
            "--out", out, "--run", str(research_db)]  # fmt: skip
    assert main(args) == 0
    rep = json.loads(sorted(p for p in (tmp_path / "research").glob("2*.json") if "candidates" not in p.name)[-1]
                     .read_text())  # fmt: skip
    h = rep["entete"]
    assert set(h["instruments"]) <= {"US", "LSE", "SW"} and sum(h["instruments"].values()) > 0
    assert h["calibration_verdict"] == "VALIDE" and h["cellules_enregistrees"]["confirmatoires"] == 2
    assert set(rep["A_resume"]["confirmatoires"]) == {"C1", "C2"}
    assert any(c.startswith("X1.t00") for c in rep["I_classement"])  # the placebo is in the report


def test_one_venue_groups_use_their_own_index(config, calendars):
    from app.services.research import mic_groups, own_index_id

    vg = {"US": ("US",), "LSE": ("LSE",), "SW": ("SW",)}
    assert mic_groups(config, calendars, vg) == {"XNYS": "US", "XLON": "LSE", "XSWX": "SW"}
    assert mic_groups(config, calendars)["XPAR"] == "P0"  # registration n° 1 groups unchanged
    assert [own_index_id(config, calendars, vg[g]) for g in vg] == ["IDX.GSPC", "IDX.FTSE", "IDX.SSMI"]
    assert own_index_id(config, calendars, ("PA", "AS")) is None  # a multi-venue group: CAC 40 / STOXX 600 rule


def test_registration_3_runs_with_the_earnings_history_and_guards_its_old_replication(research_db, tmp_path):
    """docs/34 §34.5: registration n° 3 on the European groups with the stored announcements (earnings_events):
    coverage per group in the header, the t15 date assumption in the limits; a run whose data would start in
    the sealed old replication period is refused, and so is an unseal without a frozen candidate file."""
    from app.db.models import EarningsEvent

    plan = "research/prereg/2026-10-28_reg3.yaml"
    out = str(tmp_path / "research")
    with db_session() as s:
        now = datetime.now(UTC)
        for k in range(20):  # values 20–23 without any history: unknown, never « no announcement »
            cc = ("FR", "DE", "SE")[k % 3]
            d = date(2016, 2, 3) + timedelta(days=k)
            while d < date(2018, 11, 1):
                s.add(EarningsEvent(isin=f"{cc}00000000{k:02d}", day=d, when=("bmo", "amc", "unknown")[d.month % 3],
                                    eps_actual=1.1, eps_estimate=1.0, source="yahoo", received_at=now))  # fmt: skip
                d += timedelta(days=91)
    assert main(["--params", PARAMS, "research", "calibrate", "--run", str(research_db), "--prereg", plan,
                 "--out", out]) == 0  # fmt: skip
    args = ["--params", PARAMS, "research", "run", "--prereg", plan, "--universe", "all", "--to", "2018-12-31",
            "--out", out, "--run", str(research_db)]  # fmt: skip
    assert main([*args, "--from", "2015-09-30"]) == 2  # a bar of the sealed replication: refused before any read
    assert not list((tmp_path / "research").glob("2*.json"))
    assert main(args) == 0
    rep = json.loads(sorted(p for p in (tmp_path / "research").glob("2*.json") if "candidates" not in p.name)[-1]
                     .read_text())  # fmt: skip
    assert_no_price_fields(rep)
    h = rep["entete"]
    assert h["plan"] == "2026-10-28_reg3" and h["cellules_enregistrees"]["total"] == 10
    assert set(rep["A_resume"]["confirmatoires"]) == {"C1", "C2"}
    cov = h["couverture_resultats"]
    assert cov and set(cov) <= {"P0", "P1", "P2"}
    assert all(0 <= v["part_observations_couvertes"] <= 1 for v in cov.values())
    assert sum(v["publications_placees"] for v in h["publications_resultats"].values()) > 0
    assert any("HYPOTHÈSE t15" in x for x in h["limites"])
    assert h["hypotheses"]["earnings_date_known_sessions_ahead"]["hypothese"] is True
    txt = sorted((tmp_path / "research").glob("2*.txt"))[-1].read_text()
    assert "couverture des publications" in txt and "Holm sur 2" in txt
    # the old replication opens only by unseal, with a frozen candidate file, its warm-up and its whole span
    assert main(["--params", PARAMS, "research", "unseal", "--prereg", plan, "--out", out]) == 2


def test_registration_4_runs_with_the_letters_and_guards_its_old_replication(research_db, tmp_path):
    """docs/34 §34.11: registration n° 4 (entry policies paired per signal) through the CLI on the synthetic base,
    with stored letters carrying an explicit trigger (X6): calibration, run, report with the entry-policy section,
    the letters counted in the header, aggregates only; a run starting in the sealed replication is refused."""
    from app.db.models import NewsletterItem, NewsletterValue

    plan = "research/prereg/2026-10-30_reg4.yaml"
    out = str(tmp_path / "research")
    with db_session() as s:
        for k, (isin, day) in enumerate([("FR0000000000", date(2017, 6, 1)), ("FR0000000003", date(2017, 9, 4)),
                                         ("FR0000000006", date(2018, 2, 1))]):  # fmt: skip
            px = s.query(PriceEod).filter(PriceEod.isin == isin, PriceEod.day >= day).order_by(PriceEod.day).first()
            item = NewsletterItem(source="momentum_capital", message_id=f"m{k}", dedup_hash=f"h{k}",
                                  received_at=datetime(day.year, day.month, day.day, 13, 0, tzinfo=UTC),
                                  subject="lettre", body_text="—")  # fmt: skip
            s.add(item)
            s.flush()
            c = px.close
            levels = {"Déclencheur": c * 1.02, "Objectif 1": c * 1.15, "invalidation": c * 0.9}
            s.add(NewsletterValue(item_id=item.id, isin=isin, direction="buy", levels=levels))
        s.add(NewsletterValue(item_id=item.id, isin="FR0000000009", direction="buy", levels={"achat": 10.0}))
    assert main(["--params", PARAMS, "research", "calibrate", "--run", str(research_db), "--prereg", plan,
                 "--out", out]) == 0  # fmt: skip
    args = ["--params", PARAMS, "research", "run", "--prereg", plan, "--universe", "all", "--to", "2018-12-31",
            "--out", out, "--run", str(research_db)]  # fmt: skip
    assert main([*args, "--from", "2015-09-30"]) == 2  # a bar of the sealed replication: refused before any read
    assert main(args) == 0
    rep = json.loads(sorted(p for p in (tmp_path / "research").glob("2*.json") if "candidates" not in p.name)[-1]
                     .read_text())  # fmt: skip
    assert_no_price_fields(rep)
    h = rep["entete"]
    assert h["plan"] == "2026-10-30_reg4" and h["cellules_enregistrees"]["total"] == 27
    assert sum(v["avec_declencheur_stop_objectif"] for v in h["lettres"].values()) == 3
    assert sum(v["valeurs_lues"] for v in h["lettres"].values()) == 4
    assert any("politiques d'entrée" in x for x in h["limites"])
    pe = rep["P_politiques_entree"]
    assert pe["ordre_de_lecture"][0].startswith("X1.") and set(rep["A_resume"]["confirmatoires"]) == {"C1", "C2"}
    txt = sorted((tmp_path / "research").glob("2*.txt"))[-1].read_text()
    assert "P — Politiques d'entrée" in txt and "Holm sur 2" in txt
    assert main(["--params", PARAMS, "research", "unseal", "--prereg", plan, "--out", out]) == 2
