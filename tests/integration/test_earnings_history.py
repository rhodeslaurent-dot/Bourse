"""docs/34 §34.4 — earnings announcements history: EODHD calendar (or its absence from the plan), Yahoo pages,
storage without duplicates, coverage per year and venue group, placement on the sessions for the study."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from sqlalchemy import select

from app.data.base import ProviderError, ProviderUnavailable
from app.data.providers.earnings import eodhd_earnings, when_of, yahoo_earnings
from app.db.models import EarningsEvent, Instrument
from app.db.session import db_session
from app.services import earnings
from app.services.moves import EarningsIndex

PARIS = ZoneInfo("Europe/Paris")


def _yahoo_frame(rows: list[tuple[str, float | None, float | None, float | None]]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(ts).tz_convert("UTC") for ts, *_ in rows], name="Earnings Date")
    idx = idx.tz_convert("Europe/Paris")  # as yfinance: one zone, the exchange's
    return pd.DataFrame(
        {
            "EPS Estimate": [r[1] for r in rows],
            "Reported EPS": [r[2] for r in rows],
            "Surprise(%)": [r[3] for r in rows],
        },
        index=idx,
    )


def test_yahoo_pages_time_of_day_and_missing_fields():
    frame = _yahoo_frame([
        ("2025-07-24 07:00:00+02:00", 1.2, 1.5, 25.0),  # before the open
        ("2025-04-24 18:00:00+02:00", 1.0, None, None),  # after the close, EPS not reported
        ("2025-01-30 00:00:00+01:00", None, None, None),  # time unknown
    ])  # fmt: skip
    calls = []

    def fetch(sym, limit, offset):
        calls.append(offset)
        return frame if offset == 0 else frame.iloc[0:0]

    recs = yahoo_earnings("MC.PA", 3, fetch, PARIS, (9.0, 17.5))
    assert calls == [0]  # a short page ends the history
    assert [r.when for r in recs] == ["bmo", "amc", "unknown"]
    assert recs[0].day == date(2025, 7, 24) and recs[0].surprise_pct == 25.0 and recs[0].ts.tzinfo is not None
    assert recs[1].eps_actual is None and recs[1].eps_estimate == 1.0
    assert when_of(datetime(2025, 1, 1, 12, 0), 9.0, 17.5) == "intraday"


def test_eodhd_calendar_or_its_absence_from_the_plan():
    def ok(path, **params):
        assert path == "/calendar/earnings" and params["symbols"] == "MC.PA,OR.PA" and params["from"] == "2016-01-01"
        return {"earnings": [{"code": "MC.PA", "report_date": "2025-07-24", "before_after_market": "AfterMarket",
                              "actual": 12.1, "estimate": 11.5, "percent": 5.2}]}  # fmt: skip

    recs = eodhd_earnings(ok, ["MC.PA", "OR.PA"], date(2016, 1, 1), date(2026, 10, 1))
    assert len(recs) == 1 and recs[0].when == "amc" and recs[0].source == "eodhd"

    def forbidden(path, **params):
        raise ProviderError("EODHD 403 on /calendar/earnings: forbidden")

    with pytest.raises(ProviderUnavailable):
        eodhd_earnings(forbidden, ["MC.PA"], date(2016, 1, 1), date(2026, 1, 1))

    def payment(path, **params):
        from app.data.base import QuotaExceeded

        raise QuotaExceeded("EODHD 402: plan does not include this data")

    with pytest.raises(ProviderUnavailable):  # the plan « EOD+Intraday » has no calendar (docs/34 §34.8)
        eodhd_earnings(payment, ["MC.PA"], date(2016, 1, 1), date(2026, 1, 1))


def test_load_falls_back_to_yahoo_stores_once_and_reports_coverage(db_engine, config, calendars):
    with db_session() as s:
        s.add(Instrument(isin="FR0000121014", name="LVMH", ticker_eodhd="MC.PA", mic="XPAR", in_universe=True))
        s.add(Instrument(isin="FR0000120321", name="L'Oréal", ticker_eodhd="OR.PA", mic="XPAR", in_universe=True))
        s.flush()

        class NoCalendar:
            def _get(self, path, **params):
                raise ProviderError("EODHD 403 on /calendar/earnings: plan")

        frame = _yahoo_frame(
            [("2025-07-24 18:00:00+02:00", 11.5, 12.1, 5.2), ("2024-07-23 18:00:00+02:00", 13.0, 12.0, -7.7)]
        )

        def fetch(sym, limit, offset):
            return frame if sym == "MC.PA" and offset == 0 else None

        args = dict(source="auto", eodhd=NoCalendar(), yahoo_fetch=fetch)
        rep = earnings.load_history(s, config, calendars, date(2016, 1, 1), date(2026, 10, 1), **args)
        assert rep.added == 2 and rep.with_events == 1 and any("Yahoo utilisé" in n for n in rep.notes)
        again = earnings.load_history(s, config, calendars, date(2016, 1, 1), date(2026, 10, 1), **args)
        assert again.added == 0  # (isin, day, source) is unique: a second run adds nothing
        rows = list(s.scalars(select(EarningsEvent)))
        assert {r.when for r in rows} == {"amc"} and all(r.source == "yahoo" for r in rows)
        cov = earnings.coverage(s, config, calendars, date(2016, 1, 1), date(2026, 10, 1))
        assert cov["P0"][2025] == {"valeurs": 1, "publications": 1, "avec_surprise": 1}
        assert "2024" in earnings.coverage_text(cov)


def test_the_reaction_session_follows_an_announcement_after_the_close():
    days = [date(2025, 7, d) for d in (21, 22, 23, 24, 25, 28, 29)]
    ix = EarningsIndex(days, [(date(2025, 7, 24), "amc"), (date(2025, 7, 22), "bmo")])
    assert ix.is_reaction(4) is True  # Friday 25 reacts to Thursday 24 after the close
    assert ix.is_reaction(1) is True and ix.is_reaction(3) is False
    assert ix.days_to_next(0) == 1 and ix.days_to_next(4) == 999
    far = EarningsIndex([date(2010, 1, 4)], [(date(2025, 7, 24), "amc")])
    assert far.is_reaction(0) is None and far.days_to_next(0) is None  # outside the loaded span: unknown


def test_the_study_says_which_rises_started_on_results(db_engine, example_raw, calendars):
    from app.config import validate_params
    from app.services import moves
    from tests.integration.test_moves_study import _seed

    config = validate_params(example_raw)
    days = _seed(6, 600)
    with db_session() as s:
        for isin_n in range(6):
            for k in range(280, 600, 63):  # a quarterly announcement after the close
                s.add(EarningsEvent(isin=f"FR00000000{isin_n:02d}", day=days[k], when="amc", source="yahoo",
                                    received_at=datetime(2026, 10, 28, tzinfo=UTC)))  # fmt: skip
        s.flush()
        rep = moves.run_study(s, config, calendars, days[300], days[-1], max_episodes=100, control_values=2, seed=2)
    assert rep["meta"]["valeurs_avec_publications"] == 6
    assert "debut_sur_publication_de_resultats" in rep["profil"]["debut_de_hausse"]
    assert "resultats_imminents" in rep["entonnoir_tous_detecteurs"]
