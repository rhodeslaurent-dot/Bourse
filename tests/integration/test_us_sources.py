"""Lot S5 (step 1 of the user's plan, 15/11/2026) — the US: SEC Form 4 and 8-K, FINRA short volume, US subreddits.
Synthetic documents in the documented formats, served by a fake reader; no network, no real issuer."""

from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.data.providers import finra_regsho, sec_edgar
from app.db.models import AttentionDaily, InsiderDeclaration, Instrument, MarketEvent, OfficialFiling, ShortVolumeDaily
from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services import event_journal, us_sources
from tests.unit.test_us_parsers import HEADER_8K, INDEX, form4

US = "US0000000AC1"  # Acme Corp, synthetic
NOW = datetime(2026, 11, 14, 6, 20, tzinfo=UTC)
TICKERS = {"0": {"cik_str": 1000001, "ticker": "ACME", "title": "Acme Corp"},
           "1": {"cik_str": 1000002, "ticker": "OTHR", "title": "Other Inc"}}  # fmt: skip


def _cfg(config, ua: str = "Test Bourse test@example.org"):
    c = copy.deepcopy(config)
    c.params.data.sec = {**(getattr(c.params.data, "sec", None) or {}), "user_agent": ua}
    return c


def _values(s) -> None:
    s.add(Instrument(isin=US, name="Acme Corp", mic="XNYS", currency="USD", in_universe=True, ticker_eodhd="ACME.US"))
    s.add(Instrument(isin="FR0000000EU1", name="Valeur européenne", mic="XPAR", currency="EUR", in_universe=True,
                     ticker_eodhd="EUV.PA"))  # fmt: skip
    s.flush()


class Reader:
    """A fake paced reader: the documents by URL, and the calls it served."""

    def __init__(self, docs: dict[str, str]) -> None:
        self.docs, self.calls = docs, []

    def __call__(self, url: str, max_bytes: int = 3_000_000, stop: str | None = None) -> str | None:
        self.calls.append((url, stop))
        return self.docs.get(url)


def _docs(f4: str | None = None) -> dict[str, str]:
    b = sec_edgar.BASE
    return {
        f"{b}/files/company_tickers.json": json.dumps(TICKERS),
        sec_edgar.index_url(date(2026, 11, 13)): INDEX,
        f"{b}/Archives/edgar/data/1000001/0001000001-26-000010.txt": HEADER_8K,
        f"{b}/Archives/edgar/data/1000001/0001000001-26-000011.txt": f4 or form4(),
    }


def test_form4_and_8k_of_the_followed_us_values_are_stored_without_any_name(db_engine, config):
    cfg = _cfg(config)
    r = Reader(_docs())
    with db_session() as s:
        _values(s)
        rep = us_sources.sec_load(s, cfg, date(2026, 11, 12), date(2026, 11, 13), r, NOW)
        again = us_sources.sec_load(s, cfg, date(2026, 11, 12), date(2026, 11, 13), Reader(_docs()), NOW)
        ins = s.scalar(select(InsiderDeclaration))
        fil = s.scalar(select(OfficialFiling))
        stored = json.dumps([{k: str(v) for k, v in vars(x).items() if not k.startswith("_")} for x in (ins, fil)])
    assert rep.form4_added == 1 and rep.form8k_added == 1 and rep.days_missing == [date(2026, 11, 12)]
    assert again.known == 2 and again.form4_added == again.form8k_added == 0  # idempotent: never fetched twice
    assert ins.source == "sec_form4" and ins.isin == US and ins.nature == "acquisition" and ins.role == "dirigeant"
    assert ins.amount == 20500.0 and ins.volume == 1000 and ins.price == 20.5 and ins.currency == "USD"
    assert ins.market_timestamp.replace(tzinfo=UTC) == datetime(2026, 11, 14, 1, 15, tzinfo=UTC)  # 20:15 New York
    assert "DOE" not in stored and "JANE" not in stored and "Chief" not in stored  # never the declarant's name
    assert fil.diffuser == "SEC" and fil.source == "sec_8k" and fil.family == "resultats" and fil.isin == US
    assert fil.market_timestamp.replace(tzinfo=UTC) == datetime(2026, 11, 13, 21, 30, 5, tzinfo=UTC)
    assert ("https://www.sec.gov/Archives/edgar/data/1000001/0001000001-26-000010.txt", "</SEC-HEADER>") in r.calls
    assert not any("1000002" in u for u, _ in r.calls)  # a company outside the followed values is never fetched


