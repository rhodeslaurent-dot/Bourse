"""Step 1 of the user's plan (15/11/2026), lots S1 to S3: the health of every source day by day, one journal of
events built from every source, and what each value did 1, 5 and 20 sessions after each event against its venue.
All values, letters and prices are synthetic fixtures."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import (
    EventOutcome,
    InsiderDeclaration,
    Instrument,
    MarketEvent,
    NewsletterItem,
    NewsletterValue,
    PriceEod,
    SourceHealthRow,
)
from app.db.session import db_session
from app.domain.timeutil import PARIS
from app.scheduler.runner import JobContext
from app.services import event_journal, event_measure, source_health

T = "FR0000000T01"  # the value the events are about: +1 % per session after 01/10
B = "FR0000000B01"  # splits 2-for-1 on 06/10
C = "FR0000000C01"  # −1 % per session after 01/10 (a « sell » that is right)
OTHERS = [f"FR00000000{k:02d}" for k in range(10)]  # flat: the venue's median is 0
D0 = date(2026, 10, 1)
NOW = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)


def _paris(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=PARIS).astimezone(UTC)


def _weekdays(a: date, b: date) -> list[date]:
    out, d = [], a
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


DAYS = _weekdays(date(2026, 9, 1), date(2026, 10, 30))


def _close(isin: str, d: date) -> tuple[float, float]:
    n = sum(1 for x in DAYS if D0 < x <= d)
    if isin == T:
        return 100 * 1.01**n, 100 * 1.01**n
    if isin == C:
        return 100 * 0.99**n, 100 * 0.99**n
    if isin == B:
        return (100.0, 50.0) if d < date(2026, 10, 6) else (50.0, 50.0)  # raw close, adjusted close
    return 100.0, 100.0


def _market(s) -> None:
    for k, isin in enumerate([T, B, C, *OTHERS]):
        s.add(Instrument(isin=isin, name=f"Valeur {k}", mic="XPAR", currency="EUR", in_universe=True,
                         sector="Technologie" if k < 7 else "Banques"))  # fmt: skip
        for d in DAYS:
            c, a = _close(isin, d)
            s.add(PriceEod(isin=isin, day=d, open=c, high=c, low=c, close=c, adj_close=a, volume=1000,
                           source="eodhd", received_at=NOW))  # fmt: skip
    s.flush()


def _letter(s, isin: str, direction: str, sent: datetime, uid: str) -> None:
    it = NewsletterItem(source="lettre_test", message_id=uid, dedup_hash=uid, received_at=sent, sent_at=sent,
                        subject=f"Lettre {uid}", body_text="…")  # fmt: skip
    s.add(it)
    s.flush()
    s.add(NewsletterValue(item_id=it.id, isin=isin, direction=direction))


def _insider(s, isin: str, at: datetime) -> None:
    s.add(InsiderDeclaration(source="amf_bdif", doc_id="2026DD0000001", market_timestamp=at, nature="acquisition",
                             received_at=at, processed_at=at, isin=isin, issuer_name="Émetteur fictif"))  # fmt: skip


# --- S1: health of the sources ---------------------------------------------------------------------------------------


def test_an_empty_base_shows_every_source_empty_and_the_snapshot_is_idempotent(db_engine):
    with db_session() as s:
        states = source_health.snapshot(s, date(2026, 11, 2))
        source_health.snapshot(s, date(2026, 11, 2))
        n = s.scalar(select(func.count(SourceHealthRow.id)))
    assert {x.status for x in states} == {"vide"}
    assert n == len(source_health.registry())  # one row per day and source, never two
    assert "vides :" in source_health.summary_line(states)


def test_a_source_silent_longer_than_its_cadence_is_late_and_alerts_once(db_engine):
    with db_session() as s:
        _market(s)  # last close on Friday 30/10
        _letter(s, T, "buy", _paris(date(2026, 11, 2), 7), "a1")
        mon = {x.key: x for x in source_health.snapshot(s, date(2026, 11, 2))}
        thu = source_health.snapshot(s, date(2026, 11, 5))
        late_thu = source_health.newly_late(s, date(2026, 11, 5), thu)
        fri = source_health.snapshot(s, date(2026, 11, 6))
        late_fri = source_health.newly_late(s, date(2026, 11, 6), fri)
        day, rows = source_health.latest(s)
        strip = source_health.history(s, date(2026, 11, 6))
    assert mon["cours_eod"].status == "ok" and mon["cours_eod"].silent_weekdays == 1
    assert mon["cours_eod"].total == len(DAYS) * 13 and mon["cours_eod"].first == DAYS[0]
    assert mon["lettres"].items_day == 1 and mon["lettres"].status == "ok"
    assert "cours_eod" in {x.key for x in late_thu}  # 4 weekdays without a close > 2 allowed
    assert "cours_eod" not in {x.key for x in late_fri}  # still late, but already said
    assert day == date(2026, 11, 6) and rows[0]["key"] == "cours_eod" and rows[0]["status_label"] == "en retard"
    assert [st for _, st in strip["cours_eod"]] == ["ok", "en_retard", "en_retard"]


# --- S2: the journal -------------------------------------------------------------------------------------------------


def test_letters_and_managers_dealings_become_events_once_with_their_context(db_engine, config):
    with db_session() as s:
        _market(s)
        _letter(s, T, "buy", _paris(D0, 10), "a1")
        _insider(s, T, _paris(D0, 19))
        rep = event_journal.collect(s, config, _paris(date(2026, 9, 1), 0))
        again = event_journal.collect(s, config, _paris(date(2026, 9, 1), 0))
        evs = {e.kind: e for e in s.scalars(select(MarketEvent))}
    assert sum(rep.added.values()) == 2 and sum(again.added.values()) == 0  # idempotent
    assert evs["lettre_achat"].direction == 1 and evs["lettre_achat"].mic == "XPAR"
    assert evs["lettre_achat"].source_key == "lettre:lettre_test"
    assert evs["dirigeant_acquisition"].direction == 1
    assert set(evs["lettre_achat"].context) == {"regime", "regime_de", "vix", "annonce_macro"}
    assert evs["lettre_achat"].context["regime"] is None  # unknown, said so (never a guess)


# --- S3: the measure -------------------------------------------------------------------------------------------------


def _measured(s, calendars):
    event_journal_config = type("C", (), {"raw": {}})()
    event_journal.collect(s, event_journal_config, _paris(date(2026, 9, 1), 0))
    rep = event_measure.measure(s, NOW.date(), calendars, now=NOW)
    out = {}
    for ev in s.scalars(select(MarketEvent)):
        for o in s.scalars(select(EventOutcome).where(EventOutcome.event_id == ev.id)):
            out[(ev.isin, ev.kind, o.horizon)] = o
    return rep, out


def test_the_reference_is_the_first_close_after_publication_and_the_excess_is_over_the_venue(db_engine, calendars):
    with db_session() as s:
        _market(s)
        _letter(s, T, "buy", _paris(D0, 10), "a1")  # before the close: the close of 01/10
        _insider(s, T, _paris(D0, 19))  # after the close: the close of 02/10
        _, out = _measured(s, calendars)
    a1 = out[(T, "lettre_achat", 1)]
    assert a1.status == "final" and a1.ref_day == D0 and a1.end_day == date(2026, 10, 2)
    assert a1.ret == pytest.approx(0.01) and a1.venue_median == pytest.approx(0.0)
    assert a1.excess_venue == pytest.approx(0.01) and a1.sector_median == pytest.approx(0.0)
    ins = out[(T, "dirigeant_acquisition", 1)]
    assert ins.ref_day == date(2026, 10, 2) and ins.end_day == date(2026, 10, 5)
    a20 = out[(T, "lettre_achat", 20)]
    assert a20.status == "final" and a20.ret == pytest.approx(1.01**20 - 1)


def test_a_window_not_finished_is_pending_and_a_split_makes_it_unavailable(db_engine, calendars):
    with db_session() as s:
        _market(s)
        _letter(s, T, "buy", _paris(date(2026, 10, 28), 9), "late")
        _letter(s, B, "buy", _paris(D0, 9), "split")
        rep, out = _measured(s, calendars)
        assert out[(T, "lettre_achat", 1)].status == "final"
        assert out[(T, "lettre_achat", 20)].status == "pending"
        assert out[(B, "lettre_achat", 1)].status == "final"  # 01/10 → 02/10: before the split
        five = out[(B, "lettre_achat", 5)]
        assert five.status == "unavailable" and "opération sur titre" in five.note
        assert rep.pending >= 2 and rep.unavailable >= 2
        again = event_measure.measure(s, NOW.date(), calendars, now=NOW)  # only the pending ones are read again
    assert again.events == 1 and again.pending == 2


def test_the_scoreboard_reads_each_source_in_its_own_direction(db_engine, calendars):
    with db_session() as s:
        _market(s)
        _letter(s, C, "sell", _paris(D0, 9), "s1")
        _letter(s, T, "buy", _paris(D0, 9), "b1")
        _measured(s, calendars)
        rows = {(r.kind, r.direction): r for r in event_measure.scoreboard(s, 5)}
    sell, buy = rows[("lettre_vente", -1)], rows[("lettre_achat", 1)]
    assert sell.stats.mean == pytest.approx(1 - 0.99**5) and sell.stats.hit_rate == 1.0  # the fall proves it right
    assert buy.stats.mean == pytest.approx(1.01**5 - 1)
    assert set(buy.by_regime) == {"inconnu"} and set(buy.by_vix) == {"inconnu"}
    assert set(buy.by_macro) == {"sans annonce macro"}
    lines = event_measure.report_lines(list(rows.values()), 5)
    assert any("lettre_vente" in x and "+4,9 %" in x for x in lines)


# --- the job and the page --------------------------------------------------------------------------------------------


def test_the_evening_job_builds_measures_and_says_the_health_of_every_source(db_engine, config, calendars):
    from app.scheduler.jobs import sources_daily

    emitted = []

    class Alerts:
        def emit(self, s, event, mode, intraday):
            emitted.append(event)

    with db_session() as s:
        _market(s)
        _letter(s, T, "buy", _paris(date(2026, 11, 4), 9), "j1")
    ctx = JobContext(config, calendars, date(2026, 11, 5), ("XPAR",), "normal", alerts=Alerts())
    n = sources_daily.run(ctx)
    assert n == len(source_health.registry())
    assert "1 événement(s) ajouté(s)" in (ctx.note or "") and "Sources :" in (ctx.note or "")
    assert emitted and emitted[0].level == "P3" and len(emitted[0].lines) <= 11
    assert any("Cours de clôture" in x for x in emitted[0].lines)


def test_the_sources_page_shows_the_health_and_the_scoreboard(config, calendars, db_engine, monkeypatch):
    from app.main import create_app

    with db_session() as s:
        _market(s)
        _letter(s, T, "buy", _paris(D0, 9), "p1")
        _measured(s, calendars)
        source_health.snapshot(s, date(2026, 10, 30))
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        html = c.get("/sources").text
        h20 = c.get("/sources?h=20").text
    assert "Cours de clôture" in html and "à jour" in html and "Lettres d&#39;analystes" in html
    assert "Ce qui a suivi les événements, à 5 séance(s)" in html and "20 séance(s)" in h20
    assert "pas encore de mesure" in html  # one event: under the 5 needed for a line


def test_the_other_sources_enter_the_journal_with_the_source_s_own_direction(db_engine, config):
    from app.db.models import AnalystConsensus, NewsItem, OfficialFiling, ShortPosition

    at = _paris(date(2026, 10, 6), 8)
    with db_session() as s:
        _market(s)
        for d, target in ((date(2026, 10, 1), 50.0), (date(2026, 10, 6), 52.0), (date(2026, 10, 7), 52.1)):
            s.add(AnalystConsensus(isin=T, day=d, source="tradingview", received_at=at,
                                   data={"objectif_moyen": target}))  # fmt: skip
        s.add(ShortPosition(authority="amf", isin=C, holder="Fonds fictif", pct=0.006,
                            position_date=date(2026, 10, 2), received_at=at))  # fmt: skip
        s.add(ShortPosition(authority="amf", isin=C, holder="Fonds fictif", pct=0.008,
                            position_date=date(2026, 10, 5), received_at=at))  # fmt: skip
        s.add(NewsItem(source="flux_test", guid="g1", dedup_hash="n1", fetched_at=at, published_at=at,
                       title="Contrat géant", isins=[T, C], classification={"type": "contrat", "direction": "positif",
                                                                             "magnitude": "forte"}))  # fmt: skip
        s.add(OfficialFiling(source="info_financiere", diffuser="X", uin_id="u1", family="resultats",
                             time_precision="seconde", received_at=at, processed_at=at, isin=T,
                             market_timestamp=at, title="Résultats semestriels"))  # fmt: skip
        s.flush()
        rep = event_journal.collect(s, config, _paris(date(2026, 9, 1), 0))
        evs = [
            (e.isin, e.kind, e.direction, e.strength) for e in s.scalars(select(MarketEvent).order_by(MarketEvent.id))
        ]
    kinds = {(i, k): (d, st) for i, k, d, st in evs}
    assert kinds[(T, "objectif_hausse")] == (1, pytest.approx(0.04))  # 50 → 52; 52 → 52.1 is under 2 %
    assert sum(1 for i, k, *_ in evs if k.startswith("objectif")) == 1
    assert kinds[(C, "vad_nouvelle")][0] == -1 and kinds[(C, "vad_hausse")][0] == -1
    assert kinds[(T, "nouvelle_contrat")] == (1, 3.0) and (C, "nouvelle_contrat") in kinds  # one event per value
    assert kinds[(T, "communique_resultats")][0] is None  # a filing says nothing of its direction
    assert rep.added["consensus"] == 1 and rep.added["vad"] == 2 and rep.added["actualites"] == 2
    with db_session() as s:
        ev = s.scalar(select(MarketEvent).where(MarketEvent.kind == "vad_nouvelle"))
        # public the business day after the position date (02/10, Friday → Monday 05/10 18:00 Paris)
        assert ev.event_at.replace(tzinfo=UTC) == _paris(date(2026, 10, 5), 18)


def test_the_measure_reads_its_settings_from_params(config):
    st = event_measure.settings(config)
    assert st.horizons == (1, 5, 20) and st.venue_min == 10 and st.sector_min == 5
    assert event_journal.consensus_min_change(config) == pytest.approx(0.02)
    assert event_journal.short_volume_z(config) == pytest.approx(3.0)
