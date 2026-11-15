"""Lot S1 (step 1 of the user's plan, 15/11/2026) — the health of every data source, every day.

A registry lists each source the tool collects (prices, news, macro, letters, podcasts, managers' dealings, analysts,
filings, forums, radar, the tool's own signals): the table it lands in, the column that dates an item, the expected
cadence, the venues it covers and the job that feeds it. Each evening the snapshot counts, per source, the items of
the day and of the last 7 days, the history held (first and last item) and says whether the source is on time, late
(silent longer than its cadence allows) or empty. The snapshot is kept day by day (``source_health``), so a source
that went quiet a month ago stays visible. Facts only: counts of stored rows, never an estimate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.timeutil import PARIS

FAMILIES = {
    "cours": "Cours et indicateurs",
    "conjoncture": "Macro et conjoncture",
    "actualites": "Actualités",
    "momentum": "Momentum (lettres, médias, dirigeants, analystes, forums)",
    "officiel": "Sources officielles",
    "outil": "Signaux de l'outil",
}
STATUS_LABELS = {"ok": "à jour", "en_retard": "en retard", "vide": "vide"}


@dataclass(frozen=True)
class SourceSpec:
    key: str
    label: str
    family: str
    model: Any
    column: str
    cadence: str  # seance | quotidien | hebdo
    max_silent_weekdays: int  # weekdays without any new item before the source is « en retard »
    coverage: str
    feeder: str  # job (or command) that feeds it
    where: Callable[[Any], Any] | None = None  # extra filter on the model

    @property
    def col(self) -> Any:
        return getattr(self.model, self.column)

    def query(self, *cols: Any) -> Any:
        q = select(*cols)
        return q.where(self.where(self.model)) if self.where is not None else q


def registry() -> list[SourceSpec]:
    """Every source collected by the tool (the order is the page's)."""
    from app.db import models as m

    return [
        SourceSpec("cours_eod", "Cours de clôture", "cours", m.PriceEod, "day", "quotidien", 2,
                   "16 places Europe et US", "eod_pipeline"),
        SourceSpec("indicateurs", "Indicateurs techniques (MM, ATR, volumes, force relative)", "cours",
                   m.FeatureDaily, "day", "quotidien", 2, "univers", "eod_pipeline"),
        SourceSpec("regime", "Régime de marché par place", "conjoncture", m.MarketRegimeRow, "day", "quotidien", 2,
                   "chaque place et l'Europe", "daily_regime"),
        SourceSpec("change", "Taux de change BCE", "conjoncture", m.FxRate, "day", "quotidien", 3, "devises",
                   "fx_refresh"),
        SourceSpec("macro", "Séries macro (VIX, Brent, or, taux, indices)", "conjoncture", m.MacroPoint, "day",
                   "quotidien", 2, "mondial", "macro_snapshot"),
        SourceSpec("calendrier_macro", "Calendrier macro (banques centrales, inflation, emploi)", "conjoncture",
                   m.MacroEvent, "received_at", "quotidien", 3, "EUR, USD", "macro_calendar"),
        SourceSpec("actualites", "Actualités (flux RSS)", "actualites", m.NewsItem, "fetched_at", "seance", 2,
                   "flux renseignés", "news_scan"),
        SourceSpec("lettres", "Lettres d'analystes (Gmail)", "momentum", m.NewsletterItem, "received_at", "quotidien",
                   5, "Paris", "imap_poll"),
        SourceSpec("medias", "Podcasts et médias", "momentum", m.MediaMention, "received_at", "hebdo", 7,
                   "France, Allemagne, Benelux, Italie", "news_scan"),
        SourceSpec("dirigeants", "Déclarations des dirigeants (AMF)", "momentum", m.InsiderDeclaration, "received_at",
                   "quotidien", 3, "France", "insider_archive", where=lambda t: t.source != "sec_form4"),
        SourceSpec("dirigeants_us", "Déclarations des dirigeants (SEC Form 4)", "momentum", m.InsiderDeclaration,
                   "received_at", "quotidien", 3, "États-Unis", "us_filings", where=lambda t: t.source == "sec_form4"),
        SourceSpec("consensus", "Consensus des analystes (TradingView)", "momentum", m.AnalystConsensus, "day",
                   "quotidien", 3, "univers", "consensus_daily"),
        SourceSpec("fondamentaux", "Fondamentaux (Yahoo)", "momentum", m.Fundamentals, "day", "quotidien", 3,
                   "univers, par rotation", "fundamentals_refresh"),
        SourceSpec("resultats", "Résultats publiés et attendus", "momentum", m.EarningsEvent, "received_at", "hebdo",
                   7, "Europe et US", "earnings_refresh"),
        SourceSpec("forums", "Forums et réseaux (comptages)", "momentum", m.AttentionDaily, "day", "quotidien", 3,
                   "Boursorama, Reddit (Europe, US), Bluesky", "social_watch",
                   where=lambda t: t.source != "wikipedia"),
        SourceSpec("forums_contenu", "Contenu des échanges (forums)", "momentum", m.SocialContent, "computed_at",
                   "quotidien", 5, "valeurs en emballement", "social_watch"),
        SourceSpec("wikipedia", "Pages vues Wikipédia", "momentum", m.AttentionDaily, "day", "quotidien", 3,
                   "toutes les places", "weak_signals", where=lambda t: t.source == "wikipedia"),
        SourceSpec("positions_courtes", "Positions courtes publiées", "officiel", m.ShortPosition, "received_at",
                   "quotidien", 5, "autorités renseignées", "short_positions_refresh"),
        SourceSpec("communiques", "Communiqués officiels (AMF)", "officiel", m.OfficialFiling, "received_at",
                   "quotidien", 3, "France", "filings_daily", where=lambda t: t.diffuser != "SEC"),
        SourceSpec("evenements_us", "Événements importants (SEC 8-K)", "officiel", m.OfficialFiling, "received_at",
                   "quotidien", 3, "États-Unis", "us_filings", where=lambda t: t.diffuser == "SEC"),
        SourceSpec("vad_volume_us", "Ventes à découvert du jour (FINRA)", "officiel", m.ShortVolumeDaily, "day",
                   "quotidien", 3, "États-Unis", "us_filings"),
        SourceSpec("radar", "Radar des signaux faibles", "outil", m.WeakSignalHit, "day", "quotidien", 2, "univers",
                   "weak_signals"),
        SourceSpec("signaux", "Signaux des détecteurs et des lettres", "outil", m.Signal, "ts", "quotidien", 2,
                   "univers", "opportunity_scan"),
        SourceSpec("evenements", "Journal des événements (lot S2)", "outil", m.MarketEvent, "created_at",
                   "quotidien", 2, "toutes les sources", "sources_daily"),
    ]  # fmt: skip


def _is_datetime(spec: SourceSpec) -> bool:
    return spec.column != "day"


def _bounds(day: date) -> tuple[datetime, datetime]:
    """The Paris calendar day as UTC instants."""
    a = datetime.combine(day, time.min, tzinfo=PARIS).astimezone(UTC)
    return a, a + timedelta(days=1)


def _as_day(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        dt = v if v.tzinfo else v.replace(tzinfo=UTC)
        return dt.astimezone(PARIS).date()
    return v if isinstance(v, date) else None


def weekdays_between(a: date, b: date) -> int:
    """Weekdays strictly after ``a`` up to ``b`` included (0 when ``a`` ≥ ``b``)."""
    n, d = 0, a + timedelta(days=1)
    while d <= b:
        n += d.weekday() < 5
        d += timedelta(days=1)
    return n


@dataclass
class SourceState:
    key: str
    label: str
    family: str
    cadence: str
    coverage: str
    feeder: str
    items_day: int
    items_7d: int
    total: int
    first: date | None
    last: date | None
    silent_weekdays: int | None
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "first": self.first.isoformat() if self.first else None,
                "last": self.last.isoformat() if self.last else None}  # fmt: skip


def state_of(s: Session, spec: SourceSpec, day: date) -> SourceState:
    col = spec.col
    total = int(s.scalar(spec.query(func.count()).select_from(spec.model)) or 0)
    first = _as_day(s.scalar(spec.query(func.min(col))))
    last = _as_day(s.scalar(spec.query(func.max(col))))
    if _is_datetime(spec):
        a, b = _bounds(day)
        a7 = _bounds(day - timedelta(days=6))[0]
        n_day = s.scalar(spec.query(func.count()).select_from(spec.model).where(col >= a, col < b))
        n_7 = s.scalar(spec.query(func.count()).select_from(spec.model).where(col >= a7, col < b))
    else:
        n_day = s.scalar(spec.query(func.count()).select_from(spec.model).where(col == day))
        n_7 = s.scalar(spec.query(func.count()).select_from(spec.model).where(col > day - timedelta(days=7),
                                                                                col <= day))  # fmt: skip
    silent = weekdays_between(last, day) if last is not None else None
    status = "vide" if total == 0 else "en_retard" if (silent or 0) > spec.max_silent_weekdays else "ok"
    return SourceState(spec.key, spec.label, spec.family, spec.cadence, spec.coverage, spec.feeder, int(n_day or 0),
                       int(n_7 or 0), total, first, last, silent, status)  # fmt: skip


def snapshot(s: Session, day: date, now: datetime | None = None) -> list[SourceState]:
    """Every source's state for ``day``, stored in ``source_health`` (one row per day and source, idempotent)."""
    from app.db.models import SourceHealthRow

    now = now or datetime.now(UTC)
    out: list[SourceState] = []
    for spec in registry():
        with s.begin_nested():  # one unreadable table never hides the others
            st = state_of(s, spec, day)
            out.append(st)
            row = s.scalar(select(SourceHealthRow).where(SourceHealthRow.day == day,
                                                         SourceHealthRow.source == spec.key))  # fmt: skip
            row = row or SourceHealthRow(day=day, source=spec.key)
            row.status, row.items_day, row.items_7d, row.total = st.status, st.items_day, st.items_7d, st.total
            row.first_day, row.last_day, row.silent_weekdays, row.computed_at = (
                st.first,
                st.last,
                st.silent_weekdays,
                now,
            )
            s.add(row)
    s.flush()
    return out


def newly_late(s: Session, day: date, states: list[SourceState]) -> list[SourceState]:
    """Sources late today that were not late on the previous snapshot (one alert per silence, not per day)."""
    from app.db.models import SourceHealthRow

    late = [x for x in states if x.status == "en_retard"]
    if not late:
        return []
    prev_day = s.scalar(select(func.max(SourceHealthRow.day)).where(SourceHealthRow.day < day))
    if prev_day is None:
        return late
    before = {r.source: r.status for r in s.scalars(select(SourceHealthRow).where(SourceHealthRow.day == prev_day))}
    return [x for x in late if before.get(x.key) != "en_retard"]


def summary_line(states: list[SourceState]) -> str:
    """One line for the daily review."""
    ok = sum(1 for x in states if x.status == "ok")
    late = [x.label for x in states if x.status == "en_retard"]
    empty = [x.label for x in states if x.status == "vide"]
    parts = [f"Sources : {ok} à jour sur {len(states)}"]
    if late:
        parts.append(f"en retard : {', '.join(late)}")
    if empty:
        parts.append(f"vides : {', '.join(empty)}")
    return " · ".join(parts)


def history(s: Session, day: date, days: int = 14) -> dict[str, list[tuple[date, str]]]:
    """Each source's stored status over the last ``days`` snapshots (oldest first), for the page's strip."""
    from app.db.models import SourceHealthRow

    rows = s.execute(
        select(SourceHealthRow.source, SourceHealthRow.day, SourceHealthRow.status)
        .where(SourceHealthRow.day > day - timedelta(days=days), SourceHealthRow.day <= day)
        .order_by(SourceHealthRow.day)
    )
    out: dict[str, list[tuple[date, str]]] = {}
    for src, d, st in rows:
        out.setdefault(src, []).append((d, st))
    return out


def latest(s: Session) -> tuple[date | None, list[dict[str, Any]]]:
    """The last stored snapshot (page, review): its day and its rows in the registry's order."""
    from app.db.models import SourceHealthRow

    day = s.scalar(select(func.max(SourceHealthRow.day)))
    if day is None:
        return None, []
    rows = {r.source: r for r in s.scalars(select(SourceHealthRow).where(SourceHealthRow.day == day))}
    out = []
    for spec in registry():
        r = rows.get(spec.key)
        if r is None:
            continue
        out.append({"key": spec.key, "label": spec.label, "family": spec.family, "family_label": FAMILIES[spec.family],
                    "cadence": spec.cadence, "coverage": spec.coverage, "feeder": spec.feeder, "status": r.status,
                    "status_label": STATUS_LABELS.get(r.status, r.status), "items_day": r.items_day,
                    "items_7d": r.items_7d, "total": r.total, "first": r.first_day, "last": r.last_day,
                    "silent_weekdays": r.silent_weekdays})  # fmt: skip
    return day, out
