"""Lot L1 (docs/36 §36.9, ADR-017) — archive of the regulated releases of the AMF flow, for the causes study.

* ``archive_api``: the API of info-financiere.gouv.fr, month by month (one export, or day by day if refused);
* ``archive_dila``: the DILA packages of a period (2022 for the hole of the API), each read once
  (``official_filing_packages``), XML only, deleted once read;
* ``coverage``: releases per Paris month of filing, months « non couverts » (``app.domain.filings.month_coverage``),
  sources and their agreement, times, ISIN, families, type codes.

Idempotent: a release is stored once per (diffuser, number); the second source only marks ``also_in`` and whether it
gives the same release time (``also_in_time_ok``: the check of the time rule of ADR-017 on real data), and fills
what the first left empty. Nothing before 2015-10-01: the period 2007-2015 stays reserved (docs/34, plan 7).
Metadata only, aggregated output (no title, no name printed by ``coverage``).
"""

from __future__ import annotations

import logging
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.data.providers.amf_flow import (
    API_BASE,
    DILA_BASE,
    SOURCE_API,
    Download,
    Fetch,
    OfficialRelease,
    Throttle,
    fetch_api_month,
    http_fetch,
    iter_packages,
    list_packages,
)
from app.db.models import OfficialFiling, OfficialFilingPackage
from app.domain.filings import (
    FAMILY_LABELS,
    ReleaseTime,
    family_for,
    family_of,
    month_coverage,
    month_key,
    paris_time_from_utc,
    paris_wall_clock,
    valid_isin,
)
from app.domain.timeutil import from_store

log = logging.getLogger("bourse.filings")
Progress = Callable[[str], None]
SEAL_FROM = date(2015, 10, 1)  # docs/34: the period before is reserved (plan 7 kept it unread)
_CHUNK = 500  # keys per IN query (SQLite parameter limit)
_FILLABLE = ("issuer_name", "lei", "ticker", "subtype_label", "title", "language", "disclosure", "update_status",
             "update_ref", "pdf_path")  # fmt: skip
_MONTHS_FR = ("jan", "fév", "mar", "avr", "mai", "juin", "juil", "août", "sept", "oct", "nov", "déc")


def settings(config: LoadedConfig) -> dict[str, Any]:
    data = config.params.data
    cfg = (getattr(data, "filings", None) or {}) if data is not None else {}
    return {
        "api_base_url": str(cfg.get("api_base_url", API_BASE)),
        "dila_base_url": str(cfg.get("dila_base_url", DILA_BASE)),
        "min_interval_s": max(float(cfg.get("min_interval_s", 1.0)), 1.0),  # never faster than 1 request/s
        "history_from": date.fromisoformat(str(cfg.get("history_from", "2016-01-01"))),
        "coverage_min_share": float(cfg.get("coverage_min_share", 0.5)),
    }


def refusal(start: date, end: date) -> str | None:
    """Why a period cannot be read (None when it can)."""
    if start < SEAL_FROM:
        return (f"refusé : rien avant le {SEAL_FROM:%d/%m/%Y} — la période 2007-2015 reste réservée "
                "(docs/34, plan 7 non ouvert)")  # fmt: skip
    if end < start:
        return "refusé : la fin précède le début"
    return None


