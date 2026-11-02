"""Registration n° 8 (docs/34 §34.15): the archived official AMF releases (``official_filings``, lot L1) for the
research harness. Reads the family, the first-notice status, the ISIN and the market time of each release — never
a title or an issuer name; the rules are the plan's ``filing_*`` assumptions."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import OfficialFiling
from app.domain.research.filing_triggers import (
    CellRule,
    FilingData,
    FilingRow,
    coverage,
    needs_filings,
    place,
    rules_from,
)
from app.domain.research.panel import Panel, valid_bars
from app.domain.research.pipeline import Settings
from app.domain.research.prereg import Prereg


def load_rows(s: Session, families: Sequence[str] | None = None) -> tuple[FilingRow, ...]:
    """Every archived release of the given families (all when None) as a research DTO."""
    q = select(
        OfficialFiling.isin, OfficialFiling.market_timestamp, OfficialFiling.family, OfficialFiling.time_precision,
        OfficialFiling.update_status,
    )  # fmt: skip
    if families:
        q = q.where(OfficialFiling.family.in_(list(families)))
    return tuple(
        FilingRow(isin, ts, str(fam), str(prec or "seconde"), status) for isin, ts, fam, prec, status in s.execute(q)
    )


def _families(prereg: Prereg) -> list[str]:
    return sorted({str(c.params.get("family")) for c in prereg.cells if needs_filings([c])})


def with_filings(s: Session, st: Settings, prereg: Prereg) -> Settings:
    """The settings with the releases of the plan's families and its rules (unchanged for a plan without t19)."""
    if not needs_filings(prereg.cells):
        return st
    return replace(st, filings=FilingData(load_rows(s, _families(prereg)), rules_from(prereg.assumptions)))


def filing_context(
    s: Session, st: Settings, prereg: Prereg, panels: Sequence[Panel]
) -> tuple[Settings, dict[str, Any] | None]:
    """``with_filings`` and the archive's coverage for the report (by month, the exclusion funnel of the
    confirmatory family, the placement on the panels). (st, None) for a plan without t19."""
    st = with_filings(s, st, prereg)
    if st.filings is None:
        return st, None
    conf = [c for c in prereg.confirmatory if needs_filings([c])] or [c for c in prereg.cells if needs_filings([c])]
    rule = CellRule.of(conf[0].params)
    cov = coverage(st.filings.rows, rule)
    placed: Counter[str] = Counter()
    for p in panels:
        placed.update(place(p, valid_bars(p), st.filings, rule).counts)
    cov["placement"] = dict(sorted(placed.items()))
    return st, cov


__all__ = ["filing_context", "load_rows", "with_filings"]
