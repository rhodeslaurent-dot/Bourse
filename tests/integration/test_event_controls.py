"""16/11/2026 — the step 1 control group: the same value, the same horizon, 60 sessions before and after the event,
away from any other event of the same source. Synthetic closes only."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from app.db.models import EventControl, Instrument, MarketEvent, NewsletterItem, NewsletterValue, PriceEod
from app.db.session import db_session
from app.domain.timeutil import PARIS
from app.services import event_journal, event_measure

X = "FR00000000X1"  # rises 1 % a session for 20 sessions after the event, flat otherwise
FLAT = [f"FR0000000Z{k:02d}" for k in range(12)]


def _weekdays(a: date, n: int) -> list[date]:
    out, d = [], a
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


DAYS = _weekdays(date(2026, 1, 5), 200)
EVENT = DAYS[100]
NOW = datetime(2026, 11, 16, 21, 0, tzinfo=UTC)


def _paris(d: date, hh: int) -> datetime:
    return datetime.combine(d, time(hh, 0), tzinfo=PARIS).astimezone(UTC)


def _market(s, last: int = 200) -> None:
    for isin in [X, *FLAT]:
        s.add(Instrument(isin=isin, name=isin, mic="XPAR", currency="EUR", in_universe=True))
        for k, d in enumerate(DAYS[:last]):
            c = 100 * 1.01 ** min(max(k - 100, 0), 20) if isin == X else 100.0
            s.add(PriceEod(isin=isin, day=d, open=c, high=c, low=c, close=c, adj_close=c, volume=1000,
                           source="eodhd", received_at=NOW))  # fmt: skip
    s.flush()


def _letter(s, isin: str, sent: datetime, uid: str) -> None:
    it = NewsletterItem(source="lettre_test", message_id=uid, dedup_hash=uid, received_at=sent, sent_at=sent,
                        subject=uid, body_text="…")  # fmt: skip
    s.add(it)
    s.flush()
    s.add(NewsletterValue(item_id=it.id, isin=isin, direction="buy"))


def _measure_all(s, today: date = date(2026, 11, 16)) -> None:
    event_journal.collect(s, type("C", (), {"raw": {}})(), _paris(date(2026, 1, 1), 0))
    st = event_measure.MeasureSettings()
    event_measure.measure(s, today, None, now=NOW, st=st)
    for offset in st.control_offsets:
        event_measure.measure(s, today, None, now=NOW, st=st, offset=offset)


def test_the_control_windows_are_the_same_value_before_and_after_and_the_gap_is_read(db_engine):
    with db_session() as s:
        _market(s)
        _letter(s, X, _paris(EVENT, 10), "e1")  # before the close: the reference is the close of that day
        _measure_all(s)
        rows = {(c.offset, c.horizon): c for c in s.scalars(select(EventControl))}
        before, after = rows[(-60, 5)], rows[(60, 5)]
        assert before.status == after.status == "final"
        assert before.ref_day == DAYS[40] and before.end_day == DAYS[45] and before.excess_venue == pytest.approx(0)
        assert after.ref_day == DAYS[160] and after.excess_venue == pytest.approx(0)
        board = {r.kind: r for r in event_measure.scoreboard(s, 5, 1)}
        row = board["lettre_achat"]
        assert row.stats.mean == pytest.approx(1.01**5 - 1)
        assert row.control.n == 2 and row.vs_control.diff == pytest.approx(1.01**5 - 1)
        text = "\n".join(event_measure.report_lines(list(board.values()), 5))
        assert "témoin n" in text and "écart" in text


def test_a_window_holding_another_event_of_the_source_is_not_a_control(db_engine):
    with db_session() as s:
        _market(s)
        _letter(s, X, _paris(EVENT, 10), "e1")
        _letter(s, X, _paris(DAYS[42], 10), "e0")  # the same letter source, inside the −60 window of e1
        _measure_all(s)
        e1 = s.scalar(select(MarketEvent).where(MarketEvent.event_at == _paris(EVENT, 10)))
        c = s.scalar(select(EventControl).where(EventControl.event_id == e1.id, EventControl.offset == -60,
                                                EventControl.horizon == 5))  # fmt: skip
        assert c.status == "unavailable" and "même source" in c.note


def test_a_control_not_over_yet_waits_and_history_too_short_is_said(db_engine):
    with db_session() as s:
        _market(s, last=150)  # the +60 window is not over
        _letter(s, X, _paris(EVENT, 10), "e1")
        _letter(s, X, _paris(DAYS[30], 10), "e2")  # 30 sessions of history: no −60 window
        _measure_all(s, today=DAYS[149])
        rows = list(s.scalars(select(EventControl)))
        e1 = s.scalar(select(MarketEvent.id).where(MarketEvent.event_at == _paris(EVENT, 10)))
        after = [c for c in rows if c.event_id == e1 and c.offset == 60 and c.horizon == 5]
        assert [c.status for c in after] == ["pending"]  # DAYS[160] does not exist yet
        short = [c for c in rows if c.offset == -60 and c.note == "historique insuffisant avant l'événement"]
        assert short
        assert event_measure.reset(s) > 0 and not list(s.scalars(select(EventControl)))


def test_the_cli_measures_one_pass_at_a_time(db_engine, monkeypatch, capsys):
    from app import cli

    with db_session() as s:
        _market(s)
        _letter(s, X, _paris(EVENT, 10), "e1")
        event_journal.collect(s, type("C", (), {"raw": {}})(), _paris(date(2026, 1, 1), 0))
    monkeypatch.setattr(cli, "load_params", lambda path: type("C", (), {"raw": {}})())
    base = ["events-measure", "--date", "2026-11-16"]
    assert cli.main([*base, "--offset", "-60"]) == 0
    from app.db.models import EventOutcome

    with db_session() as s:
        assert {c.offset for c in s.scalars(select(EventControl))} == {-60}  # only that pass
        assert s.scalars(select(EventOutcome)).first() is None  # the events' pass was not run
    assert "témoin -60 séances" in capsys.readouterr().out


def test_the_scoreboard_reads_one_control_window_at_a_time(db_engine):
    """17/11/2026 — a run-up before the event sits in the −60 window: each window is read on its own too."""
    with db_session() as s:
        _market(s)
        _letter(s, X, _paris(EVENT, 10), "e1")
        _measure_all(s)
        both = {r.kind: r for r in event_measure.scoreboard(s, 5, 1)}["lettre_achat"]
        before = {r.kind: r for r in event_measure.scoreboard(s, 5, 1, control_offset=-60)}["lettre_achat"]
        after = {r.kind: r for r in event_measure.scoreboard(s, 5, 1, control_offset=60)}["lettre_achat"]
    assert both.control.n == 2 and before.control.n == 1 and after.control.n == 1