@dataclass
class FilingsReport:
    source: str
    read: int = 0
    added: int = 0
    known: int = 0  # already stored by the same source
    also_in: int = 0  # already stored by the other source: marked
    rederived: int = 0  # read again by its own source: times, label check or family changed
    same_time: int = 0
    different_time: int = 0
    time_gaps: Counter[int] = field(default_factory=Counter)  # minutes, other source − stored, when they differ
    skipped: int = 0  # records or XML members that are not a release notice
    without_isin: int = 0
    bad_isin: int = 0  # an ISIN written but invalid (shape or check digit): not kept
    unknown_codes: Counter[str] = field(default_factory=Counter)
    label_checked: int = 0
    label_wrong: int = 0
    amf_time_used: int = 0  # no readable market time: the filing time is used
    day_only: int = 0
    no_time: int = 0
    added_per_month: Counter[str] = field(default_factory=Counter)
    day_by_day_months: list[str] = field(default_factory=list)
    truncated_days: list[str] = field(default_factory=list)
    packages_read: int = 0
    packages_already: int = 0
    packages_failed: list[str] = field(default_factory=list)
    bytes_downloaded: int = 0
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        src = "API info-financiere" if self.source == "info_financiere" else "paquets DILA"
        parts = [
            f"{src} : {self.read} communiqué(s) lu(s), {self.added} ajouté(s), {self.known} déjà connu(s), "
            f"{self.rederived} recalculé(s)",
            f"{self.also_in} déjà lu(s) par l'autre source ; comparaisons d'heure : identique {self.same_time}, "
            f"différente {self.different_time}"
            + (
                " (écarts en minutes : " + ", ".join(f"{g:+d} ×{n}" for g, n in self.time_gaps.most_common(5)) + ")"
                if self.time_gaps
                else ""
            ),
            f"étiquette de fuseau fausse {self.label_wrong} sur {self.label_checked}",
            f"heure de dépôt à défaut {self.amf_time_used}, date sans heure {self.day_only}, sans heure {self.no_time}",
            f"ISIN absent {self.without_isin}, invalide {self.bad_isin}",
        ]
        if self.unknown_codes:
            parts.append(
                "codes inconnus " + ", ".join(f"{c or '(vide)'} ×{n}" for c, n in self.unknown_codes.most_common(8))
            )
        if self.skipped:
            parts.append(f"{self.skipped} élément(s) illisible(s) ignoré(s)")
        if self.source == "dila":
            parts.append(
                f"{self.packages_read} paquet(s) lu(s) ({self.bytes_downloaded / 1e9:.2f} Go), "
                f"{self.packages_already} déjà lu(s), {len(self.packages_failed)} en échec"
            )
        if self.day_by_day_months:
            parts.append("lus jour par jour : " + ", ".join(self.day_by_day_months))
        if self.truncated_days:
            parts.append("jours tronqués (> 10 000) : " + ", ".join(self.truncated_days))
        return " · ".join(parts) + (f"\n  notes : {'; '.join(self.notes[:10])}" if self.notes else "")


def _existing(s: Session, rels: list[OfficialRelease]) -> dict[tuple[str, str], OfficialFiling]:
    ids = sorted({r.uin_id for r in rels})
    out: dict[tuple[str, str], OfficialFiling] = {}
    for i in range(0, len(ids), _CHUNK):
        for row in s.scalars(select(OfficialFiling).where(OfficialFiling.uin_id.in_(ids[i : i + _CHUNK]))):
            out[(row.diffuser, row.uin_id)] = row
    return out


def _times(r: OfficialRelease) -> tuple[ReleaseTime, ReleaseTime, ReleaseTime, str | None]:
    """(market, filing, retained, time source) — the API gives UTC instants computed with the label, the DILA the
    Paris wall clock (ADR-017, revised 02/11/2026)."""
    read = paris_time_from_utc if r.source == SOURCE_API else paris_wall_clock
    market = read(r.market_raw, r.tz_label)
    amf = read(r.amf_raw, r.tz_label)
    if market.utc is not None:
        return market, amf, market, "marche"
    return market, amf, amf, ("amf" if amf.utc is not None else None)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, datetime) and isinstance(b, datetime):
        return from_store(a) == from_store(b)
    return bool(a == b)