def test_us_filings_enter_the_journal_with_a_planned_sale_apart(db_engine, config):
    cfg = _cfg(config)
    with db_session() as s:
        _values(s)
        us_sources.sec_load(s, cfg, date(2026, 11, 13), date(2026, 11, 13), Reader(_docs(form4("S", plan="1"))), NOW)
        event_journal.collect(s, cfg, NOW - timedelta(days=3))
        kinds = {(e.source_key, e.kind, e.direction) for e in s.scalars(select(MarketEvent))}
    assert ("dirigeant:sec_form4", "dirigeant_cession_plan", -1) in kinds  # decided in advance: its own kind
    assert ("communique:sec_8k", "communique_resultats", None) in kinds


def test_finra_short_volume_is_stored_and_a_spike_becomes_an_event(db_engine, config):
    base = [date(2026, 10, 1) + timedelta(days=k) for k in range(40)]
    base = [d for d in base if d.weekday() < 5][:20]
    spike = date(2026, 11, 13)
    with db_session() as s:
        _values(s)
        for k, d in enumerate(base):
            s.add(ShortVolumeDaily(isin=US, day=d, symbol="ACME", short_volume=400 + (k % 3) * 10, total_volume=1000,
                                   short_exempt_volume=0, source="finra", received_at=NOW))  # fmt: skip
        text = f"Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n{spike:%Y%m%d}|ACME|800|0|1000|Q\n"
        text += f"{spike:%Y%m%d}|OTHR|10|0|100|Q\n"
        rep = us_sources.finra_load(s, spike, spike, Reader({finra_regsho.url(spike): text}), now=NOW)
        rep2 = us_sources.finra_load(s, spike, spike, Reader({finra_regsho.url(spike): text}), now=NOW)
        event_journal.collect(s, config, datetime(2026, 11, 12, tzinfo=UTC))
        ev = s.scalar(select(MarketEvent).where(MarketEvent.kind == "vad_volume_pic"))
    assert rep.short_rows == 1 and rep2.short_rows == 0  # only the followed value, once
    assert ev is not None and ev.isin == US and ev.direction == -1 and ev.strength > 3


def test_the_job_reads_finra_but_not_the_sec_without_the_user_s_agent(db_engine, config, calendars, monkeypatch):
    from app.scheduler.jobs import us_filings

    asked = []

    def fake_fetch(ua, interval, client=None, sleep=None):
        asked.append(ua)
        return Reader({})

    monkeypatch.setattr(us_filings, "http_fetch", fake_fetch)
    with db_session() as s:
        _values(s)
    ctx = JobContext(config, calendars, date(2026, 11, 16), (), "normal")
    us_filings.run(ctx)
    assert asked == [None]  # FINRA only: the SEC is never called without data.sec.user_agent
    assert "data.sec.user_agent à renseigner" in (ctx.note or "")
    ctx2 = JobContext(_cfg(config), calendars, date(2026, 11, 16), (), "normal")
    us_filings.run(ctx2)
    assert asked[1:] == ["Test Bourse test@example.org", None]


def test_us_subreddits_count_cashtags_under_their_own_source(db_engine, config, monkeypatch):
    from app.services import social as svc

    day = date(2026, 11, 14)
    ts = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
    texts = {"stocks": [(ts, "Loading up on $ACME before earnings"), (ts, "$ZZZZ to the moon")],
             "vosfinances": [(ts, "Rien sur la Valeur européenne aujourd'hui")]}  # fmt: skip
    monkeypatch.setenv("REDDIT_CLIENT_ID", "x")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "y")
    monkeypatch.setattr(svc.social, "reddit_token", lambda *a: "t")
    monkeypatch.setattr(svc.social, "reddit_listing", lambda sub, kind, tok, cl: texts.get(sub, []) if kind == "new"
                        else [])  # fmt: skip
    monkeypatch.setattr(svc.time, "sleep", lambda _: None)
    cfg = copy.deepcopy(config)
    cfg.params.weak_signals["social"]["reddit"].update({"subreddits": ["vosfinances"], "subreddits_us": ["stocks"]})
    with db_session() as s:
        _values(s)
        svc.collect_reddit(s, cfg, None, day)  # type: ignore[arg-type]
        rows = {(r.isin, r.source): r.value for r in s.scalars(select(AttentionDaily))}
    assert rows[(US, "reddit_us")] == 1  # « $ACME »; an unknown cashtag is ignored
    assert (US, "reddit") not in rows  # the US group never writes into the European source
    assert svc.cashtags("buy $acme and $ACME, not ACME$", {"ACME": US}) == [US]


