"""29/10/2026: the referential's tickers (SUR, AUX, PAS, MON, LIEN) are never searched in a letter; its names are,
with their legal form dropped."""

from __future__ import annotations

from app.data.providers.imap_gmail import extract_values
from app.db.models import Instrument, InstrumentAlias
from app.db.session import db_session
from app.domain.names import resolve
from app.services.news import alias_index, attach_instruments, name_index

LETTER = (
    "Viridien : sortie par le haut ?\n\nLa clôture s'est faite sur le haut du range, pas encore validée. Voir le "
    "lien. Mon avis : Sartorius Stedim Biotech reste aux portes de sa résistance.\n\nAtos Group : retour."
)


def test_letter_words_are_not_tickers(db_engine):
    with db_session() as s:
        for isin, name, mic, code in (
            ("DE0005176903", "Surteco Group SE", "XETR", "SUR"),
            ("PLAXILA00018", "Auxilia SA", "XWAR", "AUX"),
            ("PLPSSUS00018", "Passus S.A.", "XWAR", "PAS"),
            ("US8281741020", "Chicago Atlantic BDC, Inc.", "XNAS", "LIEN"),
            ("FR001400PVN6", "Viridien", "XPAR", "VIRI"),
            ("FR0013154002", "Sartorius Stedim Biotech S.A.", "XPAR", "DIM"),
            ("FR0000051732", "Atos SE", "XPAR", "ATO"),
            ("FR0000133308", "Orange SA", "XPAR", "ORA"),
        ):
            s.add(Instrument(isin=isin, name=name, mic=mic, in_universe=True))
            s.add(InstrumentAlias(isin=isin, provider="eodhd", alias=code))
        s.flush()
        aliases, names = alias_index(s), name_index(s)
    got = {v.isin for v in extract_values(LETTER, aliases)}
    assert got == {"FR001400PVN6", "FR0013154002", "FR0000051732"}
    assert attach_instruments("Résultats : Orange relève sa prévision, rien sur Surteco", aliases) == ["DE0005176903"]
    assert resolve("Orange", names) == "FR0000133308"  # a name given by the reader: common words too
    assert resolve("Atos Group", names) == "FR0000051732" and resolve("SUR", names) is None


def test_the_recheck_withdraws_what_a_word_created(db_engine):
    from datetime import UTC, date, datetime

    from app.db.models import NewsletterItem, NewsletterValue, Proposal, WatchlistRow
    from app.services.decisions import create_proposal, record_signal
    from app.services.mentions_recheck import recheck

    t = datetime(2026, 10, 29, 10, 52, tzinfo=UTC)
    with db_session() as s:
        s.add(Instrument(isin="DE0005176903", name="Surteco Group SE", mic="XETR"))
        s.add(Instrument(isin="FR001400PVN6", name="Viridien", mic="XPAR"))
        it = NewsletterItem(source="momentum_capital", message_id="m", dedup_hash="h", received_at=t,
                            subject="Viridien : sortie par le haut ?", body_text=LETTER)  # fmt: skip
        s.add(it)
        s.flush()
        pids = {}
        for isin, word in (("DE0005176903", "sur"), ("FR001400PVN6", "viridien")):
            sig = record_signal(s, isin, "external", "momentum_capital", t, "eod", "delayed", "t", evidence={})
            s.add(NewsletterValue(item_id=it.id, isin=isin, name=word, direction="buy", signal_id=sig.id))
            s.add(WatchlistRow(isin=isin, reason="external", source="momentum_capital", active=True))
            pids[isin] = create_proposal(s, sig, "WATCH", None, {"blocked": []}, "t", created_at=t).id
        s.flush()
        dry = recheck(s, date(2026, 10, 26), False, t)
        assert dry.proposals == [pids["DE0005176903"]] and dry.watchlist == ["DE0005176903"]
        assert s.get(Proposal, pids["DE0005176903"]).deleted_at is None  # a dry run changes nothing
        recheck(s, date(2026, 10, 26), True, t)
        assert s.get(Proposal, pids["DE0005176903"]).deleted_at is not None
        assert s.get(Proposal, pids["FR001400PVN6"]).deleted_at is None
        active = {w.isin for w in s.query(WatchlistRow).filter(WatchlistRow.active.is_(True))}
        assert active == {"FR001400PVN6"}