def store(s: Session, rels: Iterable[OfficialRelease], received_at: datetime, rep: FilingsReport) -> int:
    """Each release stored once per (diffuser, number); returns the number added. A release read again by the source
    that stored it re-derives its times, label check and family from what is read (a corrected rule applies on the
    next read); read by the other source, it keeps that source's time (``other_market_timestamp``) and whether both
    agree (``also_in_time_ok``)."""
    batch = list(rels)
    known = _existing(s, batch)
    added = 0
    for r in batch:
        rep.read += 1
        now = datetime.now(UTC)
        _market, amf, when, time_source = _times(r)
        isin = valid_isin(r.isin)
        row = known.get((r.diffuser, r.uin_id))
        fam, ok = family_for(r.type_code, r.type_codes)
        derived = {
            "market_timestamp": when.utc, "amf_timestamp": amf.utc, "time_source": time_source,
            "time_precision": when.precision, "raw_market_time": r.market_raw, "raw_amf_time": r.amf_raw,
            "tz_label": when.label, "tz_label_ok": when.label_ok, "family": fam,
        }  # fmt: skip
        if row is None:
            if not ok:
                rep.unknown_codes[r.type_code or ""] += 1
            if isin is None:
                rep.bad_isin += int(bool(r.isin))
                rep.without_isin += int(not r.isin)
            rep.label_checked += int(when.label_ok is not None)
            rep.label_wrong += int(when.label_ok is False)
            rep.amf_time_used += int(time_source == "amf")
            rep.day_only += int(when.precision == "jour")
            rep.no_time += int(when.utc is None)
            row = OfficialFiling(
                source=r.source, diffuser=r.diffuser, uin_id=r.uin_id, isin=isin, type_code=r.type_code,
                type_codes=r.type_codes or None, data_status="eod", received_at=received_at, processed_at=now,
                **derived, **{k: getattr(r, k) for k in _FILLABLE},
            )  # fmt: skip
            s.add(row)
            known[(r.diffuser, r.uin_id)] = row
            added += 1
            rep.added += 1
            first_ts = amf.utc or when.utc
            if first_ts is not None:
                rep.added_per_month[month_key(first_ts)] += 1
            continue
        changed = False
        if row.source == r.source:
            rep.known += 1
            moved = [k for k, v in derived.items() if not _same(getattr(row, k), v)]
            for k in moved:
                setattr(row, k, derived[k])
            if moved:
                rep.rederived += 1
                changed = True
            if row.other_market_timestamp is not None:
                agree = when.utc is not None and _same(row.other_market_timestamp, when.utc)
                if row.also_in_time_ok != agree:
                    row.also_in_time_ok = agree
                    changed = True
        else:
            if row.also_in is None:
                rep.also_in += 1
            else:
                rep.known += 1
            prev = from_store(row.market_timestamp) if row.market_timestamp is not None else None
            same = prev is not None and when.utc is not None and prev == when.utc
            row.also_in, row.other_market_timestamp, row.also_in_time_ok = r.source, when.utc, same
            rep.same_time += int(same)
            rep.different_time += int(not same)
            if not same and prev is not None and when.utc is not None:
                rep.time_gaps[round((when.utc - prev).total_seconds() / 60)] += 1
            changed = True
        for k in _FILLABLE:
            if getattr(row, k) is None and getattr(r, k) is not None:
                setattr(row, k, getattr(r, k))
                changed = True
        if row.isin is None and isin is not None:
            row.isin = isin
            changed = True
        if changed:
            row.processed_at = now
    s.flush()
    return added


def refamily(s: Session, progress: Progress | None = None) -> Counter[str]:
    """Families derived again from the stored type codes, without any network read (after the list of codes has
    been completed). Returns the moves « old → new »."""
    moves: Counter[str] = Counter()
    last = 0
    while True:
        rows = list(
            s.scalars(select(OfficialFiling).where(OfficialFiling.id > last).order_by(OfficialFiling.id).limit(5000))
        )
        if not rows:
            break
        for row in rows:
            fam, _ok = family_for(row.type_code, row.type_codes or [])
            if fam != row.family:
                moves[f"{row.family} → {fam}"] += 1
                row.family, row.processed_at = fam, datetime.now(UTC)
        last = rows[-1].id
        s.commit()
        if progress:
            progress(f"{last} ligne(s) relues, {sum(moves.values())} famille(s) changée(s)")
    return moves


def _months(start: date, end: date) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def archive_api(
    s: Session,
    config: LoadedConfig,
    start: date,
    end: date,
    fetch: Fetch | None = None,
    sleep: Callable[[float], None] | None = None,
    progress: Progress | None = None,
) -> FilingsReport:
    """Every Paris month touching [start, end], read whole and committed one by one (an interrupted run keeps what it
    read; the next one only marks the rest « déjà connu »). Raises ValueError for a refused period and
    ProviderUnavailable when even the day-by-day reading fails."""
    why = refusal(start, end)
    if why:
        raise ValueError(why)
    say = progress or (lambda m: None)
    st = settings(config)
    rep = FilingsReport(source="info_financiere")
    get = fetch or http_fetch()  # one client and one pace for the whole run (≥ 1 s between two requests)
    throttle = Throttle(st["min_interval_s"], sleep or time.sleep)
    for y, m in _months(start, end):
        received_at = datetime.now(UTC)
        batch = fetch_api_month(y, m, get, st["api_base_url"], throttle=throttle)
        rep.skipped += batch.skipped
        rep.notes.extend(batch.notes)
        rep.truncated_days.extend(batch.truncated_days)
        if batch.mode == "records":
            rep.day_by_day_months.append(f"{y}-{m:02d}")
        n = store(s, batch.releases, received_at, rep)
        s.commit()
        say(f"{y}-{m:02d} : {len(batch.releases)} communiqué(s) lu(s), {n} ajouté(s)"
            + (" (jour par jour)" if batch.mode == "records" else ""))  # fmt: skip
    return rep