def test_an_old_params_yaml_gets_the_new_settings_of_step_1_on_update(tmp_path):
    """The PC's params.yaml predates lots S1 to S5: ``params-init --update`` adds the new groups, keys and jobs, and
    ``--set`` writes the user's own SEC agent (never written in a tracked file)."""
    import re
    from pathlib import Path

    import yaml

    from app.cli import init_params

    example = Path("config/params.example.yaml")
    text = example.read_text(encoding="utf-8")
    a, b = text.index("\nevent_journal:"), text.index("\nweak_signals:")
    old = text[:a] + text[b:]
    old = re.sub(r"\n  sec:.*?(?=\n  finra:)", "", old, flags=re.S)
    old = re.sub(r"\n  finra: .*", "", old)
    old = re.sub(r"\n      subreddits_us: .*", "", old)
    old = re.sub(r"\n  (us_filings|sources_daily|consensus_daily|filings_daily): .*", "", old)
    old = old.replace('cron: "10 5 * * *", misfire_grace_min: 600', 'cron: "0 9 * * 0", misfire_grace_min: 1440')
    target = tmp_path / "params.yaml"
    target.write_text(old, encoding="utf-8")
    assert "subreddits_us" not in old and "\n  sec:" not in old
    init_params(str(example), str(target), "1", "x@example.org", update=True,
                sets=["data.sec.user_agent=Prénom Nom moi@example.org"])  # fmt: skip
    new = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert new["event_journal"]["horizons"] == [1, 5, 20]
    assert new["data"]["sec"]["user_agent"] == "Prénom Nom moi@example.org"
    assert new["data"]["finra"]["lookback_days"] == 5
    assert "stocks" in new["weak_signals"]["social"]["reddit"]["subreddits_us"]
    for job in ("us_filings", "sources_daily", "consensus_daily", "filings_daily"):
        assert job in new["jobs"]
    assert new["jobs"]["fundamentals_refresh"]["cron"] == "10 5 * * *"


def test_the_amf_studies_never_read_the_us_filings(db_engine, config):
    """The French flow's re-familying and coverage stay on the AMF rows: a US 8-K keeps its family."""
    from app.services import filings

    with db_session() as s:
        _values(s)
        us_sources.sec_load(s, _cfg(config), date(2026, 11, 13), date(2026, 11, 13), Reader(_docs()), NOW)
        filings.refamily(s)
        fil = s.scalar(select(OfficialFiling).where(OfficialFiling.diffuser == "SEC"))
        cov = filings.coverage(s, config, date(2026, 11, 1), date(2026, 11, 30))
    assert fil.family == "resultats"
    assert "0 communiqué(s) en base" in cov and "ISIN valide : 0" in cov  # the AMF archive alone


def test_check_config_db_says_which_base_is_read_and_what_it_holds(db_engine):
    from app.cli import db_summary

    with db_session() as s:
        _values(s)
    lines = db_summary()
    assert lines[0].startswith("base : sqlite") and "base SQLite locale par défaut" in lines[0]
    assert any(x.strip().startswith("instruments") and x.strip().endswith("2 ligne(s)") for x in lines)


def test_us_check_sample_decodes_real_shaped_filings_without_any_name():
    from app.cli import sec_sample

    rows = sec_edgar.parse_master_index(INDEX)
    docs = _docs()
    out = sec_sample(rows, 1, Reader(docs))
    assert any(x.startswith("  Form 4 0001000001-26-000011 · ACME") and "rôle dirigeant" in x for x in out)
    assert any(x.startswith("  8-K 0001000001-26-000010") and "→ resultats" in x for x in out)
    assert "DOE" not in "\n".join(out) and "JANE" not in "\n".join(out)


def test_require_postgres_stops_a_load_on_the_sqlite_fallback(db_engine, monkeypatch, capsys):
    import argparse

    from app.cli import cmd_check_config

    monkeypatch.delenv("DATABASE_URL", raising=False)
    code = cmd_check_config(argparse.Namespace(params="config/params.example.yaml", db=False, require_postgres=True))
    assert code == 2 and "ARRÊT : la base lue n'est pas Postgres" in capsys.readouterr().out


def test_finra_reads_nothing_when_no_us_value_is_followed(db_engine):
    r = Reader({})
    with db_session() as s:
        us_sources.finra_load(s, date(2026, 11, 2), date(2026, 11, 13), r, now=NOW)
    assert r.calls == []


def test_a_backfill_sets_its_own_filing_budget(db_engine, config):
    cfg = _cfg(config)
    with db_session() as s:
        _values(s)
        capped = us_sources.sec_load(s, cfg, date(2026, 11, 13), date(2026, 11, 13), Reader(_docs()), NOW,
                                     max_filings=1)  # fmt: skip
    assert capped.truncated and capped.form4_added + capped.form8k_added == 1


def test_an_older_params_yaml_still_checks_the_recent_groups(config):
    """16/11/2026: the production params.yaml (older config_validation) listed 12 features out of 20."""
    import copy

    from app.config.loader import validate_params

    raw = copy.deepcopy(config.raw)
    raw["config_validation"]["optional_groups"] = ["tax", "llm"]
    keys = {f.key for f in validate_params(raw).features}
    assert {"admission", "market_gate", "conjuncture", "events", "entry", "zones", "ranking", "pockets"} <= keys
