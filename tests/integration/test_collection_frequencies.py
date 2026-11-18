"""Lot S4 (step 1 of the user's plan, 15/11/2026) — collection frequencies: the analysts' consensus every evening,
the fundamentals of the whole universe by daily rotation, the AMF filings every evening, the news until 22:00 Paris.
Values and figures are synthetic fixtures; no network."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select

from app.db.models import AnalystConsensus, Fundamentals, Instrument
from app.db.session import db_session
from app.scheduler.runner import JobContext

NOW = datetime(2026, 11, 16, 21, 0, tzinfo=UTC)


def _insts(s, n: int) -> None:
    for k in range(n):
        s.add(Instrument(isin=f"FR00000001{k:02d}", name=f"Valeur {k}", mic="XPAR", currency="EUR", in_universe=True,
                         ticker_eodhd=f"V{k}.PA"))  # fmt: skip
    s.flush()


def test_fundamentals_rotate_over_the_whole_universe_oldest_first(db_engine):
    from app.services.fundamentals import refresh

    info = {"trailingPE": 12.0, "currency": "EUR"}
    with db_session() as s:
        _insts(s, 5)
        s.add(Fundamentals(isin="FR0000000100", day=date(2026, 11, 1), source="yahoo", symbol="V0.PA", data=info,
                           received_at=NOW))  # fmt: skip
        s.add(Fundamentals(isin="FR0000000101", day=date(2026, 10, 1), source="yahoo", symbol="V1.PA", data=info,
                           received_at=NOW))  # fmt: skip
        s.flush()
        first: list[str] = []
        refresh(s, date(2026, 11, 16), 3, info_fn=lambda sym: first.append(sym) or info, sleep=lambda _: None)
        second: list[str] = []
        refresh(s, date(2026, 11, 17), 3, info_fn=lambda sym: second.append(sym) or info, sleep=lambda _: None)
    assert first == ["V2.PA", "V3.PA", "V4.PA"]  # never read first
    assert second == ["V1.PA", "V0.PA", "V2.PA"]  # then the oldest reading: 01/10 before 01/11 before 16/11


def test_the_consensus_is_stored_every_evening_from_the_stored_tickers(db_engine, config, calendars, monkeypatch):
    from app.scheduler.jobs import consensus_daily

    seen = []

    def fake_enrich(rows, cfg):  # the delayed screener, offline
        seen.extend(r.code + "." + r.exchange for r in rows)
        for r in rows[:2]:
            r.extra["analystes_tv"] = {"nb_analystes": 12, "objectif_moyen": 50.0, "note": "achat"}
        return rows

    monkeypatch.setattr(consensus_daily, "enrich_with_screener", fake_enrich)
    with db_session() as s:
        _insts(s, 3)
        s.add(Instrument(isin="FR0000000199", name="Sans ticker", mic="XPAR", currency="EUR", in_universe=True))
    ctx = JobContext(config, calendars, date(2026, 11, 16), ("XPAR",), "normal")
    assert consensus_daily.run(ctx) == 2
    with db_session() as s:
        rows = list(s.scalars(select(AnalystConsensus)))
    assert sorted(seen) == ["V0.PA", "V1.PA", "V2.PA"]  # a value without a stored ticker is skipped
    assert len(rows) == 2 and rows[0].day == date(2026, 11, 16) and rows[0].data["objectif_moyen"] == 50.0
    assert "2 consensus enregistré(s) sur 3 valeur(s)" in (ctx.note or "")
    assert consensus_daily.run(ctx) == 2  # the same evening again: updated, never doubled
    with db_session() as s:
        assert len(list(s.scalars(select(AnalystConsensus)))) == 2


def test_the_consensus_note_says_why_nothing_was_stored(db_engine, config, calendars, monkeypatch):
    """17/11/2026: « 0 consensus enregistré(s) sur 4799 valeur(s) » for days, without a reason."""
    from app.scheduler.jobs import consensus_daily

    def found_without_values(rows, cfg):  # the screener answers, the analyst columns are empty
        for r in rows:
            r.tv_ticker = "EURONEXT:" + r.code
        return rows

    monkeypatch.setattr(consensus_daily, "enrich_with_screener", found_without_values)
    with db_session() as s:
        _insts(s, 2)
    ctx = JobContext(config, calendars, date(2026, 11, 16), ("XPAR",), "normal")
    assert consensus_daily.run(ctx) == 0
    assert "2 valeur(s) trouvée(s) dans le screener ; aucune valeur numérique dans les colonnes" in ctx.note
    assert "price_target_average" in ctx.note
    config.params.data.tradingview_screener.pop("analyst_columns")
    consensus_daily.run(ctx)
    assert "colonnes d'analystes absentes de params.yaml" in ctx.note


def test_the_filings_are_read_every_evening_over_the_last_days(db_engine, config, calendars, monkeypatch):
    from app.scheduler.jobs import filings_daily
    from app.services.filings import FilingsReport

    asked = []

    def fake_archive(s, cfg, start, end):
        asked.append((start, end))
        return FilingsReport(source="info_financiere", read=4, added=3)

    monkeypatch.setattr(filings_daily, "archive_api", fake_archive)
    ctx = JobContext(config, calendars, date(2026, 11, 16), (), "normal")
    assert filings_daily.run(ctx) == 3
    assert asked == [(date(2026, 11, 13), date(2026, 11, 16))]  # data.filings.daily_lookback_days = 3


def test_the_new_jobs_are_scheduled_and_the_old_defaults_are_moved_on_update(config):
    from app import cli
    from app.scheduler.registry import IMPLEMENTED_JOBS

    specs = config.params.jobs.specs()
    for name in ("consensus_daily", "filings_daily", "sources_daily"):
        assert name in IMPLEMENTED_JOBS and specs[name].enabled and not specs[name].market_days_only
    assert specs["fundamentals_refresh"].cron == "10 5 * * *"
    assert specs["news_scan"].day["cron"] == "*/15 9-21 * * 1-5"  # until 22:00 Paris (New York session)
    old = (
        "jobs:\n"
        '  news_scan:\n    premarket: {cron: "*/5 7-8 * * 1-5", market_days_only: true}\n'
        '    day: {cron: "*/15 9-18 * * 1-5", market_days_only: true}\n    misfire_grace_min: 5\n'
        '  fundamentals_refresh: {cron: "0 9 * * 0", misfire_grace_min: 1440, market_days_only: false}   # x\n'
        '  weekly_review: {cron: "0 8 * * 6"}\n'
        "\nworkflow:\n"
    )
    out, moved = cli.update_default_schedules(old)
    assert moved == ["fundamentals_refresh", "news_scan.day"]
    assert 'fundamentals_refresh: {cron: "10 5 * * *"' in out and "# x" in out
    assert 'day: {cron: "*/15 9-21 * * 1-5"' in out and 'weekly_review: {cron: "0 8 * * 6"}' in out
    assert cli.update_default_schedules(out) == (out, [])  # idempotent
    mine = old.replace('"0 9 * * 0"', '"0 7 * * 6"')  # a schedule the user chose is kept
    assert "fundamentals_refresh" not in cli.update_default_schedules(mine)[1]


def test_the_consensus_note_gives_the_coverage_per_exchange():
    from app.scheduler.jobs.consensus_daily import coverage
    from app.services.universe import SymbolRow

    rows = [SymbolRow(isin=f"X{k}", name="n", code=f"C{k}", exchange=ex, currency="EUR") for k, ex in
            enumerate(["PA", "PA", "US", "US", "US", "XETRA"])]  # fmt: skip
    rows[0].tv_ticker = rows[1].tv_ticker = rows[2].tv_ticker = "T"
    for r in rows:
        r.extra["kind"] = "etf" if r.code == "C4" else "stock"
    assert coverage(rows) == (
        " ; couverture par place : XETRA 0/1, US 1/3"  # least covered first; PA is complete
        " ; XETRA non trouvées (stock 1) : C5 ; US non trouvées (etf 1, stock 1) : C3, C4"
    )
    for r in rows:
        r.tv_ticker = "T"
    assert coverage(rows) == ""


def test_the_coverage_says_when_the_screener_was_cut():
    from app.scheduler.jobs.consensus_daily import coverage
    from app.services.universe import SymbolRow

    rows = [SymbolRow(isin=f"X{k}", name="n", code=f"C{k}", exchange="US", currency="USD") for k in range(2)]
    rows[0].tv_ticker = "T"
    for r in rows:
        r.extra["screener_tronque"] = (1500, 6200)
    assert coverage(rows) == " ; couverture par place : US 1/2 (source lue 1500/6200 lignes)"


def test_the_note_names_stocks_first_and_values_found_without_consensus(config):
    """18/11/2026: a large American value's consensus stayed 23 days old — the note names the stocks the screener
    missed before the ETFs (which have no analysts), and the values found without any analyst figure."""
    from app.scheduler.jobs.consensus_daily import coverage, diagnosis
    from app.services.universe import SymbolRow

    rows = [SymbolRow(isin=f"X{k}", name="n", code=f"C{k}", exchange="US", currency="USD") for k in range(4)]
    rows[0].extra["kind"], rows[1].extra["kind"], rows[2].extra["kind"] = "etf", "stock", "stock"
    rows[3].extra["kind"] = "stock"
    rows[2].tv_ticker = rows[3].tv_ticker = "T"
    rows[3].extra["analystes_tv"] = {"objectif_moyen": 10.0}
    assert coverage(rows).endswith("US non trouvées (etf 1, stock 1) : C1, C0")
    assert "trouvées sans consensus (1) : C2.US" in diagnosis(config, rows, 1)
