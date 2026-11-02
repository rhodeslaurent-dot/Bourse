"""Lot M0 (docs/39): every instrument kind the code writes fits the column (« crypto_etp » is 10 characters; the column
was 8, PostgreSQL would have refused the universe refresh of a configured crypto ETP)."""

from __future__ import annotations

from app.db.models import Instrument


def test_every_kind_written_by_the_code_fits_the_column():
    width = Instrument.__table__.c.kind.type.length
    for kind in ("stock", "etf", "crypto_etp"):
        assert len(kind) <= width, kind


def test_a_crypto_etp_is_stored_and_read_back(db_engine):
    from app.db.session import db_session

    with db_session() as s:
        s.add(Instrument(isin="XS0000000001", name="ETP fictif", mic="XETR", currency="EUR", kind="crypto_etp"))
    with db_session() as s:
        assert s.get(Instrument, "XS0000000001").kind == "crypto_etp"