def archive_dila(
    s: Session,
    config: LoadedConfig,
    start: date,
    end: date,
    fetch: Fetch | None = None,
    download: Download | None = None,
    sleep: Callable[[float], None] | None = None,
    progress: Progress | None = None,
    again: bool = False,
    max_packages: int | None = None,
    workdir: Path | None = None,
) -> FilingsReport:
    """The DILA packages named from ``start`` to the day after ``end`` (the package of 00:00 holds the evening
    before), each downloaded, read (XML only) and deleted; a package read is recorded and skipped next time unless
    ``again``; a failed one is not recorded (read again next time)."""
    why = refusal(start, end)
    if why:
        raise ValueError(why)
    say = progress or (lambda m: None)
    st = settings(config)
    rep = FilingsReport(source="dila")
    base = st["dila_base_url"]
    packages = [p for p in list_packages(fetch, base) if start <= p.day <= end + timedelta(days=1)]
    done = set(s.scalars(select(OfficialFilingPackage.name)))
    todo = [p for p in packages if again or p.name not in done]
    rep.packages_already = len(packages) - len(todo)
    if max_packages is not None:
        todo = todo[: max(max_packages, 0)]
    say(f"{len(packages)} paquet(s) DILA du {start} au {end} · {rep.packages_already} déjà lu(s) · {len(todo)} à lire")
    kw: dict[str, Any] = {"sleep": sleep} if sleep is not None else {}
    with tempfile.TemporaryDirectory(prefix="dila_") as tmp:
        wd = workdir or Path(tmp)
        month = None
        for i, (p, content, size, error) in enumerate(
            iter_packages(todo, wd, download, base, st["min_interval_s"], **kw), start=1
        ):
            if content is None:
                rep.packages_failed.append(p.name)
                rep.notes.append(error or p.name)
                continue
            rep.packages_read += 1
            rep.bytes_downloaded += size
            rep.skipped += content.unreadable
            n = store(s, content.releases, datetime.now(UTC), rep)
            row = s.scalar(select(OfficialFilingPackage).where(OfficialFilingPackage.name == p.name))
            if row is None:
                row = OfficialFilingPackage(name=p.name)
                s.add(row)
            row.n_xml, row.n_new, row.n_unreadable = content.xml_members, n, content.unreadable
            row.size_bytes, row.processed_at = size, datetime.now(UTC)
            s.commit()
            if p.day.strftime("%Y-%m") != month or i == len(todo):
                month = p.day.strftime("%Y-%m")
                say(f"{p.name} ({i}/{len(todo)}) : {rep.read} communiqué(s) lu(s), {rep.added} ajouté(s) au total")
    return rep


