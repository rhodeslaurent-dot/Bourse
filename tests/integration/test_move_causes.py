# ruff: noqa: E501
"""docs/36 — the causes study end to end on a small synthetic base: rises, falls and stratified controls under the
same rules, every source placed on the values' own sessions (earnings, AMF purchases, letters, stored news), the
attribution adds up, the event study measures from the next open (earnings and AMF purchases masked until their
registration has run), the same report twice with the same seed, aggregates only, the CLI refuses the history kept
for confirmation."""

from __future__ import annotations

import json
import math
import random
from datetime import UTC, date, datetime, timedelta

import pytest

from app.db.models import (
    EarningsEvent,
    InsiderDeclaration,
    Instrument,
    NewsItem,
    NewsletterItem,
    NewsletterValue,
    PriceEod,
)
from app.db.session import db_session
from app.domain import move_causes as mc
from app.services import move_causes

T0 = date(2023, 1, 2)
NOW = datetime(2026, 10, 30, tzinfo=UTC)
NAMES = ["Arvelis", "Bormont", "Caltrix", "Dravenor", "Elsimar", "Fortavia", "Galmeris", "Hexoria", "Ilvarion",
         "Jorvalis", "Kestrane", "Lumivor"]  # fmt: skip


def _days(n: int) -> list[date]:
    out, d = [], T0
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _series(rng: random.Random, n: int) -> list[tuple[float, float, float, float, int, float]]:
    """Random walk with quiet bases, rises and pullbacks (as the docs/34 test), opening gaps and a dividend."""
    px, out = 50.0, []
    k = 0
    factor = 0.95  # adj_close / close before the dividend detachment
    while len(out) < n:
        phase = k % 3
        for _ in range(rng.randint(15, 30)):
            drift = {0: 0.0, 1: 0.012, 2: -0.006}[phase]
            r = drift + rng.gauss(0, 0.006 if phase == 0 else 0.012)
            o = px * (1 + rng.gauss(0, 0.003) + (0.05 if rng.random() < 0.02 else 0.0))
            px = max(1.0, px * math.exp(r))
            hi, lo = max(o, px) * (1 + abs(rng.gauss(0, 0.004))), min(o, px) * (1 - abs(rng.gauss(0, 0.004)))
            vol = int(200_000 * (2.5 if phase == 1 and rng.random() < 0.3 else 1.0) * (0.7 + rng.random()))
            if len(out) == 450:
                factor = 1.0  # ex-dividend: the adjustment factor changes by ≈ 5 %
            out.append((o, hi, lo, px, vol, factor))
        k += 1
    return out[:n]


def _seed(n_values: int = 12, n_days: int = 700) -> tuple[list[date], dict[str, list[float]]]:
    rng = random.Random(3)
    days = _days(n_days)
    closes: dict[str, list[float]] = {}
    with db_session() as s:
        for v, name in enumerate(NAMES[:n_values]):
            isin = f"FR00000002{v:02d}"
            s.add(Instrument(isin=isin, name=f"{name} SA", mic="XPAR", currency="EUR", in_universe=True,
                             sector="Industrie" if v % 2 else "Santé", market_cap_eur=5e8 if v < 6 else 3e9,
                             adv_eur_20=2e7, pea_eligible=True, srd_status="complet"))  # fmt: skip
            closes[isin] = []
            for d, (o, h, lo, c, vol, f) in zip(days, _series(rng, n_days), strict=True):
                s.add(PriceEod(isin=isin, day=d, open=o, high=h, low=lo, close=c, adj_close=c * f, volume=vol,
                               source="eodhd", received_at=NOW, data_status="eod"))  # fmt: skip
                closes[isin].append(c)
    return days, closes


