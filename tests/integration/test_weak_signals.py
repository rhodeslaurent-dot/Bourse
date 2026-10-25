"""docs/26 — the radar reads Wikidata and Wikipedia (mocked here), labels the values, blocks an unconfirmed
frenzy and never creates a proposal by itself; the measure gives the excess return per label."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import httpx

from app.db.models import (
    FeatureDaily,
    Instrument,
    NewsItem,
    PriceEod,
    Proposal,
    WeakSignalHit,
    WikiArticle,
)
from app.db.session import db_session
from app.services import engine
from app.services.weak_signals import compute_hits, measure, refresh_articles, refresh_attention
from tests.integration.test_engine import DAY, ISIN, NOW, _seed, breakout_bars

D = date(2026, 10, 20)
T = datetime(2026, 10, 21, 5, 0, tzinfo=UTC)


def _client() -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        assert "BoursePilot" in req.headers["user-agent"]
        if "wikidata" in req.url.host:
            body = {"results": {"bindings": [{"isin": {"value": "FR0000000001"}, "t_fr": {"value": "Petite Société"}}]}}
            return httpx.Response(200, json=body)
        if "Inconnue" in str(req.url):
            return httpx.Response(404)
        start = date(2026, 7, 22)
        items = [
            {"timestamp": f"{start + timedelta(days=k):%Y%m%d}00", "views": 100 + (k % 5) * 5}
            for k in range((D - start).days - 2)
        ] + [{"timestamp": f"{D - timedelta(days=k):%Y%m%d}00", "views": 2500} for k in (2, 1, 0)]
        return httpx.Response(200, content=json.dumps({"items": items}))

    return httpx.Client(transport=httpx.MockTransport(handler))


def _small(s) -> None:
    s.add(Instrument(isin="FR0000000001", name="Petite Société", mic="XPAR", currency="EUR", in_universe=True,
                     small_cap=True, market_cap_eur=2e8, active=True))  # fmt: skip
    for k in range(61):
        d = D - timedelta(days=60 - k)
        vol = 400_000 if k == 60 else 100_000
        s.add(PriceEod(isin="FR0000000001", day=d, open=10, high=10.2, low=9.9, close=10.0 + 0.01 * (k % 3),
                       volume=vol, source="eodhd", received_at=T, data_status="eod"))  # fmt: skip
    s.add(FeatureDaily(isin="FR0000000001", day=D, rs_rank=60, atr14=0.3, sessions=61))
    s.flush()


def test_mapping_views_and_pump_label(db_engine, config):
    with db_session() as s, _client() as c:
        _small(s)
        assert refresh_articles(s, config, c) == 2  # fr + en checked (en not found: stored as checked, no title)
        arts = {a.lang: a.title for a in s.query(WikiArticle).filter(WikiArticle.isin == "FR0000000001")}
        assert arts == {"fr": "Petite Société", "en": None}
        assert refresh_articles(s, config, c) == 0  # checked recently: no new query
        rows, errors = refresh_attention(s, config, c, D)
        assert rows > 60 and not errors
        hits = compute_hits(s, config, D, now=T)
        assert [(h.isin, h.label) for h in hits] == [("FR0000000001", "emballement")]
        assert hits[0].attention_z > 3 and hits[0].rvol >= 3 and hits[0].close is not None
        # a published news item on the value: corroborated attention, no block
        s.add(NewsItem(source="actusnews", guid="g1", dedup_hash="h1", fetched_at=T - timedelta(hours=3),
                       title="Petite Société : contrat", isins=["FR0000000001"]))  # fmt: skip
        s.flush()
        assert compute_hits(s, config, D, now=T)[0].label == "attention_corroboree"
        assert s.query(WeakSignalHit).count() == 1  # one label per value and day (updated)


def test_a_pump_blocks_the_entry_and_no_proposal_comes_from_the_radar_alone(db_engine, config):
    with db_session() as s:
        s.add(Instrument(isin="FR0000000002", name="Calme", mic="XPAR", currency="EUR", in_universe=True, active=True))
        s.add(WeakSignalHit(isin="FR0000000002", day=DAY, label="attention_corroboree", idea=True, text="idée",
                            computed_at=NOW))  # fmt: skip
        _seed(s, breakout_bars())
        s.add(WeakSignalHit(isin=ISIN, day=DAY, label="emballement", text="emballement non corroboré",
                            computed_at=NOW))  # fmt: skip
        s.flush()
        engine.scan(s, config, DAY, now=NOW)
        props = s.query(Proposal).all()
        assert props and {p.isin for p in props} == {ISIN}  # the idea alone never makes a proposal (A26.1)
        assert all(
            any("emballement non corroboré" in b and b.startswith("BLOQUÉ") for b in (p.gates or {}).get("blocked", []))
            for p in props
        )
        assert all((p.details or {}).get("signaux_faibles", {}).get("etiquette") == "emballement" for p in props)


def test_measure_excess_return_per_label(db_engine):
    with db_session() as s:
        start = date(2026, 1, 1)
        for i in range(25):
            isin = f"FR{i:010d}"
            for k in range(30):
                px = 10 * (1.01**k if i == 0 else 1.0)
                s.add(PriceEod(isin=isin, day=start + timedelta(days=k), open=px, high=px, low=px, close=px,
                               volume=1, source="eodhd", received_at=T, data_status="eod"))  # fmt: skip
        s.add(WeakSignalHit(isin="FR0000000000", day=start, label="attention_corroboree", text="x", computed_at=T))
        s.flush()
        res = measure(s)
        m = res["attention_corroboree"]["5_seances"]
        assert m["n"] == 1 and abs(m["mean_r"] - (1.01**5 - 1)) < 1e-4