def coverage(s: Session, config: LoadedConfig, start: date | None = None, end: date | None = None) -> str:
    """Aggregated coverage of the archive (no title, no issuer printed)."""
    st = settings(config)
    lo = start or st["history_from"]
    hi = end or date.today()
    counts: Counter[str] = Counter()
    for amf_ts, mkt_ts in s.execute(select(OfficialFiling.amf_timestamp, OfficialFiling.market_timestamp)):
        ts = amf_ts if amf_ts is not None else mkt_ts
        if ts is not None:
            counts[month_key(from_store(ts))] += 1
    months = [f"{y}-{m:02d}" for y, m in _months(lo, hi)]
    fractions: dict[str, float] = {}
    for key, day, first in ((months[0], lo, True), (months[-1], hi, False)):
        nxt = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
        length = (nxt - date(day.year, day.month, 1)).days
        share = ((nxt - day).days if first else day.day) / length
        fractions[key] = min(fractions.get(key, 1.0), share)
    cov = month_coverage(counts, months, st["coverage_min_share"], fractions)
    total = s.scalar(select(func.count()).select_from(OfficialFiling)) or 0
    lines = [
        "Communiqués officiels AMF (lot L1) — archive par mois de dépôt, heure de Paris",
        f"  {total} communiqué(s) en base ; période lue ici : {lo} → {hi}",
        "  année " + "".join(f"{m:>7}" for m in _MONTHS_FR) + "   total",
    ]
    by_year: dict[str, list[str]] = {}
    for c in cov:
        partial = fractions.get(c.month, 1.0) < 1.0  # a month begun or not ended inside the period
        mark = "~" if partial else ("!" if c.covered is False else " ")
        by_year.setdefault(c.month[:4], []).append(f"{c.n:>6}{mark}")
    for y, cells in by_year.items():
        pad = [" " * 7] * (lo.month - 1) if y == lo.strftime("%Y") else []
        row = pad + cells + [" " * 7] * (12 - len(pad) - len(cells))
        yr_total = sum(counts.get(f"{y}-{k:02d}", 0) for k in range(1, 13))
        lines.append(f"  {y}  " + "".join(row) + f" {yr_total:>7}")
    missing = [c.month for c in cov if c.covered is False and fractions.get(c.month, 1.0) >= 1.0]
    lines.append(f"  « ! » : moins de {st['coverage_min_share']:.0%} de la médiane du même mois des autres années ;"
                 " « ~ » : mois partiel")  # fmt: skip
    lines.append("  mois non couverts : " + (", ".join(missing) if missing else "aucun")
                 + " · mois partiels : " + ", ".join(k for k, v in fractions.items() if v < 1.0))  # fmt: skip

    def grouped(col: Any) -> list[tuple[Any, int]]:
        return [(k, int(n)) for k, n in s.execute(select(col, func.count()).group_by(col)).all()]

    src = dict(grouped(OfficialFiling.source))
    both = dict(grouped(OfficialFiling.also_in_time_ok))
    same, diff = both.get(True, 0), both.get(False, 0)
    lines.append(
        f"  sources : API {src.get('info_financiere', 0)} · DILA {src.get('dila', 0)} · lus par les deux "
        f"{same + diff} (heure identique {same}, différente {diff})"
    )
    gaps: Counter[int] = Counter()
    for own, other in s.execute(
        select(OfficialFiling.market_timestamp, OfficialFiling.other_market_timestamp).where(
            OfficialFiling.other_market_timestamp.is_not(None), OfficialFiling.market_timestamp.is_not(None)
        )
    ):
        gaps[round((from_store(other) - from_store(own)).total_seconds() / 60)] += 1
    if gaps:
        lines.append("  écart d'heure entre sources (minutes, autre − base) : "
                     + " · ".join(f"{g:+d} ×{n}" for g, n in gaps.most_common(6)))  # fmt: skip
    tsrc = dict(grouped(OfficialFiling.time_source))
    prec = dict(grouped(OfficialFiling.time_precision))
    lab = dict(grouped(OfficialFiling.tz_label_ok))
    lines.append(
        f"  heure : de marché {tsrc.get('marche', 0)}, de dépôt à défaut {tsrc.get('amf', 0)}, date sans heure "
        f"{prec.get('jour', 0)}, illisible {prec.get('illisible', 0)} · étiquette de fuseau fausse "
        f"{lab.get(False, 0)} sur {lab.get(False, 0) + lab.get(True, 0)}"
    )
    with_isin = s.scalar(select(func.count()).select_from(OfficialFiling).where(OfficialFiling.isin.is_not(None))) or 0
    lines.append(f"  ISIN valide : {with_isin} ({with_isin / total:.0%})" if total else "  ISIN valide : 0")
    fams = sorted(grouped(OfficialFiling.family), key=lambda x: -x[1])
    lines.append("  familles : " + " · ".join(f"{FAMILY_LABELS.get(f, f)} {n}" for f, n in fams))
    label_col, fam_col = func.max(OfficialFiling.subtype_label), func.max(OfficialFiling.family)
    codes = s.execute(
        select(OfficialFiling.type_code, func.count(), label_col, fam_col)
        .group_by(OfficialFiling.type_code)
        .order_by(func.count().desc())
    ).all()
    lines.append("  codes de type (code · famille · libellé de l'API · nombre) :")
    for code, n, label, fam in codes[:45]:
        known = family_of(code)[1]
        lines.append(f"    {code or '(vide)':>6} · {FAMILY_LABELS.get(fam, fam):<38} · {(label or '—')[:60]:<60} · {n}"
                     + ("" if known else "  ← code inconnu (famille du code secondaire)"))  # fmt: skip
    return "\n".join(lines)
