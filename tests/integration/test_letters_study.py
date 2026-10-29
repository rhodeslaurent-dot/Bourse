# ruff: noqa: E501
"""docs/20 §20.11 — the letters study end to end on a small synthetic base: recommendations placed on their reaction
session, a value read in a word (its name is not in the letter) left out, 20 seeded controls per recommendation,
the same report twice with the same seed, aggregates only, and the CLI refusing the history kept for confirmation."""

from __future__ import annotations

import math
import random
from datetime import UTC, date, datetime, timedelta

from app.db.models import Instrument, NewsletterItem, NewsletterValue, PriceEod
from app.db.session import db_session
from app.services import letters_study

T0 = date(2024, 6, 3)
NOW = datetime(2026, 10, 29, tzinfo=UTC)
NAMES = ["Arvelis", "Bormont", "Caltrix", "Dravenor", "Elsimar", "Fortavia", "Galmeris", "Hexoria", "Ilvarion",
         "Jorvalis", "Kestrane", "Lumivor", "Mardelis", "Nortavia", "Orvalis", "Pelmora", "Quivaris", "Rostavel",
         "Sylmaron", "Tarvelis", "Ulmeris", "Varnoria", "Wesmara", "Xelvoris", "Yrdaval", "Zorvane"]  # fmt: skip


def _days(n: int) -> list[date]:
    out, d = [], T0
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _seed(n_days: int = 600) -> tuple[list[date], dict[str, list[float]]]:
    rng = random.Random(5)
    days = _days(n_days)
    closes: dict[str, list[float]] = {}
    with db_session() as s:
        for v, name in enumerate(NAMES):
            isin = f"FR00000001{v:02d}"
            s.add(Instrument(isin=isin, name=f"{name} SA", mic="XPAR", currency="EUR", in_universe=True,
                             market_cap_eur=2e9, adv_eur_20=2e7, pea_eligible=True))  # fmt: skip
            px, sig = 40.0 + v, 0.008 + 0.0004 * v
            closes[isin] = []
            for d in days:
                o = px * (1 + rng.gauss(0, 0.003))
                px = max(1.0, px * math.exp(rng.gauss(0.0004, sig)))
                hi, lo = max(o, px) * (1 + abs(rng.gauss(0, 0.004))), min(o, px) * (1 - abs(rng.gauss(0, 0.004)))
                s.add(PriceEod(isin=isin, day=d, open=o, high=hi, low=lo, close=px, adj_close=px,
                               volume=int(150_000 * (0.7 + rng.random())), source="eodhd", received_at=NOW))  # fmt: skip
                closes[isin].append(px)
    return days, closes


def _letters(days: list[date], closes: dict[str, list[float]]) -> tuple[date, date]:
    a, b = "FR0000000100", "FR0000000101"  # Arvelis (written), Bormont (not written: an old word match)
    k1, k2 = 420, 450
    c1 = closes[a][k1 - 1]
    with db_session() as s:
        it1 = NewsletterItem(
            source="momentum_capital", message_id="m1", dedup_hash="h1", subject="Momentum : Arvelis",
            received_at=datetime.combine(days[k1], datetime.min.time(), UTC) + timedelta(hours=6),  # before the open (07:00 Paris)
            body_text=f"Achat Arvelis sur franchissement de {c1 * 1.01:.2f}, stop {c1 * 0.94:.2f}, objectif {c1 * 1.12:.2f}. "
            "Le marché reste sur sa lancée, rien de plus.",
        )  # fmt: skip
        it2 = NewsletterItem(
            source="zonebourse", message_id="m2", dedup_hash="h2", subject="Zonebourse Momentum",
            received_at=datetime.combine(days[k2], datetime.min.time(), UTC) + timedelta(hours=10),  # during the session (11:00 Paris)
            body_text="Caltrix à surveiller : la tendance se confirme.",
        )  # fmt: skip
        s.add_all([it1, it2])
        s.flush()
        s.add(NewsletterValue(item_id=it1.id, isin=a, name="Arvelis", direction="buy",
                              levels={"déclencheur": round(c1 * 1.01, 2), "invalidation": round(c1 * 0.94, 2),
                                      "objectif 1": round(c1 * 1.12, 2)}))  # fmt: skip
        s.add(NewsletterValue(item_id=it1.id, isin=b, name="sur", direction="buy"))  # read in a word
        s.add(NewsletterValue(item_id=it1.id, isin=None, name="CAC 40", direction="neutral"))
        s.add(NewsletterValue(item_id=it2.id, isin="FR0000000102", name="Caltrix", direction="watch"))
    return days[k1], days[k2 + 1]


def test_the_study_measures_each_written_recommendation_against_seeded_controls(db_engine, config, calendars, tmp_path):
    days, closes = _seed()
    before_open, intraday_next = _letters(days, closes)
    with db_session() as s:
        rows, meta = letters_study.build_rows(s, config, calendars, date(2026, 1, 2), days[-1], today=days[-1])
    assert meta["exclusions"]["nom_absent_du_texte"] == 1 and meta["mentions_neutres_ou_ventes"] == 1
    assert sorted((r.source, r.day) for r in rows) == [("momentum_capital", before_open), ("zonebourse", intraday_next)]
    rec = next(r for r in rows if r.source == "momentum_capital")
    assert rec.levels == "recalculables" and rec.direction == "buy"
    assert rec.arms["E2_X1"].status != "non_applicable" and rec.arms["E3_X1"].status == "non_applicable"
    assert all(len(r.ctrl_fwd["ouverture"][20]) == 20 for r in rows)  # 20 controls, all with a 20-session return
    assert meta["controles"]["tires"] == 40 and meta["references"].get("equipondere")
    assert rec.fwd["ouverture"][1] is not None and rec.fwd["cloture"][1] is not None
    with db_session() as s:
        rep = letters_study.run_study(s, config, calendars, date(2026, 1, 2), days[-1], today=days[-1])
        again = letters_study.run_study(s, config, calendars, date(2026, 1, 2), days[-1], today=days[-1])
    assert rep == again  # seeded: the same controls, the same report
    assert rep["ensemble"]["n"] == 2 and set(rep["decoupages"]["source"]) == {"momentum_capital", "zonebourse"}
    j, t = letters_study.write(rep, tmp_path)  # refuses any price, name or ISIN
    text = t.read_text(encoding="utf-8")
    assert "Échantillon insuffisant" in text and "## 3. Règles d'entrée et de sortie" in text
    assert "FR00000001" not in text and "Arvelis" not in text and j.name.startswith("letters_study_")
    with db_session() as s:
        only = letters_study.build_rows(
            s, config, calendars, date(2026, 1, 2), days[-1], ["zonebourse"], today=days[-1]
        )
    assert [r.source for r in only[0]] == ["zonebourse"]


def test_the_cli_keeps_the_old_european_history_for_confirmation(capsys):
    import argparse

    from app.cli import cmd_letters_study

    args = argparse.Namespace(params="config/params.example.yaml", calendars="config/market_calendars.yaml",
                              from_date="2016-06-01", to=None, source=None, seed=1, controls=20, out="data/research")  # fmt: skip
    assert cmd_letters_study(args) == 2
    assert "réservées à la confirmation" in capsys.readouterr().out
