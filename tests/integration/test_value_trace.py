"""18/11/2026 — « why are NVIDIA and Take-Two not proposed, nor in the watchlist? »: one value through each gate."""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta

from app import cli
from app.db.models import Instrument, NewsItem, PriceEod
from app.db.session import db_session
from app.services.value_trace import find, trace

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)


def _seed(s) -> None:
    s.add(Instrument(isin="US8740541094", name="Take-Two Interactive Software, Inc.", ticker_eodhd="TTWO.US",
                     mic="XNAS", currency="USD", in_universe=False,
                     universe_reasons=["capitalisation inconnue"]))  # fmt: skip
    s.add(Instrument(isin="US67066G1040", name="NVIDIA Corporation", ticker_eodhd="NVDA.US", mic="XNAS",
                     currency="USD", in_universe=True, market_cap_eur=4.1e12, adv_eur_20=3.0e10))  # fmt: skip
    for k in range(3):
        d = date(2026, 11, 14) + timedelta(days=k)
        s.add(PriceEod(isin="US67066G1040", day=d, open=180, high=182, low=178, close=181 + k, volume=1,
                       source="eodhd", received_at=NOW))  # fmt: skip
    s.add(NewsItem(source="rss", guid="g1", dedup_hash="h1", fetched_at=datetime.now(UTC), title="Take-Two : GTA VI",
                   isins=["US8740541094"]))  # fmt: skip


def test_a_value_is_followed_gate_by_gate(db_engine, config):
    with db_session() as s:
        _seed(s)
        s.flush()
        assert [i.isin for i in find(s, "nvda")] == ["US67066G1040"]  # by ticker, any case
        assert [i.isin for i in find(s, "take-two")] == ["US8740541094"]  # by name
        ttwo = "\n".join(trace(s, config, "TTWO", date(2026, 11, 18)))
        nvda = "\n".join(trace(s, config, "US67066G1040", date(2026, 11, 18)))
        unknown = trace(s, config, "ZZZZ", date(2026, 11, 18))
    assert "univers : NON — motifs : capitalisation inconnue" in ttwo and "capitalisation inconnue" in ttwo
    assert "actualités rattachées (14 j) : 1 — dernière : Take-Two : GTA VI" in ttwo
    assert "propositions (30 j) : aucune — une proposition naît d'un signal, jamais d'une actualité seule" in ttwo
    assert "univers : OUI" in nvda and "4100.0 Md€" in nvda and "3 séance(s) stockée(s), dernière 16/11/2026" in nvda
    assert "liste de surveillance : NON" in nvda and "aucun détecteur" in nvda
    assert "aucune valeur connue" in unknown[0]


def test_gates_coverage_value_prints_each_value(db_engine, monkeypatch, capsys):
    monkeypatch.setenv("PARAMS_PATH", "config/params.example.yaml")
    with db_session() as s:
        _seed(s)
    args = argparse.Namespace(value=["NVDA", "TTWO"], from_date=None, to=None, json=False)
    assert cli.cmd_gates_coverage(args) == 0
    out = capsys.readouterr().out
    assert "NVIDIA Corporation" in out and "Take-Two Interactive" in out