def _sources(days: list[date], closes: dict[str, list[float]]) -> None:
    isins = sorted(closes)
    with db_session() as s:
        for n, isin in enumerate(isins):
            for k in range(40, len(days), 63):  # quarterly announcements, alternately before the open / after the close
                s.add(EarningsEvent(isin=isin, day=days[k], when="bmo" if (k // 63) % 2 else "amc", source="yahoo",
                                    received_at=NOW))  # fmt: skip
            for m, k in enumerate(range(320 + n, len(days) - 30, 37)):  # AMF purchases, published at 18:00 Paris
                ts = datetime.combine(days[k], datetime.min.time(), UTC) + timedelta(hours=16)
                s.add(InsiderDeclaration(
                    source="amf_bdif", doc_id=f"2024DD{n:02d}{m:04d}", market_timestamp=ts, isin=isin, isin_source="declaration",
                    role="PDG", nature="acquisition", price=closes[isin][k], currency="EUR", volume=1000.0,
                    transactions=[{"instrument": "Action", "venue": "Euronext Paris", "nature": "acquisition"}],
                    parse_status="parsed", received_at=NOW, processed_at=NOW,
                ))  # fmt: skip
            for m, k in enumerate(range(330 + 2 * n, len(days) - 30, 29)):  # letters before the open (07:00 Paris)
                it = NewsletterItem(source="momentum_capital", message_id=f"m{n}-{m}", dedup_hash=f"h{n}-{m}",
                                    subject=f"Momentum : {NAMES[n]}", body_text=f"Achat de {NAMES[n]} SA.",
                                    received_at=datetime.combine(days[k], datetime.min.time(), UTC) + timedelta(hours=6))  # fmt: skip
                s.add(it)
                s.flush()
                s.add(NewsletterValue(item_id=it.id, isin=isin, name=f"{NAMES[n]} SA", direction="buy"))
            for m, k in enumerate(range(600 + n, len(days) - 5, 11)):  # stored news: only the recent span
                s.add(NewsItem(source="rss", guid=f"g{n}-{m}", dedup_hash=f"n{n}-{m}", title=f"{NAMES[n]} annonce",
                               isins=[isin], fetched_at=datetime.combine(days[k], datetime.min.time(), UTC),
                               published_at=datetime.combine(days[k], datetime.min.time(), UTC) + timedelta(hours=12)))  # fmt: skip


def _run(config, calendars, days: list[date], ledger=()):
    with db_session() as s:
        return move_causes.run_study(s, config, calendars, days[300], days[-30], rises=60, falls=60, controls=60,
                                     seed=1, setup_values=2, ledger=ledger, today=days[-1] + timedelta(days=1))  # fmt: skip


@pytest.fixture
def study(db_engine, example_raw, calendars):
    from app.config import validate_params

    config = validate_params(example_raw)  # the production policy: d3/d4 disabled, d2/d7 watch-only
    days, closes = _seed()
    _sources(days, closes)
    return config, calendars, days


def test_the_study_attributes_rises_falls_and_controls_under_the_same_rules(study):
    config, calendars, days = study
    rep = _run(config, calendars, days)
    m = rep["meta"]
    assert m["groupes"] == ["P0"] and m["valeurs"] == 12
    assert 0 < m["hausses_etudiees"] <= 60 and 0 < m["baisses_etudiees"] <= 60
    assert m["controles_tires"] + m["controles_manquants"] == 60 and m["controles_tires"] > 0
    att = rep["attribution"]
    assert att["unites"] == {
        "hausses": m["hausses_etudiees"],
        "baisses": m["baisses_etudiees"],
        "controles": m["controles_tires"],
    }
    for g in mc.GROUPS:  # one primary cause per unit
        assert sum(x["k"] for x in att["cause_principale"][g].values() if "k" in x) == att["unites"][g]
    # market and sector explain a move: sans objet for a control
    assert "sans_objet" in att["causes"][mc.MARKET]["controles"]
    # sources placed: earnings (covered: quarterly history), letters, AMF purchases (French values in Paris)
    for cause in (mc.EARNINGS, mc.LETTER, mc.INSIDER):
        assert att["causes"][cause]["hausses"]["n"] > 0, cause
    cov = rep["couverture"]
    assert cov[mc.INSIDER] and all(v["n"] > 0 for v in cov[mc.INSIDER].values())
    # news only covers its own recent span: most units are « unknown », never « absent »
    assert att["causes"][mc.NEWS]["hausses"]["n"] < att["unites"]["hausses"]
    assert m["sources"]["dirigeants"]["retenues_dans_le_perimetre"] > 0
    # the event study: every gap, volume spike, letter and detector setup bought at the next open
    ev = rep["evenements"]
    assert ev[mc.EV_LETTER]["etudiees"] > 0 and "barriere" in ev[mc.EV_LETTER]
    assert ev[mc.EV_RVOL]["occurrences"] >= ev[mc.EV_RVOL]["apres_delai"] > 0
    # earnings and AMF purchases: masked until registrations n° 3 and n° 5 have run
    for cause in (mc.EV_EARNINGS, mc.EV_INSIDER):
        assert "masque_registre" in ev[cause] and "5" not in ev[cause] and ev[cause]["occurrences"] > 0
    assert rep["tests_multiples"]["seuil_t"] == 3.0
    text = move_causes.render_md(rep)
    for title in (
        "# Causes des hausses",
        "## Couverture des sources",
        "## A. Attribution",
        "## B. Étude d'événements",
        "Non couvert",
    ):
        assert title in text
    assert "Harvey" in text and "aucun ISIN" in text


def test_registered_rows_open_after_the_plan_ran_and_the_report_is_reproducible(study, tmp_path):
    from pathlib import Path

    from app.domain.research.prereg import file_sha256

    config, calendars, days = study
    ledger = [
        {"kind": "run", "prereg_sha256": file_sha256(Path(p).read_bytes())}
        for p in (move_causes.REG3, move_causes.REG5)
    ]
    a = _run(config, calendars, days, ledger)
    b = _run(config, calendars, days, ledger)
    assert json.dumps(a, default=str, sort_keys=True) == json.dumps(b, default=str, sort_keys=True)  # same seed
    ev = a["evenements"]
    assert "masque_registre" not in ev[mc.EV_EARNINGS] and ev[mc.EV_EARNINGS]["etudiees"] > 0
    assert "5" in ev[mc.EV_INSIDER]
    j, md = move_causes.write(a, tmp_path)  # refuses any price, name or ISIN
    assert j.exists() and md.read_text(encoding="utf-8").startswith("# Causes des hausses")
    assert "FR0000000" not in j.read_text(encoding="utf-8")


def test_the_cli_keeps_the_old_european_history_for_confirmation(capsys):
    import argparse

    from app.cli import cmd_moves_causes

    args = argparse.Namespace(params="config/params.example.yaml", calendars="config/market_calendars.yaml",
                              from_date="2016-06-01", to=None, out="data/research")  # fmt: skip
    assert cmd_moves_causes(args) == 2
    assert "réservées à la confirmation" in capsys.readouterr().out


def test_an_empty_base_gives_an_empty_report(db_engine, example_raw, calendars):
    from app.config import validate_params

    with db_session() as s:
        rep = move_causes.run_study(s, validate_params(example_raw), calendars, date(2017, 1, 2), date(2017, 6, 30),
                                    today=date(2026, 10, 30))  # fmt: skip
    assert rep["meta"]["valeurs"] == 0 and rep["attribution"]["unites"] == {g: 0 for g in mc.GROUPS}
    assert "aucun ISIN" in move_causes.render_md(rep)
    with pytest.raises(ValueError):
        with db_session() as s:
            move_causes.run_study(s, validate_params(example_raw), calendars, date(2016, 6, 1), date(2017, 6, 30))
