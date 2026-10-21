"""RSS and newsletter parsers on anonymised fixtures; malformed feed raises (never silent)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.data.providers.imap_gmail import extract_values, parse_eml, source_for
from app.data.providers.rss import parse_feed

FX = Path(__file__).resolve().parents[1] / "fixtures"
SENDERS = {
    "zonebourse": ["@zonebourse.com", "@marketscreener.com"],
    "momentum_capital": ["@capital.fr", "@prismamedia"],
}


def test_parse_rss_fixture():
    items = parse_feed("actusnews", (FX / "rss" / "actusnews_sample.xml").read_bytes())
    assert len(items) == 3 and items[0].guid == "an-1001"
    assert items[0].published_at == datetime(2026, 9, 15, 5, 32, tzinfo=UTC)
    assert "FR0000120271" in items[0].body and "<p>" not in items[0].body
    assert items[0].dedup_hash != items[1].dedup_hash


def test_malformed_feed_raises():
    with pytest.raises(ValueError):
        parse_feed("x", (FX / "rss" / "malformed.xml").read_bytes())


def test_parse_eml_zonebourse_and_extract():
    mail = parse_eml(
        (FX / "imap" / "zonebourse_sample.eml").read_bytes(),
        SENDERS,
        received_at=datetime(2026, 9, 15, 8, 3, tzinfo=UTC),
    )
    assert mail.source == "zonebourse" and "Sélection du jour" in mail.subject
    assert mail.sent_at == datetime(2026, 9, 15, 8, 2, 11, tzinfo=UTC) and mail.received_at.hour == 8
    vals = extract_values(mail.body_text, {"sanofi": "FR0000120578"})
    by = {v.isin: v for v in vals}
    assert by["FR0000120271"].direction == "buy" and by["FR0000120271"].levels == {"objectif": 60.0, "stop": 52.5}
    assert by["FR0000120073"].direction == "watch" and by["FR0000120073"].levels["resistance"] == 180.0
    assert by["FR0000120578"].direction == "sell" and by["FR0000120578"].name == "sanofi"


def test_parse_eml_html_momentum_and_unrelated():
    mail = parse_eml((FX / "imap" / "momentum_sample.eml").read_bytes(), SENDERS)
    assert mail.source == "momentum_capital" and "<p>" not in mail.body_text
    vals = extract_values(mail.body_text, {"sap": "DE0007164600"})
    asml = next(v for v in vals if v.isin == "NL0010273215")
    assert (
        asml.direction == "buy"
        and asml.levels["achat"] == 640.0
        and asml.levels["objectif"] == 700.0
        and asml.levels["invalidation"] == 600.0
    )
    assert parse_eml((FX / "imap" / "unrelated_sample.eml").read_bytes(), SENDERS) is None
    assert source_for("Foo <x@marketscreener.com>", SENDERS) == "zonebourse"


def test_search_criteria_only_known_senders():
    from datetime import date

    from app.data.providers.imap_gmail import search_criteria

    c = search_criteria(
        {"zonebourse": ["@zonebourse.com"], "momentum": ["@capital.fr", "@prismamedia"]}, date(2026, 10, 19)
    )
    assert c == 'UNSEEN SINCE 19-Oct-2026 OR OR FROM "capital.fr" FROM "prismamedia" FROM "zonebourse.com"'


def test_shared_mailbox_other_mails_untouched(monkeypatch):
    """A mailbox shared with other uses: mails are peeked, only newsletters get the \\Seen flag."""
    import imaplib

    from app.data.providers.imap_gmail import GmailImap

    raws = {
        b"1": (FX / "imap" / "zonebourse_sample.eml").read_bytes(),
        b"2": (FX / "imap" / "unrelated_sample.eml").read_bytes(),
    }
    log: list[tuple] = []

    class FakeImap:
        def __init__(self, host):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            log.append(("login", u))

        def select(self, box):
            log.append(("select", box))
            return "OK", [b"2"]

        def search(self, charset, criteria):
            log.append(("search", criteria))
            return "OK", [b"1 2"]

        def fetch(self, uid, what):
            log.append(("fetch", uid, what))
            return "OK", [(b'%s (INTERNALDATE "20-Oct-2026 09:31:00 +0200" BODY[] {10}' % uid, raws[uid]), b")"]

        def store(self, uid, op, flag):
            log.append(("store", uid, flag))

    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeImap)
    mails = GmailImap("imap.gmail.com", "u", "p").fetch_unseen({"zonebourse": ["@zonebourse.com"]}, mailbox="Bourse")
    assert [m.source for m in mails] == ["zonebourse"]
    assert ("select", "Bourse") in log and all("PEEK" in e[2] for e in log if e[0] == "fetch")
    assert [e for e in log if e[0] == "store"] == [("store", b"1", "\\Seen")]
    assert next(e for e in log if e[0] == "search")[1].startswith("UNSEEN SINCE")
