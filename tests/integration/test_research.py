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


def test_research_commands_end_to_end(research_db, tmp_path):
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
    # costs never improve a trade: gross R ≥ net R on average in every reported cell
    for row in rep["G_couts_et_comptes"].values():
        if row.get("cout_moyen_r") is not None:
            assert row["cout_moyen_r"] >= 0
    # a calibration of another plan does not count: the next report is INVALIDE and freezes no candidate
    cal_path = next((tmp_path / "research").glob("calibration_*.json"))
    cal_path.write_text(json.dumps({**cal, "plan_sha256": "autre plan"}), encoding="utf-8")
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
