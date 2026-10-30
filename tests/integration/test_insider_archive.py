"""docs/34 §34.8 — archive of the AMF managers' dealing declarations: idempotent storage, ISIN from the
declaration or by exact normalised name, completion after a later PDF read, coverage, job registration."""

from __future__ import annotations

import importlib
import json
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.data.providers.amf_bdif import LIST_PATH
from app.db.models import InsiderDeclaration, Instrument
from app.db.session import db_session
from app.services import insiders

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "amf_bdif"
PDFS = {"AAAA0001": "declaration_buy.txt", "BBBB0002": "declaration_linked_sale.txt"}


def _fetcher(calls: list[str]):
    payload = (FIX / "listing_synthetic.json").read_bytes()

    def fetch(url, params):
        calls.append(url)
        if url.endswith(LIST_PATH):
            return payload if params["From"] == 0 else json.dumps({"result": []}).encode()
        assert "-en.pdf" not in url  # the French document is read
        return next((k for k in PDFS if k in url), "unreadable").encode()

    return fetch


def _pdf_text(content: bytes) -> str | None:
    name = PDFS.get(content.decode())
    return (FIX / name).read_text(encoding="utf-8") if name else None


def test_archive_is_idempotent_maps_isins_and_completes_later(db_engine, config):
    with db_session() as s:
        s.add(Instrument(isin="FR0000000AA1", name="Société Exemple SA", mic="XPAR", in_universe=True))
        s.add(Instrument(isin="FR0000000BB2", name="Fictive Industries", mic="XPAR", in_universe=True))
        s.add(Instrument(isin="FR0000000CC3", name="Inconnue", mic="XPAR"))  # « Inconnue Holding » ≠ « Inconnue »
        s.flush()
        calls: list[str] = []
        args = dict(fetch=_fetcher(calls), sleep=lambda x: None)

        # first pass without a PDF extractor: listing only, figures empty, ISIN by exact normalised name
        rep = insiders.archive(s, config, date(2026, 10, 27), date(2026, 10, 28), pdf_text=None, **args)
        assert (rep.listed, rep.added, rep.completed, rep.pdf_read) == (3, 3, 0, 0)
        rows = {r.doc_id: r for r in s.scalars(select(InsiderDeclaration))}
        assert all(
            r.source == "amf_bdif" and r.parse_status == "listing_only" and r.price is None for r in rows.values()
        )
        assert rows["2026DD9000001"].isin == "FR0000000AA1" and rows["2026DD9000001"].isin_source == "name"
        assert rows["2026DD9000002"].isin == "FR0000000BB2"  # « Fictive Industries S.A. » → legal form removed
        assert rows["2026DD9000003"].isin is None  # unmatched, kept
        assert all(r.received_at is not None and r.processed_at is not None for r in rows.values())

        # same window again: nothing added
        again = insiders.archive(s, config, date(2026, 10, 27), date(2026, 10, 28), pdf_text=None, **args)
        assert (again.added, again.completed) == (0, 0)

        # later with an extractor: the rows are completed, never duplicated
        calls.clear()
        rep = insiders.archive(s, config, date(2026, 10, 27), date(2026, 10, 28), pdf_text=_pdf_text, **args)
        assert (rep.added, rep.completed, rep.parsed) == (0, 3, 2)  # the third PDF is unreadable: marked so
        assert len(list(s.scalars(select(InsiderDeclaration)))) == 3
        buy = s.scalar(select(InsiderDeclaration).where(InsiderDeclaration.doc_id == "2026DD9000001"))
        assert buy.parse_status == "parsed" and buy.role == "PDG" and buy.nature == "acquisition"
        assert buy.volume == 2500 and abs(buy.price - 12.68) < 1e-9 and buy.n_transactions == 2
        assert buy.isin == "FR0000000AA1" and buy.isin_source == "name"  # a filled field is never rewritten
        unread = s.scalar(select(InsiderDeclaration).where(InsiderDeclaration.doc_id == "2026DD9000003"))
        assert unread.parse_status == "pdf_unrecognised" and unread.nature is None
        assert len(buy.transactions) == 2 and buy.transactions[0]["tx_date"] == "2026-10-23"
        sale = s.scalar(select(InsiderDeclaration).where(InsiderDeclaration.doc_id == "2026DD9000002"))
        assert sale.role == "personne liée" and sale.related_role == "administrateur" and sale.nature == "cession"
        stored = json.dumps(
            [{c.name: getattr(r, c.name) for c in InsiderDeclaration.__table__.columns} for r in rows.values()],
            default=str,
        )
        assert "Personne" not in stored and "Imaginaire" not in stored and "Patrimoine" not in stored  # PRIVACY

        # a parsed declaration's PDF is never downloaded again
        calls.clear()
        insiders.archive(s, config, date(2026, 10, 27), date(2026, 10, 28), pdf_text=_pdf_text, **args)
        assert not any("AAAA0001" in c or "BBBB0002" in c for c in calls)

        cov = insiders.coverage(s)
        assert (
            "2026-10 :     3 déclarations ·     2 chiffrées ·     1 acquisitions ·     2 avec ISIN (2 dans l'univers)"
            in cov
        )


