"""Registration n° 5 (docs/34 §34.12): the archived AMF declarations (``insider_declarations``) for the research
harness and the shadow tracking. Reads roles, natures, venues, prices and volumes of the declarations — never a
name (none is stored) — and the quote currency of the values; the rules are the plan's ``insider_*`` assumptions."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data.providers.amf_bdif import SOURCE
from app.db.models import InsiderDeclaration, Instrument, ShadowSignal
from app.domain.research.insider_triggers import (
    CellRule,
    InsiderData,
    InsiderRow,
    check_forward,
    coverage,
    insider_report,
    needs_insiders,
    place,
    rules_from,
)
from app.domain.research.panel import Panel, valid_bars
from app.domain.research.pipeline import Settings
from app.domain.research.prereg import Prereg, PreregError


def load_rows(s: Session) -> tuple[InsiderRow, ...]:
    """Every archived AMF declaration as a research DTO (with the value's quote currency)."""
    quote = {str(i): c for i, c in s.execute(select(Instrument.isin, Instrument.currency))}
    out = []
    for r in s.scalars(select(InsiderDeclaration).where(InsiderDeclaration.source == SOURCE)):
        out.append(
            InsiderRow(
                r.isin,
                r.market_timestamp,
                r.parse_status,
                r.role,
                r.nature,
                r.nature_detail,
                r.venue,
                r.price,
                r.volume,
                r.currency,
                quote.get(r.isin or ""),
                r.isin_source,
                tuple(t for t in (r.transactions or []) if isinstance(t, Mapping)),
            )
        )
    return tuple(out)


def with_insiders(s: Session, st: Settings, prereg: Prereg) -> Settings:
    """The settings with the declarations and the plan's rules (unchanged for a plan without t17)."""
    if not needs_insiders(prereg.cells):
        return st
    return replace(st, insiders=InsiderData(load_rows(s), rules_from(prereg.assumptions)))


def insider_context(
    s: Session, st: Settings, prereg: Prereg, panels: Sequence[Panel]
) -> tuple[Settings, dict[str, Any] | None]:
    """``with_insiders`` and the archive's coverage for the report header (by month, the exclusion funnel of the
    confirmatory rule, the placement on the panels). (st, None) for a plan without t17."""
    st = with_insiders(s, st, prereg)
    if st.insiders is None:
        return st, None
    conf = [c for c in prereg.confirmatory if needs_insiders([c])] or [c for c in prereg.cells if needs_insiders([c])]
    rule = CellRule.of(conf[0].params)
    cov = coverage(st.insiders.rows, st.insiders.rules, rule)
    placed: Counter[str] = Counter()
    for p in panels:
        placed.update(place(p, valid_bars(p), st.insiders, rule).counts)
    cov["placement"] = dict(sorted(placed.items()))
    return st, cov


def check_forward_unseal(s: Session, prereg_path: str | Path, prereg: Prereg, ledger: Sequence[Mapping[str, str]],
                         end: date) -> None:  # fmt: skip
    """The plan's ``forward_confirmation`` rule before its single unseal (nothing for a plan without it)."""
    raw = yaml.safe_load(Path(prereg_path).read_bytes()) or {}
    fw = raw.get("forward_confirmation")
    if not fw:
        return
    n = s.scalar(
        select(func.count(ShadowSignal.id)).where(ShadowSignal.plan_id == prereg.id, ShadowSignal.cell == fw["cell"])
    )
    try:
        check_forward(fw, prereg.sha256, ledger, end, int(n or 0))
    except ValueError as e:
        raise PreregError(str(e)) from e


__all__ = ["check_forward_unseal", "insider_context", "insider_report", "load_rows", "with_insiders"]
