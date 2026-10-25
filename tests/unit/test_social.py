"""docs/26 S3 — forum dates parsed in several French formats (unknown formats ignored), counts per day,
and the « buzz précoce » label (retail chatter before any publication and before the price)."""

from datetime import date

from app.data.providers.social import daily_counts, forum_dates
from app.domain.weak_signals import BUZZ_EARLY, PUMP, RadarInput, RadarParams, label

TODAY = date(2026, 10, 25)


def test_forum_dates_formats_and_counts():
    html = """<div>Le 24/10/2026 18:02</div><p>aujourd'hui 09:15</p><span>Hier 22:40</span>
              <i>23 oct. 2026</i><b>32/13/2026</b><em>il y a 5 min</em>"""
    d = forum_dates(html, TODAY)
    assert d.count(TODAY) == 2 and d.count(date(2026, 10, 24)) == 2 and date(2026, 10, 23) in d
    counts = {c.day: c.messages for c in daily_counts(d)}
    assert counts == {date(2026, 10, 23): 1, date(2026, 10, 24): 2, TODAY: 2}
    only_today = daily_counts([TODAY] * 30)
    assert only_today[0].truncated  # the page holds only today's messages: a floor


def _inp(**kw):
    base = {"attention_z": None, "rvol": None, "move_atr": None, "news_recent": 0, "small_cap": False,
            "rs_rank": None, "social_z": None, "move_5d_atr": None}  # fmt: skip
    return RadarInput(**{**base, **kw})


def test_early_buzz_is_an_idea_and_a_small_cap_frenzy_stays_blocked():
    p = RadarParams()
    lab = label(_inp(social_z=3.1, move_5d_atr=0.6, rvol=1.8), p)
    assert lab.label == BUZZ_EARLY and lab.idea and "avant toute publication" in lab.text
    assert label(_inp(social_z=3.1, move_5d_atr=2.5), p) is None  # the move already happened: not early
    assert label(_inp(social_z=3.1, move_5d_atr=0.5, news_recent=1), p) is None  # published: not a weak signal
    assert label(_inp(social_z=3.5, rvol=4, small_cap=True, move_5d_atr=0.5), p).label == PUMP


def test_velocity_needs_coverage_or_a_usual_rate():
    from app.domain.weak_signals import velocity

    now = 1_000_000.0
    h = 3600
    quiet_then_burst = [now - 20 * h, now - 10 * h] + [now - k * 600 for k in range(6)]
    recent, ratio = velocity(quiet_then_burst + [now - 30 * h], now)  # page reaches back 30 h
    assert recent == 6 and ratio == (6 / 2) / (2 / 24)
    busy_page = [now - k * 60 for k in range(30)]  # a busy forum: the page covers only 30 minutes
    assert velocity(busy_page, now) == (30, None)  # unknown, never read as an acceleration
    assert velocity(busy_page, now, usual_per_hour=20.0)[1] == (30 / 2) / 20.0


def test_forum_url_falls_back_on_other_tickers():
    from app.db.models import Instrument
    from app.services.social import forum_url

    tpl = {"XPAR": "https://www.boursorama.com/bourse/forum/1rP{ticker}/"}
    assert forum_url(Instrument(isin="X", name="A", mic="XPAR", ticker_local="AI"), tpl).endswith("/1rPAI/")
    assert forum_url(Instrument(isin="X", name="A", mic="XPAR", ticker_eodhd="TTE.PA"), tpl).endswith("/1rPTTE/")
    assert forum_url(Instrument(isin="X", name="A", mic="XPAR", ticker_tv="EURONEXT:MC"), tpl).endswith("/1rPMC/")
    assert forum_url(Instrument(isin="X", name="A", mic="XETR", ticker_local="SAP"), tpl) is None


def test_date_holders_and_date_class_count_one_date_per_message():
    """25/10/2026: 41 dates read on both the Air Liquide and the TotalEnergies pages — a page of threads
    shows a creation date and a « last reply » date per row. ``--structure`` shows where the dates sit
    (markup names only), ``date_class`` keeps the message date alone."""
    from datetime import datetime

    from app.data.providers.social import date_holders, forum_times_in
    from app.services.social import forum_page_dates

    row = (
        '<tr class="c-table__row"><td class="c-forum-topic"><a>Pseudo123</a> sujet</td>'
        '<td class="c-forum-date c-forum-date--created">{c}</td>'
        '<td class="c-forum-date--last">{l} 14:32</td></tr>'
    )
    html = "".join(row.format(c="20/10/2026", l=f"{d}/10/2026") for d in (23, 24, 25))
    holders = date_holders(html)
    assert holders == {"td.c-forum-date c-forum-date--created": (3, 0), "td.c-forum-date--last": (3, 3)}
    assert not any("Pseudo" in k for k in holders)  # never a text or an author
    now = datetime(2026, 10, 25, 18, 0)
    got = forum_times_in(html, now, "c-forum-date--last")
    assert [(d.day, ts.hour if ts else None) for d, ts in got] == [(23, 14), (24, 14), (25, 14)]
    assert len(forum_page_dates(html, {}, TODAY)) == 6  # without date_class: both columns counted
    assert len(forum_page_dates(html, {"date_class": "c-forum-date--last"}, TODAY)) == 3