def test_an_isin_given_by_the_declaration_wins_over_the_name(db_engine, config):
    with db_session() as s:
        s.add(Instrument(isin="FR0000000ZZ9", name="Societe Exemple", mic="XPAR"))  # same name, other ISIN
        s.flush()
        insiders.archive(s, config, date(2026, 10, 27), date(2026, 10, 28), fetch=_fetcher([]), pdf_text=_pdf_text,
                         sleep=lambda x: None)  # fmt: skip
        buy = s.scalar(select(InsiderDeclaration).where(InsiderDeclaration.doc_id == "2026DD9000001"))
        assert buy.isin == "FR0000000AA1" and buy.isin_source == "declaration"


def test_long_periods_are_read_window_by_window(db_engine, config):
    seen: list[tuple[str, str]] = []

    def fetch(url, params):
        seen.append((params["DateDebut"][:10], params["DateFin"][:10]))
        return b'{"result": []}'

    with db_session() as s:
        rep = insiders.archive(s, config, date(2026, 1, 1), date(2026, 3, 15), fetch=fetch, pdf_text=None,
                               sleep=lambda x: None)  # fmt: skip
        assert "aucune déclaration archivée" in insiders.coverage(s)
    assert rep.listed == 0 and len(seen) == 3  # 31-day windows (data.insiders.window_days)
    assert seen[0] == ("2025-12-31", "2026-01-31") and seen[-1][1] == "2026-03-15"


def test_the_job_is_registered_and_declared():
    from app.scheduler.registry import IMPLEMENTED_JOBS

    assert IMPLEMENTED_JOBS["insider_archive"] == "app.scheduler.jobs.insider_archive:run"
    assert callable(importlib.import_module("app.scheduler.jobs.insider_archive").run)


def test_the_job_archives_the_look_back_window(db_engine, config, calendars, monkeypatch):
    from app.scheduler.jobs import insider_archive
    from app.scheduler.runner import JobContext

    got: dict = {}

    def fake_archive(s, cfg, start, end, **kw):
        got.update(start=start, end=end)
        return insiders.ArchiveReport(listed=4, added=2)

    monkeypatch.setattr(insider_archive, "archive", fake_archive)
    ctx = JobContext(config=config, calendars=calendars, run_date=date(2026, 10, 29), open_mics=(), variant="")
    assert insider_archive.run(ctx) == 2
    assert got == {"start": date(2026, 10, 22), "end": date(2026, 10, 29)}  # data.insiders.lookback_days: 7
    assert ctx.note and "2 ajoutée(s)" in ctx.note
    assert config.params.jobs.specs()["insider_archive"].market_days_only is False


def test_reparse_reads_again_the_declarations_whose_nature_was_not_recognised(db_engine, config):
    """PC archive of 30/10/2026: 24 348 declarations parsed with their nature empty or shifted into « mixte ».
    ``--reparse`` downloads those PDFs again and replaces their fields; a recognised one is never downloaded."""
    with db_session() as s:
        calls: list[str] = []
        args = dict(fetch=_fetcher(calls), sleep=lambda x: None)
        insiders.archive(s, config, date(2026, 10, 27), date(2026, 10, 28), pdf_text=_pdf_text, **args)
        buy = s.scalar(select(InsiderDeclaration).where(InsiderDeclaration.doc_id == "2026DD9000001"))
        sale = s.scalar(select(InsiderDeclaration).where(InsiderDeclaration.doc_id == "2026DD9000002"))
        buy.nature, buy.transactions, buy.tx_date = None, [{"nature": None}], None  # as the former parser left it
        s.flush()
        calls.clear()
        rep = insiders.archive(s, config, date(2026, 10, 27), date(2026, 10, 28), pdf_text=_pdf_text, reparse=True,
                               **args)  # fmt: skip
        assert any("AAAA0001" in c for c in calls) and not any("BBBB0002" in c for c in calls)
        assert rep.completed >= 1 and buy.nature == "acquisition" and buy.tx_date == date(2026, 10, 23)
        assert len(buy.transactions) == 2 and sale.nature == "cession"
