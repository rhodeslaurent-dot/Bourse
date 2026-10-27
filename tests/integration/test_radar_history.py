"""docs/26 (user decision of 26/10/2026, option B): years of radar history — the forum read slowly at night,
topics counted on their creation day and once each, true zeros only on covered days, a refusal of the site
pauses the reading; Wikipedia views back to 2015 fetched only where they are missing."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import httpx

from app.db.models import AttentionDaily, FeatureDaily, ForumHistoryProgress, Instrument, WikiArticle
from app.db.session import db_session
from app.services.forum_history import run_pass

NOW = datetime(2026, 10, 26, 0, 30, tzinfo=UTC)


def _row(tid: int, created: str, last: str) -> str:
    return (f'<tr class="c-table__row"><td><a href="/bourse/forum/1rPEXM/detail/{tid}/">sujet</a>'
            f'<span class="c-source__time">{created}</span></td>'
            f'<td><span class="u-color-big-stone c-table__last-message">{last}</span></td></tr>')  # fmt: skip


PAGES = {
    1: _row(1, "20/10/2026", "25 oct. 10:00") + _row(2, "01/03/2024", "24 oct. 09:00"),
    2: _row(2, "01/03/2024", "24 oct. 09:00")  # moved down a page during the reading: counted once
    + _row(3, "15/01/2025", "02/02/2025")
    + _row(4, "10/06/2019", "05/01/2019"),
    3: _row(5, "03/05/2018", "20/12/2018"),
}


def _inst(s) -> None:  # type: ignore[no-untyped-def]
    s.add(Instrument(isin="FR0000000005", name="Exemplaire", ticker_local="EXM", mic="XPAR", currency="EUR",
                     in_universe=True, active=True))  # fmt: skip
    s.add(FeatureDaily(isin="FR0000000005", day=date(2026, 10, 23), rs_rank=90, sessions=300))
    s.add(
        AttentionDaily(
            isin="FR0000000005", day=date(2026, 8, 27), source="forum_boursorama", lang="-", value=2, received_at=NOW
        )
    )  # fmt: skip — start of the 60-day backfill: the cutoff
    s.flush()


def test_forum_history_counts_each_thread_once_on_its_creation_day(db_engine, config, monkeypatch):
    monkeypatch.setitem(config.raw["weak_signals"]["social"]["history"], "forum_from", "2019-01-01")
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        k = int(req.url.path.rstrip("/").split("page-")[-1]) if "page-" in req.url.path else 1
        return httpx.Response(200, text=PAGES.get(k, ""))

    with db_session() as s, httpx.Client(transport=httpx.MockTransport(handler)) as c:
        _inst(s)
        notes = run_pass(s, config, c, NOW, sleep=lambda x: None)
        prog = s.get(ForumHistoryProgress, "FR0000000005")
        rows = {r.day: r.value for r in s.query(AttentionDaily).filter(AttentionDaily.source == "forum_boursorama")}
        assert prog.done and len(seen) == 3  # stops once the oldest last activity read is before 2019
        assert rows[date(2024, 3, 1)] == 1  # thread 2 seen twice, counted once
        assert rows[date(2025, 1, 15)] == 1 and rows[date(2019, 6, 10)] == 1
        assert date(2018, 5, 3) not in rows  # before forum_from: never counted
        assert rows[date(2026, 8, 27)] == 2  # the 60-day backfill is left as it was (thread 1 is after the cutoff)
        assert rows[date(2023, 7, 14)] == 0  # a covered day without a thread: a true zero
        assert "sujet(s) comptés" in notes[0]


def test_a_refusal_of_the_site_pauses_the_reading(db_engine, config):
    with db_session() as s, httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403))) as c:
        _inst(s)
        notes = run_pass(s, config, c, NOW, sleep=lambda x: None)
        assert "refus du site" in notes[0]
        assert run_pass(s, config, c, NOW + timedelta(hours=1), sleep=lambda x: None)[0].startswith("forum en pause")
        from app.db.models import JobRun
        from app.services.forum_history import progress_summary

        s.add(JobRun(job="forum_history", started_at=NOW, ended_at=NOW, status="ok", note=notes[0]))
        s.flush()
        p = progress_summary(s)  # 27/10/2026: tell a long forum from a pause or a job that did not run
        assert p["valeurs"] == 1 and p["terminees"] == 0
        assert p["en_cours"][0]["pause_jusqu_au"] is not None and p["en_cours"][0]["pages_lues"] == 0
        assert p["derniers_passages"][0]["statut"] == "ok" and "refus du site" in p["derniers_passages"][0]["note"]


def test_wikipedia_history_fills_only_the_missing_older_part(db_engine, config):
    from app.services.weak_signals import attention_history

    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        items = [{"timestamp": f"{date(2015, 7, 1) + timedelta(days=k):%Y%m%d}00", "views": 10} for k in range(30)]
        return httpx.Response(200, content=json.dumps({"items": items}))

    with db_session() as s, httpx.Client(transport=httpx.MockTransport(handler)) as c:
        _inst(s)
        s.add(WikiArticle(isin="FR0000000005", lang="fr", title="Exemplaire", checked_at=NOW))
        s.add(AttentionDaily(isin="FR0000000005", day=date(2026, 8, 1), source="wikipedia", lang="fr", value=5,
                             received_at=NOW))  # fmt: skip
        s.flush()
        n, days, errors = attention_history(s, config, c, date(2015, 7, 1), today=date(2026, 10, 26))
        assert (n, days, errors) == (1, 30, [])
        assert "20150701/20260731" in calls[0]  # up to the day before the first stored day


def test_a_site_serving_the_same_pages_again_ends_the_value(db_engine, config):
    """27/10/2026 on the PC: past a page number the site served the same list again (4 400 pages read, 1 518
    threads, oldest activity stuck a year back). Three pages without any new thread end the value; undated
    threads are reported, never counted."""
    loop = _row(10, "20/10/2026", "25 oct. 10:00") + _row(11, "n.c.", "24 oct. 09:00")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=loop)  # every page number: the same two threads

    with db_session() as s, httpx.Client(transport=httpx.MockTransport(handler)) as c:
        _inst(s)
        notes = run_pass(s, config, c, NOW, sleep=lambda x: None)
        prog = s.get(ForumHistoryProgress, "FR0000000005")
        assert prog.done and prog.next_page == 4  # page 1, then 3 pages without a new thread
        assert any("pagination du site épuisée" in n and "FR0000000005" in n for n in notes)
        assert any("1 sujet(s) sans date de création lisible" in n for n in notes)


def test_probe_shows_new_threads_dates_and_formats(db_engine, config, monkeypatch):
    from app.services.forum_history import probe_pages

    monkeypatch.setattr("app.services.forum_history.time.sleep", lambda x: None)

    def handler(req: httpx.Request) -> httpx.Response:
        k = int(req.url.path.rstrip("/").split("page-")[-1]) if "page-" in req.url.path else 1
        return httpx.Response(200, text=PAGES.get(k, PAGES[3]))

    with db_session() as s, httpx.Client(transport=httpx.MockTransport(handler)) as c:
        _inst(s)
        out = probe_pages(s, config, c, "FR0000000005", [1, 2, 9])
    assert out[0].startswith("page 1 : 2 sujet(s), 2 nouveau(x)")
    assert any(line.startswith("page 2 : 3 sujet(s), 2 nouveau(x)") for line in out)
    assert any("c-source__time : 99/99/9999" in line for line in out)  # the creation format, never a word
    assert "sujet" not in " ".join(x for x in out if x.startswith("    "))
