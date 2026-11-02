"""Registration n° 8 (docs/34 §34.15): the official releases of the AMF flow (``official_filings``, lot L1) as
research triggers. Pure: the releases come as DTOs (``FilingRow``), the panel's calendar places them.

Trigger ``t19`` (one function, its variants are cell parameters of the plan):

- a release is retained only when its family is the cell's (``app.domain.filings.FAMILIES``), it is a first notice
  (``update_status`` not « REC » / « ADD »), it carries a French ISIN (the flow covers issuers supervised by the AMF)
  and a market time (``market_timestamp``, the Paris wall clock of ADR-017). Unknown → excluded and counted;
- it is placed on the value's first own session **on** its day when it was released at or before
  ``filing_close_cutoff_paris`` (Paris time), else on the first own session **after** it (released after the close,
  on a non-session day, or dated without an hour: ``time_precision = jour``, never assumed known earlier); a session
  more than ``filing_max_gap_days`` calendar days later is not a placement (suspension, not yet listed) — the rule of
  registration n° 5 for the AMF declarations;
- one event per value every ``dedup_sessions`` own sessions (the first one, outcome-free); the shifted control moves
  each event ``shift_sessions`` own sessions later;
- signals only on Paris-listed values (``Panel.is_xpar``) and on eligible bars.

The signal is known at the close of its session; the order works from the next open (E_open), so the entry is always
after the release.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from app.domain.filings import FAMILIES
from app.domain.research.panel import I64, Bool, Features, Panel

FILING_TRIGGERS = frozenset({"t19"})
PARIS = ZoneInfo("Europe/Paris")
OK = "retenue"
REASONS = ("autre_famille", "rectificatif_ou_complement", "sans_isin_francais", "sans_heure")  # checked in this order
PLACEMENT = ("placees", "hors_panneau", "hors_paris", "sans_seance_proche")


@dataclass(frozen=True)
class FilingRow:
    """One stored release (``official_filings``): no title, no issuer name."""

    isin: str | None
    market_timestamp: datetime | None  # aware UTC (naive = UTC); None: no readable time
    family: str
    time_precision: str = "seconde"  # seconde | jour | illisible
    update_status: str | None = None  # NEW | REC | ADD


@dataclass(frozen=True)
class FilingRules:
    """The plan's registered assumptions (``filing_*`` keys): never a code default."""

    close_cutoff: time
    max_gap_days: int


@dataclass(frozen=True)
class FilingData:
    rows: tuple[FilingRow, ...]
    rules: FilingRules


@dataclass(frozen=True)
class CellRule:
    """The t19 parameters of one cell."""

    family: str
    dedup_sessions: int = 20
    shift_sessions: int = 0

    @staticmethod
    def of(params: Mapping[str, Any]) -> CellRule:
        fam = str(params.get("family", ""))
        if fam not in FAMILIES:
            raise ValueError(f"t19 : famille {fam!r} inconnue (liste fermée : {', '.join(FAMILIES)})")
        return CellRule(fam, int(params.get("dedup_sessions", 20)), int(params.get("shift_sessions", 0)))


def needs_filings(cells: Sequence[Any]) -> bool:
    return any(getattr(c, "trigger", None) in FILING_TRIGGERS for c in cells)


def check_filing_params(params: Mapping[str, Any]) -> None:
    """A t19 cell names a known family and positive windows (refused at registration, never at run time)."""
    rule = CellRule.of(params)
    if rule.dedup_sessions < 1 or rule.shift_sessions < 0:
        raise ValueError("t19 : dedup_sessions ≥ 1 et shift_sessions ≥ 0")


def rules_from(assumptions: Mapping[str, Any]) -> FilingRules:
    """The plan's assumptions → rules; a missing key is refused (the plan is the only source)."""
    hh, mm = (int(x) for x in str(assumptions["filing_close_cutoff_paris"]).split(":"))
    return FilingRules(time(hh, mm), int(assumptions["filing_max_gap_days"]))


def screen(r: FilingRow, rule: CellRule) -> str:
    """« retenue » or the first exclusion reason (``REASONS``)."""
    if r.family != rule.family:
        return "autre_famille"
    if (r.update_status or "").upper() in ("REC", "ADD"):
        return "rectificatif_ou_complement"
    if not (r.isin or "").startswith("FR"):
        return "sans_isin_francais"
    if r.market_timestamp is None or r.time_precision == "illisible":
        return "sans_heure"
    return OK


def _paris(ts: datetime) -> datetime:
    return (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).astimezone(PARIS)


@dataclass
class Placed:
    ts: I64
    cols: I64
    counts: dict[str, int] = field(default_factory=dict)


