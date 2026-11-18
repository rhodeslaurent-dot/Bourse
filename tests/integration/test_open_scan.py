"""docs/06 D1 (18/11/2026): the opening scan — a value with a catalyst since the previous close that opens with a
held gap and an unusual volume gets a card and one Telegram alert; the engine decides (C ≥ ``scoring.d1_min_c``),
the card expires within the morning, a delayed quote is said, and the availability mode is read first."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.data.dto import DataStatus
from app.db.models import DecisionSnapshot, Instrument, NewsItem, OfficialFiling, Proposal, Signal
from app.db.session import db_session
from app.scheduler.jobs import open_scan as job
from app.scheduler.runner import JobContext
from app.services import open_scan
from tests.integration.test_engine import ISIN, _seed
from tests.unit.test_d1_gap import bars

NOW = datetime(2026, 10, 22, 7, 15, tzinfo=UTC)  # 09:15 Paris, the session after the last stored close
PREV = bars()[-1].close


class Provider:
    calls: list[list[str]] = []

    def __init__(self, gap: float = 0.07) -> None:
        self.gap = gap

    def day_quotes(self, symbols):  # type: ignore[no-untyped-def]
        Provider.calls.append(list(symbols))
        o = PREV * (1 + self.gap)
        return [{"symbol": "TTE.PA", "market_timestamp": NOW - timedelta(minutes=2), "received_at": NOW,
                 "data_status": DataStatus.DELAYED, "open": o, "high": o * 1.008, "low": o * 0.996,
                 "close": o * 1.006, "previousClose": PREV, "volume": 900_000.0}]  # fmt: skip


class Alerts:
    def __init__(self) -> None:
        self.sent: list = []

    def emit(self, s, event, mode, *a, **k):  # type: ignore[no-untyped-def]
        self.sent.append(event)
        return True


def _seed_value(news: bool = True) -> None:
    with db_session() as s:
        _seed(s, bars())
        inst = s.get(Instrument, ISIN)
        inst.ticker_eodhd, inst.active = "TTE.PA", True
        if news:
            c = {"direction": "positif", "magnitude": "forte", "type": "resultats"}
            s.add(NewsItem(source="test", guid="n1", dedup_hash="n1", title="Résultats nettement supérieurs",
                           fetched_at=NOW - timedelta(hours=10), isins=[ISIN], classification=c))  # fmt: skip
        else:
            s.add(OfficialFiling(source="test", diffuser="test", uin_id="f1", time_precision="seconde",
                                 family="resultats", isin=ISIN, title="Résultats", received_at=NOW, processed_at=NOW,
                                 market_timestamp=NOW - timedelta(hours=14)))  # fmt: skip


def test_an_opening_gap_after_good_news_gets_a_card_and_one_alert(db_engine, config, calendars):
    _seed_value()
    alerts = Alerts()
    with db_session() as s:
        n, note = open_scan.run(s, config, calendars, NOW, {"XPAR"}, alerts, "disponible", Provider())
    assert n == 1 and "1 en écart ≥ 3 %" in note and "1 fiche(s) nouvelle(s)" in note
    (ev,) = alerts.sent
    assert ev.kind == "d1_gap" and ev.level == "P2" and ev.isin == ISIN and len(ev.lines) <= 12
    assert "ouverture +7,0 %, volume" in ev.lines[0] and ev.lines[0].endswith("fois l'habituel")
    assert ev.lines[1].startswith("Catalyseur : actualité positive (forte)")
    assert any("Cours différé" in x for x in ev.lines) and any("à toi de juger" in x for x in ev.lines)
    with db_session() as s:
        sig = s.query(Signal).filter(Signal.detector == "d1_gap_catalyst").one()
        p = s.query(Proposal).filter(Proposal.signal_id == sig.id).one()
        assert sig.evidence["ouverture"] > PREV and sig.evidence["cloture_veille"] == PREV
        assert any("catalyseur classé 15 pt ≥ 12 (D1)" in x for x in (p.gates or {}).get("passed", []))
        exp = p.expires_at if p.expires_at.tzinfo else p.expires_at.replace(tzinfo=UTC)
        assert exp <= NOW + timedelta(minutes=30)  # a decision of the morning, never a card left for days
        assert s.query(DecisionSnapshot).filter(DecisionSnapshot.proposal_id == p.id).count() == 1  # rule 10
    alerts2 = Alerts()
    with db_session() as s:  # 10 minutes later: same value, same day — no second card, no second alert
        n2, _ = open_scan.run(s, config, calendars, NOW + timedelta(minutes=10), {"XPAR"}, alerts2, "disponible",
                              Provider())  # fmt: skip
    assert n2 == 0 and alerts2.sent == []


def test_results_without_a_classified_news_stay_a_watch(db_engine, config, calendars):
    _seed_value(news=False)
    alerts = Alerts()
    with db_session() as s:
        n, _ = open_scan.run(s, config, calendars, NOW, {"XPAR"}, alerts, "disponible", Provider())
        p = s.query(Proposal).one()
        assert n == 1 and p.action == "WATCH"
        assert any("catalyseur classé 0 pt < 12" in b for b in (p.gates or {}).get("blocked", []))
    assert alerts.sent[0].lines[1].startswith("Catalyseur : résultats publiés le 21/10")
    assert any(x.startswith("L'outil ne prépare pas d'ordre") for x in alerts.sent[0].lines)


def test_nothing_without_a_gap_out_of_mode_or_before_the_open(db_engine, config, calendars):
    _seed_value()
    with db_session() as s:
        n, note = open_scan.run(s, config, calendars, NOW, {"XPAR"}, Alerts(), "disponible", Provider(gap=0.01))
        assert n == 0 and "0 en écart ≥ 3 %" in note
        n, note = open_scan.run(s, config, calendars, NOW, {"XPAR"}, Alerts(), "reunion", Provider())
        assert n == 0 and "mode « reunion »" in note
        early = datetime(2026, 10, 22, 6, 30, tzinfo=UTC)  # 08:30 Paris
        n, note = open_scan.run(s, config, calendars, early, {"XPAR"}, Alerts(), "disponible", Provider())
        assert n == 0 and note == "aucune place concernée ouverte"
        assert s.query(Signal).filter(Signal.detector == "d1_gap_catalyst").count() == 0


def test_the_jobs_split_europe_and_new_york(config, calendars, monkeypatch):
    seen = []
    monkeypatch.setattr(job.open_scan, "run", lambda s, c, cal, now, mics, *a: (seen.append(mics), (0, "ok"))[1])
    ctx = JobContext(config, calendars, date(2026, 10, 22), ("XPAR",), "normal", mode="disponible")
    monkeypatch.setattr(job, "db_session", _NoDb)
    assert job.run_eu(ctx) == 0 and job.run_us(ctx) == 0 and ctx.note == "ok"
    assert "XPAR" in seen[0] and "XNYS" not in seen[0] and seen[1] == {"XNYS"}


class _NoDb:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False
