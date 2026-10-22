"""Values the engine writes must fit their PostgreSQL columns (SQLite in tests never enforces VARCHAR widths)."""

from app.db.models import Proposal, Signal, Trade
from app.domain.detectors import DETECTORS


def _width(column) -> int:
    return column.property.columns[0].type.length


def test_detector_names_fit_signal_and_trade_columns():
    names = [*DETECTORS, "external"]
    for col in (Signal.detector, Trade.detector):
        too_long = [n for n in names if len(n) > _width(col)]
        assert not too_long, f"{col}: {too_long}"


def test_engine_labels_fit_their_columns():
    assert len("moteur_lettre") <= _width(Signal.source)
    for action in ("BUY", "WATCH", "HOLD", "SELL", "REDUCE", "NO_TRADE"):
        assert len(action) <= _width(Proposal.action)
    for acc in ("pea", "cto_cash", "cto_srd"):
        assert len(acc) <= _width(Proposal.account_recommended)
    for status in ("realtime", "delayed", "eod", "stale", "unavailable"):
        assert len(status) <= _width(Signal.data_status)
