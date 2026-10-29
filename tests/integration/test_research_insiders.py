"""docs/34 §34.12 — registration n° 5 (managers' dealings, AMF BDIF) end to end on a synthetic Paris base: the
archived declarations feed t17 through ``research run`` (coverage in the header and the .txt, the A1 abstention in
the title, ledger row with the plan sha256), the forward period stays closed, and the shadow series records C1."""

from __future__ import annotations

import csv
import json
from datetime import UTC, date, datetime, timedelta

import pytest

from app.cli import main
from app.db.models import BacktestRun, InsiderDeclaration, Instrument, PriceEod, ShadowSignal
from app.db.session import db_session
from app.domain.research.prereg import PreregError
from app.domain.research.report import assert_no_price_fields
from tests.integration.test_research import _series

PARAMS = "config/params.example.yaml"
PLAN = "research/prereg/2026-10-30_reg5.yaml"
TX = {"instrument": "Action", "nature": "acquisition", "nature_detail": "Acquisition", "venue": "Euronext Paris"}


def _decl(k: int, isin: str, ts: datetime, price: float | None, **kw) -> InsiderDeclaration:
    now = datetime.now(UTC)
    base = dict(source="amf_bdif", doc_id=f"2022DD{k:07d}", market_timestamp=ts, isin=isin, isin_source="declaration",
                role="PDG", nature="acquisition", nature_detail="Acquisition", venue="Euronext Paris", price=price,
                volume=2000.0, currency="EUR", transactions=[TX], parse_status="parsed", received_at=now,
                processed_at=now)  # fmt: skip
    base.update(kw)
    return InsiderDeclaration(**base)


