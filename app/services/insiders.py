"""docs/34 §34.8 — daily archive of the managers' dealing declarations published by the AMF (BDIF).

Archive only, kept forward from now for a future pre-registered test of the « achats de dirigeants » trigger:
no signal, no score, nothing shown beyond a coverage line. The availability date of a declaration is its AMF
publication time (``market_timestamp``), never the transaction date. Idempotent: a declaration is stored once per
(source, number); a later read only completes empty fields (PDF read afterwards, ISIN found afterwards).
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.data.providers.amf_bdif import (
    SOURCE,
    DealingDeclaration,
    Fetch,
    PdfText,
    fetch_declarations,
    pdf_extractor_available,
    pypdf_text,
)
from app.db.models import InsiderDeclaration, Instrument
from app.domain.timeutil import PARIS
from app.services.media import norm_name

log = logging.getLogger("bourse.insiders")
Progress = Callable[[str], None]
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_FILLABLE = (
    "info_date", "issuer_name", "issuer_token", "role", "related_role", "linked_person", "nature", "nature_detail",
    "tx_date", "tx_date_last", "price", "currency", "volume", "amount", "venue", "n_transactions", "pdf_url",
)  # fmt: skip


@dataclass
class ArchiveReport:
    listed: int = 0
    added: int = 0
    completed: int = 0
    parsed: int = 0
    pdf_read: int = 0
    isin_from_declaration: int = 0
    isin_from_name: int = 0
    without_isin: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        return (
            f"{self.listed} déclaration(s) lue(s), {self.added} ajoutée(s), {self.completed} complétée(s), "
            f"{self.pdf_read} PDF lu(s) · ISIN : {self.isin_from_declaration} par la déclaration, "
            f"{self.isin_from_name} par le nom, {self.without_isin} sans"
            + (f" · {'; '.join(self.notes)}" if self.notes else "")
        )


def settings(config: LoadedConfig) -> dict[str, Any]:
    data = config.params.data
    cfg = (getattr(data, "insiders", None) or {}) if data is not None else {}
    return {
        "page_size": int(cfg.get("page_size", 100)),
        "min_interval_s": max(float(cfg.get("min_interval_s", 1.0)), 1.0),  # never faster than 1 request/s
        "read_pdf": bool(cfg.get("read_pdf", True)),
        "lookback_days": int(cfg.get("lookback_days", 7)),
        "window_days": max(int(cfg.get("window_days", 31)), 1),
    }


def _name_index(s: Session) -> dict[str, str]:
    """Normalised instrument name → ISIN; a name two instruments share is dropped (never matched by guess)."""
    out: dict[str, str] = {}
    clash: set[str] = set()
    for isin, name in s.execute(select(Instrument.isin, Instrument.name)):
        n = norm_name(name or "")
        if not n:
            continue
        if n in out and out[n] != isin:
            clash.add(n)
        out.setdefault(n, isin)
    return {k: v for k, v in out.items() if k not in clash}


def _isin_of(d: DealingDeclaration, names: dict[str, str]) -> tuple[str | None, str | None]:
    if d.isin and _ISIN.match(d.isin):
        return d.isin, "declaration"
    hit = names.get(norm_name(d.issuer_name or "")) if d.issuer_name else None
    return (hit, "name") if hit else (None, None)


def store(
    s: Session, decls: list[DealingDeclaration], received_at: datetime, rep: ArchiveReport, overwrite: bool = False
) -> None:
    """Each declaration stored once; a known one only gets what it lacked — or, with ``overwrite`` (``--reparse``),
    the fields of its PDF read again replace the former ones (parser fixed on 30/10/2026)."""
    names = _name_index(s)
    ids = [d.doc_id for d in decls]
    known = {
        r.doc_id: r
        for r in s.scalars(
            select(InsiderDeclaration).where(InsiderDeclaration.source == SOURCE, InsiderDeclaration.doc_id.in_(ids))
        )
    } if ids else {}  # fmt: skip
    for d in decls:
        now = datetime.now(UTC)
        isin, how = _isin_of(d, names)
        row = known.get(d.doc_id)
        if row is None:
            row = InsiderDeclaration(
                source=d.source, doc_id=d.doc_id, market_timestamp=d.published_at, isin=isin, isin_source=how,
                transactions=[t.model_dump(mode="json") for t in d.transactions] or None,
                parse_status=d.parse_status, received_at=received_at, processed_at=now,
                **{k: getattr(d, k) for k in _FILLABLE},
            )  # fmt: skip
            s.add(row)
            known[d.doc_id] = row
            rep.added += 1
            rep.parsed += int(d.parse_status == "parsed")
        elif overwrite and d.parse_status == "parsed":
            for k in _FILLABLE:
                setattr(row, k, getattr(d, k))
            row.transactions = [t.model_dump(mode="json") for t in d.transactions] or None
            if how == "declaration" or row.isin is None:
                row.isin, row.isin_source = isin, how
            row.parse_status, row.processed_at = "parsed", now
            rep.completed += 1
        else:
            changed = False
            for k in _FILLABLE:
                if getattr(row, k) is None and getattr(d, k) is not None:
                    setattr(row, k, getattr(d, k))
                    changed = True
            if row.transactions is None and d.transactions:
                row.transactions = [t.model_dump(mode="json") for t in d.transactions]
                changed = True
            if row.isin is None and isin is not None:
                row.isin, row.isin_source = isin, how
                changed = True
            if row.parse_status != "parsed" and d.parse_status != "listing_only" and d.parse_status != row.parse_status:
                rep.parsed += int(d.parse_status == "parsed")
                row.parse_status = d.parse_status
                changed = True
            if changed:
                row.processed_at = now
                rep.completed += 1
        if row.isin_source == "declaration":
            rep.isin_from_declaration += 1
        elif row.isin_source == "name":
            rep.isin_from_name += 1
        else:
            rep.without_isin += 1
    s.flush()


def archive(
    s: Session,
    config: LoadedConfig,
    start: date,
    end: date,
    fetch: Fetch | None = None,
    pdf_text: PdfText | None | Literal["auto"] = "auto",
    sleep: Callable[[float], None] | None = None,
    progress: Progress | None = None,
    reparse: bool = False,
) -> ArchiveReport:
    """Declarations published between ``start`` and ``end`` (Paris days), read window by window
    (``data.insiders.window_days``) and stored once. Raises ProviderUnavailable when the listing is unreachable.
    ``reparse``: the PDFs of the declarations whose nature was not recognised (empty or « mixte ») are read again
    and their fields replaced (parser fixed on 30/10/2026)."""
    say = progress or (lambda m: None)
    st = settings(config)
    rep = ArchiveReport()
    if pdf_text == "auto":
        pdf_text = pypdf_text if (st["read_pdf"] and pdf_extractor_available()) else None
        if st["read_pdf"] and pdf_text is None:
            rep.notes.append("pypdf absent : chiffres non lus, adresse du PDF conservée")
    kw: dict[str, Any] = {"sleep": sleep} if sleep is not None else {}
    lo = start
    while lo <= end:
        hi = min(lo + timedelta(days=st["window_days"] - 1), end)
        done = select(InsiderDeclaration.doc_id).where(
            InsiderDeclaration.source == SOURCE, InsiderDeclaration.parse_status == "parsed"
        )
        if reparse:  # nature not recognised (fixed 30/10) or no role read (fixed 31/10): read again
            done = done.where(
                InsiderDeclaration.nature.is_not(None),
                InsiderDeclaration.nature != "mixte",
                InsiderDeclaration.role.is_not(None),
            )
        parsed_ids = set(s.scalars(done))  # their PDF is never downloaded again
        received_at = datetime.now(UTC)
        res = fetch_declarations(
            lo, hi, fetch, st["page_size"], st["min_interval_s"], pdf_text, parsed_ids, **kw
        )  # fmt: skip
        rep.listed += len(res.declarations)
        rep.skipped += res.skipped
        rep.pdf_read += res.pdf_read
        rep.notes.extend(res.notes)
        store(s, res.declarations, received_at, rep, overwrite=reparse)
        if reparse:  # hours of PDFs: each window kept even if a later one fails (review of 30/10/2026)
            s.commit()
        say(f"{lo} → {hi} : {len(res.declarations)} déclaration(s), {rep.added} ajoutée(s) au total")
        lo = hi + timedelta(days=1)
    if rep.skipped:
        rep.notes.append(f"{rep.skipped} élément(s) sans numéro ou sans date ignoré(s)")
    return rep


def coverage(s: Session) -> str:
    """Per month of publication (Paris): declarations, with figures, acquisitions, with an ISIN, of which an
    instrument of the universe. No price is read."""
    universe = set(s.scalars(select(Instrument.isin).where(Instrument.in_universe.is_(True))))
    cells: dict[str, dict[str, int]] = defaultdict(
        lambda: dict.fromkeys(("n", "chiffrees", "achats", "isin", "univers"), 0)
    )
    for r in s.scalars(select(InsiderDeclaration).where(InsiderDeclaration.source == SOURCE)):
        ts = r.market_timestamp if r.market_timestamp.tzinfo else r.market_timestamp.replace(tzinfo=UTC)
        c = cells[ts.astimezone(PARIS).strftime("%Y-%m")]
        c["n"] += 1
        c["chiffrees"] += int(r.parse_status == "parsed" and r.volume is not None)
        c["achats"] += int(r.nature == "acquisition")
        c["isin"] += int(r.isin is not None)
        c["univers"] += int(r.isin in universe)
    lines = ["Archive des déclarations de dirigeants (AMF BDIF) — par mois de publication"]
    if not cells:
        lines.append("  aucune déclaration archivée")
    for m, c in sorted(cells.items()):
        lines.append(
            f"  {m} : {c['n']:>5} déclarations · {c['chiffrees']:>5} chiffrées · {c['achats']:>5} acquisitions"
            f" · {c['isin']:>5} avec ISIN ({c['univers']} dans l'univers)"
        )
    return "\n".join(lines)
