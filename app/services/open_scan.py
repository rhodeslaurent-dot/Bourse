"""docs/06 D1 — the opening scans (ADR-025, built 18/11/2026 at the user's request: « a large value up 7 % at the open
after very good results: there is an opportunity on a move; I judge the bias and the strategy »; alert on the page and
by Telegram, user's choice of the same day).

Every 10 minutes after the open of Europe and of New York: the universe values with a **catalyst since the previous
close** — results published (AMF communiqué, SEC 8-K 2.02) or a news item classified positive, strong or medium — get
a quote of the day (open, high, low, last, previous close, volume); the D1 detector reads it, and the usual engine
decides: admission (C ≥ ``scoring.d1_min_c`` for D1), Python sizing (rule 7), caps, circuit breaker and data
freshness (rule 2: on a delayed quote, « à vérifier », no order sheet). Each new card is announced once by Telegram
(P2) and expires ``expire_minutes_after_scan`` later, at the latest at ``expire_latest_local`` (venue time).
Only in the availability modes of ``availability.d1_allowed_modes`` (docs/02 §2.3: a decision to take at once)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Instrument, NewsItem, OfficialFiling, PriceEod, Proposal, Signal
from app.domain.detectors import GapSnapshot
from app.domain.timeutil import PARIS, REUNION, from_store
from app.services.news import about

DETECTOR = "d1_gap_catalyst"
US = frozenset({"XNYS", "XNAS", "XASE", "ARCX", "BATS"})


@dataclass
class OpenScanSettings:
    enabled: bool = False
    catalyst_hours: int = 20  # since the previous close: results the evening before, news of the night
    news_magnitudes: tuple[str, ...] = ("forte", "moyenne")
    max_candidates: int = 80
    expire_minutes: int = 30
    expire_latest_local: str = "11:00"
    allowed_modes: tuple[str, ...] = ("disponible",)


def settings(config: Any) -> OpenScanSettings:
    raw = ((getattr(config.params, "detectors", None) or {}).get(DETECTOR)) or {}
    st = OpenScanSettings(enabled=bool(raw.get("enabled", False)))
    for key, attr in (("catalyst_hours", "catalyst_hours"), ("max_candidates", "max_candidates"),
                      ("expire_minutes_after_scan", "expire_minutes")):  # fmt: skip
        if raw.get(key) is not None:
            setattr(st, attr, int(raw[key]))
    if raw.get("news_magnitudes"):
        st.news_magnitudes = tuple(raw["news_magnitudes"])
    if raw.get("expire_latest_local"):
        st.expire_latest_local = str(raw["expire_latest_local"])
    av = getattr(config.params, "availability", None)
    if av is not None and getattr(av, "d1_allowed_modes", None):
        st.allowed_modes = tuple(str(m) for m in av.d1_allowed_modes)
    return st


def candidates(s: Session, now: datetime, mics: set[str], st: OpenScanSettings) -> dict[str, str]:
    """{isin: the catalyst, as a plain fact} for the universe values of ``mics``."""
    since = now - timedelta(hours=st.catalyst_hours)
    q_univ = select(Instrument.isin, Instrument.name).where(
        Instrument.in_universe.is_(True), Instrument.active.is_(True), Instrument.mic.in_(mics)
    )
    names: dict[str, str] = {str(i): str(nm) for i, nm in s.execute(q_univ)}
    universe = set(names)
    out: dict[str, str] = {}
    q = select(OfficialFiling).where(OfficialFiling.family == "resultats", OfficialFiling.market_timestamp >= since)
    for f in s.scalars(q.order_by(OfficialFiling.market_timestamp)):
        if f.isin in universe and f.market_timestamp is not None:
            at = from_store(f.market_timestamp).astimezone(PARIS)
            out[f.isin] = f"résultats publiés le {at:%d/%m à %H:%M} (heure de Paris)"
    for n in s.scalars(select(NewsItem).where(NewsItem.fetched_at >= since).order_by(NewsItem.fetched_at)):
        c = n.classification or {}
        if c.get("direction") != "positif" or c.get("magnitude") not in st.news_magnitudes:
            continue
        for isin in n.isins or []:
            if isin in universe and isin not in out and about(n, isin, names.get(isin)):
                out[isin] = f"actualité positive ({c.get('magnitude')}) : {n.title[:90]}"
    return dict(list(out.items())[: st.max_candidates])


def _adv_shares(s: Session, isin: str, before: Any) -> float | None:
    rows = s.scalars(select(PriceEod.volume).where(PriceEod.isin == isin, PriceEod.day < before)
                     .order_by(PriceEod.day.desc()).limit(20)).all()  # fmt: skip
    vols = [float(v) for v in rows if v]
    return sum(vols) / len(vols) if len(vols) >= 10 else None


def snapshots(
    s: Session,
    calendars: Any,
    rows: list[dict[str, Any]],
    by_symbol: dict[str, str],
    catalysts: dict[str, str],
    now: datetime,
) -> tuple[dict[str, GapSnapshot], dict[str, Any]]:
    """Today's session so far of each quoted value (GapSnapshot) and its quote for the engine, its status kept (rule
    2: a quote delayed by 15 min stays delayed, whatever the download time). A quote of an earlier session is left
    out (before the open, the provider still gives yesterday's close)."""
    from app.data.dto import MarketPerimeter, PriceType, Quote

    gaps: dict[str, GapSnapshot] = {}
    quotes: dict[str, Any] = {}
    for r in rows:
        code = r["symbol"].split(".")[0]  # EODHD answers « NVDA » for « NVDA.US » in a list call
        isin = by_symbol.get(r["symbol"]) or next(
            (i for sym, i in by_symbol.items() if sym.split(".")[0] == code), None
        )
        inst = s.get(Instrument, isin) if isin else None
        venue = getattr(calendars, "venues", {}).get(inst.mic) if inst is not None else None
        if inst is None or venue is None or isin is None:
            continue
        local = r["market_timestamp"].astimezone(venue.tz)
        if local.date() != now.astimezone(venue.tz).date():
            continue
        opened = datetime.combine(local.date(), venue.open_time, tzinfo=venue.tz)
        closed = datetime.combine(local.date(), venue.close_time, tzinfo=venue.tz)
        if local <= opened:
            continue
        elapsed = min(1.0, (local - opened).total_seconds() / max((closed - opened).total_seconds(), 1.0))
        gaps[isin] = GapSnapshot(r["previousClose"], r["open"], r["high"], r["low"], r["close"], r.get("volume"),
                                 _adv_shares(s, isin, local.date()), elapsed, inst.adv_eur_20,
                                 catalysts.get(isin))  # fmt: skip
        quotes[isin] = Quote(source="eodhd_rest", market_perimeter=MarketPerimeter.PRIMARY,
                             received_at=r["received_at"], processed_at=datetime.now(UTC),
                             data_status=r["data_status"], symbol=r["symbol"], isin=isin,
                             market_timestamp=r["market_timestamp"], price_type=PriceType.LAST, last=r["close"],
                             volume_cum=int(r["volume"]) if r.get("volume") else None,
                             currency=inst.currency, declared_delay_minutes=15)  # fmt: skip
    return gaps, quotes


def _fr(x: float, fmt: str) -> str:
    return format(x, fmt).replace(".", ",")


_RATIO = re.compile(r"ratio d'admission ([0-9.]+) < ([0-9.]+)")


def plain(reason: str) -> str:
    """An engine reason, without its prefix, the admission ratio said as a score in percent."""
    text = reason.split(" : ", 1)[-1] if reason.startswith(("WATCH", "BLOQUÉ")) else reason
    m = _RATIO.search(text)
    if m:
        return f"score {float(m.group(1)):.0%} sous le seuil de {float(m.group(2)):.0%}".replace("%", " %")
    return text


def alert_lines(p: Proposal, sig: Signal, name: str, status: str) -> list[str]:
    """≤ 12 lines, plain French: the move, the catalyst, the plan or why not, the size, what to check, the risk."""
    ev = sig.evidence or {}
    gap, pace = ev.get("ecart_ouverture"), ev.get("rythme_volume")
    head = name
    if isinstance(gap, int | float):
        head += f" : ouverture {_fr(gap * 100, '+.1f')} %"
    if isinstance(pace, int | float):
        head += f", volume {_fr(pace, '.1f')} fois l'habituel"
    lines = [head]
    if ev.get("catalyseur"):
        lines.append(f"Catalyseur : {ev['catalyseur']}")
    p_max = (p.details or {}).get("p_max") or sig.entry_high
    lines.append(f"Plan : achat au-dessus de {_fr(float(sig.entry_low or 0), '.2f')} (plus haut du jour), "
                 f"{_fr(float(p_max), '.2f') if p_max else '—'} au plus ; stop {_fr(float(p.stop or 0), '.2f')} "
                 "(sous le plus bas du jour)")  # fmt: skip
    if p.action == "BUY" and p.shares:
        lines.append(
            f"Taille calculée par l'outil : {p.shares} titre(s), perte {p.risk_eur or 0:.0f} € si le stop sert"
        )
    else:
        why = [plain(b) for b in ((p.gates or {}).get("blocked") or [])][:2]
        lines.append("L'outil ne prépare pas d'ordre" + (f" : {'; '.join(why)[:160]}" if why else ""))
    if status != "realtime" or p.to_verify:
        lines.append("Cours différé d'environ 15 min : vérifie le cours chez ton courtier avant toute décision")
    lines += ["Mouvement non mesuré par l'outil : incertain, risque de perte significatif — à toi de juger",
              "Fiche : /opportunites"]  # fmt: skip
    return lines[:12]


def _expiry(now: datetime, tz: Any, st: OpenScanSettings) -> datetime:
    hh, mm = (int(x) for x in st.expire_latest_local.split(":"))
    latest = datetime.combine(now.astimezone(tz).date(), time(hh, mm), tzinfo=tz)
    return min(now + timedelta(minutes=st.expire_minutes), max(latest, now + timedelta(minutes=5)))


def run(
    s: Session, config: Any, calendars: Any, now: datetime, mics: set[str], alerts: Any = None,
    mode: str = "disponible", provider: Any = None,
) -> tuple[int, str]:  # fmt: skip
    """(new D1 cards, note for ``jobs_runs``)."""
    from app.notify.base import Event
    from app.services.engine import enabled, scan
    from app.services.instruments import eodhd_symbols_for, name_of

    st = settings(config)
    if not st.enabled or not enabled(config):
        return 0, f"écart d'ouverture désactivé (detectors.{DETECTOR}.enabled ou moteur)"
    if mode not in st.allowed_modes:
        return 0, f"mode « {mode} » : scan d'ouverture réservé aux modes {', '.join(st.allowed_modes)} (docs/02 §2.3)"
    open_now = set()
    for m in mics:
        venue = getattr(calendars, "venues", {}).get(m)
        try:
            if venue is not None and venue.is_open_at(now):
                open_now.add(m)
        except ValueError:  # calendar of the year not loaded: never a guess
            continue
    if not open_now:
        return 0, "aucune place concernée ouverte"
    cats = candidates(s, now, open_now, st)
    if not cats:
        return 0, "aucune valeur avec catalyseur depuis la clôture précédente"
    by_symbol = eodhd_symbols_for(list(cats))
    if provider is None:
        from app.data.providers.eodhd import eodhd_from

        provider = eodhd_from(config)
    rows = provider.day_quotes(list(by_symbol))
    gaps, quotes = snapshots(s, calendars, rows, by_symbol, cats, now)
    moved = sum(1 for g in gaps.values() if g.prev_close and g.open / g.prev_close - 1 >= 0.03)
    at = f"{now.astimezone(REUNION):%H:%M} Réunion"
    if not gaps:
        return 0, f"{at} : {len(cats)} valeur(s) avec catalyseur, aucun cours de la séance du jour"
    day = now.astimezone(PARIS).date()
    q_d1 = select(Signal.id).where(Signal.detector == DETECTOR, Signal.ts >= now - timedelta(hours=20))
    before = set(s.scalars(q_d1))
    rep = scan(s, config, day, isins=list(gaps), now=now, quotes=quotes, gaps=gaps)
    new_ids = set(s.scalars(q_d1)) - before
    sigs = {x.id: x for x in s.scalars(select(Signal).where(Signal.id.in_(new_ids)))} if new_ids else {}
    props = list(s.scalars(select(Proposal).where(Proposal.signal_id.in_(list(sigs))))) if sigs else []
    for prop in props:
        sig = sigs[prop.signal_id]
        inst = s.get(Instrument, prop.isin)
        venue = getattr(calendars, "venues", {}).get(inst.mic) if inst is not None else None
        prop.expires_at = _expiry(now, venue.tz if venue is not None else PARIS, st)
        if alerts is not None:
            q = quotes.get(prop.isin)
            status = str(getattr(getattr(q, "data_status", None), "value", "delayed"))
            name = name_of(prop.isin) or prop.isin
            alerts.emit(s, Event("P2", "d1_gap", f"Mouvement à l'ouverture — {name}",
                                 alert_lines(prop, sig, name, status), isin=prop.isin), mode, True, now)  # fmt: skip
    s.flush()
    buys = sum(1 for x in props if x.action == "BUY")
    return len(props), (f"{at} : {len(cats)} valeur(s) avec catalyseur, {len(gaps)} cotée(s) aujourd'hui, {moved} en "
                        f"écart ≥ 3 % ; {len(props)} fiche(s) nouvelle(s) dont {buys} achat(s)"
                        + (f" ; erreurs : {'; '.join(rep.errors[:2])}" if rep.errors else ""))  # fmt: skip


def us_mics(calendars: Any) -> set[str]:
    return {m for m in getattr(calendars, "venues", {}) if m in US}


def europe_mics(calendars: Any) -> set[str]:
    return {m for m in getattr(calendars, "venues", {}) if m not in US}