@pytest.fixture
def insider_db(db_engine, config):
    closes: dict[tuple[str, date], float] = {}
    with db_session() as s:
        isins = [f"FR00000001{k:02d}" for k in range(10)]
        for k, isin in enumerate(isins):
            s.add(Instrument(isin=isin, name=f"P{k}", mic="XPAR", currency="EUR", in_universe=True, market_cap_eur=2e9,
                             pea_eligible=True, kind="stock"))  # fmt: skip
            rows = _series(isin, 660, 0.0004 if k % 2 else -0.0002, seed=40 + k, start=date(2020, 6, 1))
            closes.update({(isin, r.day): r.close for r in rows})
            s.add_all(rows)
        for isin in ("FR0003500008", "EU0009658202"):
            s.add(Instrument(isin=isin, name=isin, mic="XPAR", currency="EUR", kind="index"))
            s.add_all(_series(isin, 660, 0.0003, seed=98, start=date(2020, 6, 1)))
        days = sorted({d for (_, d) in closes})
        n = 0
        for j, d in enumerate(d for d in days if date(2021, 11, 2) <= d <= date(2022, 9, 1)):
            if j % 4:
                continue
            isin = isins[(j // 4) % 10]
            ts = datetime(d.year, d.month, d.day, 9, 30, tzinfo=UTC)
            n += 1
            s.add(_decl(n, isin, ts, closes[(isin, d)]))
            if j % 12 == 0:  # an executive sale, a declaration without figures, one not read
                n += 3
                seller = isins[(j // 4 + 5) % 10]
                s.add(_decl(n - 2, seller, ts, closes[(seller, d)], nature="cession",
                            transactions=[{**TX, "nature": "cession", "nature_detail": "Cession"}]))  # fmt: skip
                s.add(_decl(n - 1, isin, ts + timedelta(hours=1), None))
                s.add(_decl(n, isin, ts, None, parse_status="listing_only", role=None, nature=None, transactions=None))
        last = days[-1]
        n += 1
        s.add(_decl(n, isins[3], datetime(last.year, last.month, last.day, 9, 0, tzinfo=UTC), closes[(isins[3], last)]))
        run = BacktestRun(created_at=datetime.now(UTC), params_version="test", start=date(2021, 1, 4),
                          end=date(2022, 6, 30), instruments=0)  # fmt: skip
        s.add(run)
        s.flush()
        return run.id, last


def test_registration_5_runs_on_the_archived_declarations_and_abstains_below_a1(insider_db, tmp_path, config,
                                                                                calendars):  # fmt: skip
    run_id, last = insider_db
    out = tmp_path / "research"
    out.mkdir()
    from app.services.research import load_prereg

    prereg = load_prereg(PLAN)
    # the calibration replays run 2 on P0 / P1 / P2 (untouched by this plan): a VALIDE file of this plan stands for it
    (out / "calibration_20261030_000000.json").write_text(json.dumps(
        {"verdict": "VALIDE", "concordance": 1.0, "run": run_id, "plan_sha256": prereg.sha256,
         "fin_donnees": str(last)}), encoding="utf-8")  # fmt: skip
    args = ["--params", PARAMS, "research", "run", "--prereg", PLAN, "--universe", "all", "--from", "2020-06-01",
            "--to", str(last), "--out", str(out), "--run", str(run_id)]  # fmt: skip
    assert main(args) == 0
    rep = json.loads(sorted(p for p in out.glob("2*.json") if "candidates" not in p.name)[-1].read_text())
    assert_no_price_fields(rep)
    h = rep["entete"]
    assert h["plan"] == "2026-10-30_reg5" and h["plan_sha256"] == prereg.sha256 and h["instruments"] == {"PA": 10}
    cov = h["declarations_dirigeants"]
    assert cov["declarations"] > 0 and 0 < cov["taux_analyse"] < 1 and "2022-03" in cov["par_mois"]
    assert cov["entonnoir"]["montant_inconnu"] > 0 and cov["entonnoir"]["non_analysee"] > 0
    assert cov["placement"]["placees"] == cov["retenues_regle_c1"]
    a = rep["A_resume"]
    assert a["titre"].startswith("NON CONCLUANT") and a["verdicts_familles"]["C1"].startswith("non concluant")
    cells = {r["id"]: r for fam in rep["C_etape0_par_famille"].values() for r in fam}
    assert set(a["confirmatoires"]) == {"C1", "C2"} and "X2.t17.h20" in cells
    assert a["confirmatoires"]["C1"]["n"] >= 20 and cells["X2.t17.h20"]["n"] > 0  # purchases and sales placed
    txt = sorted(out.glob("2*.txt"))[-1].read_text(encoding="utf-8")
    assert "couverture de l'archive" in txt and "NON CONCLUANT" in txt and "Holm sur 2" in txt
    with (out / "ledger.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[-1]["kind"] == "run" and rows[-1]["prereg_sha256"] == prereg.sha256
    # the forward period: no unseal without a frozen candidate file, nor before 12 months after the verdict
    assert main(["--params", PARAMS, "research", "unseal", "--prereg", PLAN, "--out", str(out)]) == 2
    from app.services.insider_research import check_forward_unseal

    with db_session() as s, pytest.raises(PreregError, match="12 mois"):
        check_forward_unseal(s, PLAN, prereg, rows, date(2027, 6, 1))
    # the shadow series: C1 on the last session (a purchase published that morning), once
    from app.services.shadow import PLAN_REG5, record_shadow_signals, shadow_cells

    assert shadow_cells(PLAN_REG5) == ("C1",)
    with db_session() as s:
        got = record_shadow_signals(s, config, calendars, last, plan=PLAN_REG5)
        assert got == {"C1": 1}
        sig = s.query(ShadowSignal).one()
        assert (sig.plan_id, sig.cell, sig.day, sig.isin, sig.venue_group) == ("2026-10-30_reg5", "C1", last,
                                                                               "FR0000000103", "P0")  # fmt: skip
        assert record_shadow_signals(s, config, calendars, last, plan=PLAN_REG5) == {"C1": 0}
        assert s.query(PriceEod).count() > 0