def place(p: Panel, valid: Bool, data: FilingData, rule: CellRule) -> Placed:
    """Retained releases of a cell rule on the panel: (calendar row, column) and the placement counts."""
    rules = data.rules
    col = {x: k for k, x in enumerate(p.isins)}
    day_num = p.days.astype("datetime64[D]").astype(np.int64)
    own: dict[int, tuple[I64, I64]] = {}
    ts: list[int] = []
    cols: list[int] = []
    counts: Counter[str] = Counter()
    for r in data.rows:
        if screen(r, rule) != OK or r.market_timestamp is None:
            continue
        k = col.get(str(r.isin))
        if k is None:
            counts["hors_panneau"] += 1
            continue
        if not bool(p.is_xpar[k]):
            counts["hors_paris"] += 1
            continue
        if k not in own:
            rows_k = np.nonzero(valid[:, k])[0].astype(np.int64)
            own[k] = (rows_k, day_num[rows_k])
        rows_k, days_k = own[k]
        local = _paris(r.market_timestamp)
        dn = int(np.datetime64(local.date(), "D").astype(np.int64))
        after = r.time_precision == "jour" or local.time() > rules.close_cutoff
        i = int(np.searchsorted(days_k, dn, side="right" if after else "left"))
        if i >= days_k.size or int(days_k[i]) - dn > rules.max_gap_days:
            counts["sans_seance_proche"] += 1
            continue
        counts["placees"] += 1
        ts.append(int(rows_k[i]))
        cols.append(k)
    return Placed(np.array(ts, dtype=np.int64), np.array(cols, dtype=np.int64), dict(counts))


def _events(placed: Placed, f: Features, rule: CellRule) -> tuple[I64, I64]:
    """Own-session events of a cell: one per value every ``dedup_sessions``, then the shift."""
    pk = f.pk
    by: dict[int, set[int]] = defaultdict(set)
    for t, c in zip(placed.ts.tolist(), placed.cols.tolist(), strict=True):
        by[c].add(int(pk.pos[t, c]))
    out_pos: list[int] = []
    out_col: list[int] = []
    for c, sessions in sorted(by.items()):
        nxt = -1
        for q in sorted(sessions):
            if q < nxt:
                continue
            nxt = q + rule.dedup_sessions
            q2 = q + rule.shift_sessions
            if q2 < int(pk.n_bars[c]):
                out_pos.append(q2)
                out_col.append(c)
    cols = np.array(out_col, dtype=np.int64)
    rows = pk.order[np.array(out_pos, dtype=np.int64), cols] if cols.size else np.zeros(0, dtype=np.int64)
    return rows.astype(np.int64), cols


def t19_filing(p: Panel, f: Features, data: FilingData | None, params: Mapping[str, Any]) -> Bool:
    """T19 (registration n° 8): the cell's retained releases as events on eligible Paris bars. Without the archive
    (``data`` None) nothing is known: no signal."""
    m = np.zeros(p.shape, dtype=np.bool_)
    if data is None:
        return m
    rule = CellRule.of(params)
    ts, cols = _events(place(p, f.valid, data, rule), f, rule)
    m[ts, cols] = True
    return np.asarray(m & f.elig & p.is_xpar[None, :], dtype=np.bool_)


def coverage(rows: Sequence[FilingRow], rule: CellRule) -> dict[str, Any]:
    """Per Paris month of release: releases of the family and those retained; the exclusion funnel. Counts only."""
    months: dict[str, Counter[str]] = defaultdict(Counter)
    funnel: Counter[str] = Counter()
    for r in rows:
        why = screen(r, rule)
        if why == "autre_famille":
            continue
        funnel[why] += 1
        if r.market_timestamp is not None:
            m = months[_paris(r.market_timestamp).strftime("%Y-%m")]
            m["famille"] += 1
            m["retenues"] += int(why == OK)
    return {
        "famille": rule.family,
        "entonnoir": {k: funnel.get(k, 0) for k in (OK, *REASONS[1:])},
        "par_mois": {k: dict(v) for k, v in sorted(months.items())},
    }


def coverage_text(cov: Mapping[str, Any] | None) -> str:
    """The report's coverage block for t19 (appended to the French report)."""
    if not cov:
        return ""
    lines = ["", f"Communiqués officiels AMF (t19, famille « {cov['famille']} ») — couverture", ""]
    lines.append("  entonnoir : " + " · ".join(f"{k} {v}" for k, v in cov["entonnoir"].items()))
    if cov.get("placement"):
        lines.append("  placement sur les séances : " + " · ".join(f"{k} {v}" for k, v in cov["placement"].items()))
    per_year: dict[str, list[int]] = defaultdict(lambda: [0] * 12)
    for m, c in cov["par_mois"].items():
        per_year[m[:4]][int(m[5:]) - 1] = int(c.get("retenues", 0))
    lines.append("  retenues par mois (jan → déc) :")
    for y, vals in sorted(per_year.items()):
        lines.append(f"    {y} : " + " ".join(f"{v:>4}" for v in vals))
    lines.append("  un mois vide ou très bas est un trou de l'archive (07-10/2018, 03-06/2020 : docs/36 §36.9), "
                 "pas une absence de communiqué")  # fmt: skip
    return "\n".join(lines) + "\n"


__all__ = [
    "FILING_TRIGGERS",
    "CellRule",
    "FilingData",
    "FilingRow",
    "FilingRules",
    "check_filing_params",
    "coverage",
    "coverage_text",
    "needs_filings",
    "place",
    "rules_from",
    "screen",
    "t19_filing",
]
