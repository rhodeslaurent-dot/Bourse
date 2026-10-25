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
