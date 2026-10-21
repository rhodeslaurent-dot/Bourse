"""docs/19 (A19.1–A19.4): read-only import of past newsletters from a Gmail mailbox."""

from __future__ import annotations

import argparse
import imaplib
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app import cli
from app.data.providers.imap_gmail import all_mail_folder, history_criteria
from app.db.models import (
    DecisionSnapshot,
    FeatureDaily,
    Instrument,
    MarketRegimeRow,
    NewsletterItem,
    PriceEod,
    Proposal,
    SignalOutcome,
    WatchlistRow,
)
from app.db.session import db_session

RAW = (Path(__file__).resolve().parents[1] / "fixtures" / "imap" / "momentum_sample.eml").read_bytes()
ASML = "NL0010273215"


class FakeGmail:
    log: list[tuple] = []

    def __init__(self, host):
        pass

    def login(self, u, p):
        FakeGmail.log.append(("login", u))

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\All \\HasNoChildren) "/" "[Gmail]/Tous les messages"']

    def select(self, box, readonly=False):
        FakeGmail.log.append(("select", box, readonly))
        return "OK", [b"1"]

    def search(self, charset, criteria):
        FakeGmail.log.append(("search", criteria))
        return "OK", [b"1"]

    def fetch(self, uid, what):
        FakeGmail.log.append(("fetch", what))
        head = b'1 (INTERNALDATE "15-Sep-2026 13:04:40 +0200" BODY[] {10}'
        body = RAW.split(b"\n\n", 1)[0] if b"HEADER.FIELDS" in what.encode() else RAW
        return "OK", [(head, body), b")"]

    def store(self, *a):
        raise AssertionError("a history import must never change a flag")

    def logout(self):
        pass


def args(**kw):
    base = dict(
        user="moi@gmail.com",
        query="from:momentum",
        source="momentum_capital",
        since=None,
        limit=None,
        dry_run=False,
        no_llm=True,
        params="config/params.example.yaml",
        calendars="config/market_calendars.yaml",
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_folder_and_criteria():
    assert (
        all_mail_folder([b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\All) "/" "[Gmail]/All Mail"'])
        == '"[Gmail]/All Mail"'
    )
    assert history_criteria('from:"momentum"', date(2025, 1, 2)) == 'X-GM-RAW "from:momentum" SINCE 02-Jan-2025'


@pytest.fixture
def gmail(monkeypatch):
    FakeGmail.log = []
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeGmail)
    monkeypatch.setenv("IMAP_HISTORY_PASSWORD", "abcd efgh ijkl mnop")


def test_dry_run_writes_nothing(db_engine, gmail, capsys):
    assert cli.cmd_newsletters_history(args(dry_run=True)) == 0
    out = capsys.readouterr().out
    assert "momentum@capital.fr" in out and "rien n'a été enregistré" in out and "15/09/2026" in out
    assert "abcd" not in out and "ASML" not in out  # no password, no letter content
    with db_session() as s:
        assert s.scalar(select(NewsletterItem)) is None
    assert ("select", '"[Gmail]/Tous les messages"', True) in FakeGmail.log


def test_import_dates_proposal_from_letter_and_computes_outcome(db_engine, gmail, calendars, capsys):
    cal = calendars.get("XAMS")
    days = cal.sessions_between(date(2026, 9, 1), date(2026, 10, 20))
    with db_session() as s:
        s.add(Instrument(isin=ASML, name="ASML", mic="XAMS", in_universe=True))
        for i, d in enumerate(days):
            px = 600 + i * 3
            s.add(
                PriceEod(
                    isin=ASML,
                    day=d,
                    open=px,
                    high=px + 4,
                    low=px - 4,
                    close=px + 1,
                    volume=1,
                    source="eodhd",
                    received_at=datetime(2026, 10, 20, tzinfo=UTC),
                    data_status="eod",
                    official=True,
                )
            )
        s.add(FeatureDaily(isin=ASML, day=date(2026, 9, 10), rs_rank=71, sessions=250))
        s.add(FeatureDaily(isin=ASML, day=date(2026, 9, 30), rs_rank=95, sessions=250))  # after the letter
        s.add(MarketRegimeRow(day=date(2026, 9, 11), ts=datetime(2026, 9, 11, tzinfo=UTC), regime="green"))
        s.add(MarketRegimeRow(day=date(2026, 10, 1), ts=datetime(2026, 10, 1, tzinfo=UTC), regime="red"))
    assert cli.cmd_newsletters_history(args()) == 0
    out = capsys.readouterr().out
    assert "1/1" in out and "propositions" in out
    with db_session() as s:
        p = s.scalar(select(Proposal).where(Proposal.isin == ASML))
        created = p.created_at if p.created_at.tzinfo else p.created_at.replace(tzinfo=UTC)
        assert created == datetime(2026, 9, 15, 11, 4, 40, tzinfo=UTC) and p.status == "expired"
        snap = s.scalar(select(DecisionSnapshot).where(DecisionSnapshot.proposal_id == p.id))
        assert snap.features["rs_rank"] == 71 and snap.regime["regime"] == "green"  # nothing after the letter
        assert "reconstitution" in snap.quotes and snap.portfolio_state == []
        assert s.scalar(select(WatchlistRow)) is None
        o = s.scalar(select(SignalOutcome).where(SignalOutcome.proposal_id == p.id))
        assert o is not None and o.status in ("target", "horizon", "not_filled", "running")
    assert not any(e[0] == "fetch" and "PEEK" not in e[1] for e in FakeGmail.log)
    # re-running imports nothing twice
    assert cli.cmd_newsletters_history(args()) == 0
    with db_session() as s:
        assert len(list(s.scalars(select(NewsletterItem)))) == 1
        assert len(list(s.scalars(select(Proposal).where(Proposal.isin == ASML)))) == 1
    assert "deja_presentes 1" in capsys.readouterr().out


def test_linked_page_values_are_ingested(db_engine, config, calendars):
    """A19.7: the page behind « Lire la suite » feeds the extraction; its status is traced."""
    import httpx

    from app.data.providers.imap_gmail import NewsletterMail
    from app.data.providers.linked_page import enrich_mail
    from app.db.models import NewsletterValue
    from app.services.newsletters import ingest_mail

    article = (
        "<article><p>ASML (NL0010273215) : achat, entrée à 640, objectif 700, stop 600.</p><p>"
        + "x " * 150
        + "</p></article>"
    )
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=article)), follow_redirects=False)
    mail = NewsletterMail(
        "zonebourse",
        "<zb1>",
        "Momentum",
        "noreply@zonebourse.com",
        None,
        datetime(2026, 10, 20, 8, tzinfo=UTC),
        "Trois valeurs sous surveillance.",
        "zbh1",
        '<a href="https://www.zonebourse.com/m/1">Lire la suite</a>',
    )
    mail, page = enrich_mail(mail, {"link_texts": ["Lire la suite"], "allowed_domains": ["zonebourse.com"]}, client=c)
    with db_session() as s:
        s.add(Instrument(isin=ASML, name="ASML", mic="XAMS", in_universe=True))
        s.flush()
        rep = ingest_mail(s, mail, config, None, None, calendars=calendars)
        item = s.scalar(select(NewsletterItem).where(NewsletterItem.dedup_hash == "zbh1"))
        assert rep.values >= 1 and item.parsed["page_liee"]["statut"] == "ok"
        v = s.scalar(select(NewsletterValue).where(NewsletterValue.isin == ASML))
        assert v.levels["achat"] == 640.0 and v.levels["stop"] == 600.0
