"""Live quotes for display (user request of 28/10/2026): the prices shown on the opportunities and watchlist
pages are refreshed as often as the sources allow, without extra cost.

- **Saxo** first, for instruments whose Uic is known (resolved lazily through the read-only reference endpoint
  ``ref/v1/instruments`` by ISIN, the listing whose venue is the instrument's MIC — never a guess), at most every
  ``live_quotes.saxo_seconds``. Saxo OpenAPI calls are free; without the Euronext L1 subscription Saxo answers
  delayed quotes (``DelayedByMinutes``) or ``NoAccess`` — the status says which.
- **EODHD REST** for the others (one API call per symbol), at most every ``live_quotes.eodhd_minutes``, or on a
  manual « Rafraîchir les cours » no closer than ``live_quotes.manual_min_seconds`` to the previous one, within a
  daily display budget ``live_quotes.eodhd_daily_calls_max``. Budget spent → said so, last values kept.
- Outside the session of the instrument's venue (per-MIC calendar), nothing is fetched: the last close is shown
  (« clôture du jj/mm »).

Every value keeps its source, market timestamp, reception time and status: a 15-min delayed quote stays
« différé » however recent the download (rule 2). Unavailable → « indisponible », never an estimate. These prices
are for display and for the intraday re-evaluation only: they never enter a proposal's decision snapshot.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.dto import DataStatus, Quote, classify_status
from app.db.models import Instrument, PriceEod
from app.domain.timeutil import PARIS, REUNION, format_dual

log = logging.getLogger("bourse.live_quotes")

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "saxo_seconds": 15,
    "eodhd_minutes": 15,
    "manual_min_seconds": 60,
    "eodhd_daily_calls_max": 15000,
}
MAX_UIC_LOOKUPS_PER_REFRESH = 20  # lazy Uic resolution: a few per refresh, never a burst on the reference API


def live_params(config: Any) -> dict[str, Any]:
    """``params.yaml › live_quotes`` with the example's values as defaults (a file not yet updated by
    ``params-init --update`` keeps working)."""
    raw = getattr(config.params, "live_quotes", None) or {}
    return {**DEFAULTS, **(raw if isinstance(raw, dict) else {})}


def saxo_configured() -> bool:
    return bool(os.environ.get("SAXO_TOKEN_KEY") or os.environ.get("SAXO_ACCESS_TOKEN"))


# --- fetchers (injected in tests) ---------------------------------------------------------------------------

SaxoFetch = Callable[[dict[str, list[str]]], list[Quote]]  # {asset_type: [uic]} → quotes (symbol = uic)
SaxoResolve = Callable[[str, str], "str | None"]  # (isin, mic) → uic
EodhdFetch = Callable[[list[str]], dict[str, Quote]]  # isins → {isin: quote}; one call per symbol


@dataclass
class Fetchers:
    saxo_fetch: SaxoFetch | None = None
    saxo_resolve: SaxoResolve | None = None
    eodhd_fetch: EodhdFetch | None = None


def default_fetchers(config: Any) -> Fetchers:
    from app.scheduler.jobs.position_monitor import quotes_for

    fx = Fetchers(eodhd_fetch=lambda isins: quotes_for(isins, config))
    if not saxo_configured():
        return fx
    from app.data.providers.saxo import SaxoClient, SaxoPriceProvider, find_uic

    client = SaxoClient()
    provider = SaxoPriceProvider(client, stale_after=dict(config.params.data.stale_after_minutes))

    def saxo_fetch(by_type: dict[str, list[str]]) -> list[Quote]:
        out: list[Quote] = []
        for asset_type, uics in by_type.items():
            out += provider.fetch_quotes(uics, asset_type=asset_type)
        return out

    fx.saxo_fetch = saxo_fetch
    fx.saxo_resolve = lambda isin, mic: find_uic(client, isin, mic)
    return fx


# --- views ------------------------------------------------------------------------------------------------


def fmt_price(x: float | None) -> str:
    if x is None:
        return "—"
    digits = 2 if abs(x) >= 10 else 3 if abs(x) >= 1 else 4
    return f"{x:,.{digits}f}".replace(",", " ").replace(".", ",")


def fmt_pct(x: float | None) -> str:
    return "" if x is None else f"{x * 100:+.2f} %".replace(".", ",")


TONES = {"realtime": "green", "delayed": "amber", "stale": "red", "eod": "slate", "unavailable": "slate"}


@dataclass
class LiveView:
    isin: str
    session_open: bool
    quote: Quote | None
    last: float | None
    status: str
    label: str
    source: str | None
    market_timestamp: datetime | None
    received_at: datetime | None
    variation: float | None
    plausible: bool = True
    note: str = ""
    currency: str | None = None

    def as_json(self) -> dict[str, Any]:
        mts = self.market_timestamp
        return {
            "isin": self.isin,
            "cours": self.last,
            "cours_txt": fmt_price(self.last),
            "devise": self.currency,
            "variation": self.variation,
            "variation_txt": fmt_pct(self.variation),
            "statut": self.status,
            "statut_txt": self.label,
            "ton": TONES.get(self.status, "slate") if self.plausible else "amber",
            "source": self.source,
            "heure": f"{mts.astimezone(REUNION):%H:%M}" if mts else "",
            "heure_txt": format_dual(mts) if mts else "",
            "recu_a": self.received_at.isoformat() if self.received_at else None,
            "vraisemblable": self.plausible,
            "note": self.note,
            "seance_ouverte": self.session_open,
        }


@dataclass
class RefreshResult:
    views: dict[str, LiveView]
    any_open: bool
    saxo_served: bool
    eodhd_used: int
    eodhd_max: int
    manual_requested: bool = False
    manual_accepted: bool = False
    message: str = ""
    budget_exhausted: bool = False
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def eodhd_left(self) -> int:
        return max(self.eodhd_max - self.eodhd_used, 0)

    @property
    def next_poll_s(self) -> int | None:
        """``saxo_seconds`` when a venue is open and Saxo serves a value; the EODHD cadence when only EODHD
        serves an open venue (the cache never calls it sooner); ``None`` when every venue is closed."""
        if not self.any_open or not self.params.get("enabled", True):
            return None
        if self.saxo_served:
            return int(self.params["saxo_seconds"])
        return int(self.params["eodhd_minutes"]) * 60


@dataclass
class _Entry:
    quote: Quote | None
    fetched_at: datetime


class LiveQuoteService:
    """Process-wide cache shared by the web pages and the ``intraday_reeval`` job (same process)."""

    def __init__(self, fetchers: Fetchers | None = None) -> None:
        self.fetchers = fetchers  # None → built from the config at first use
        self.cache: dict[str, _Entry] = {}
        self.saxo_checked: dict[str, datetime] = {}
        self.eodhd_checked: dict[str, datetime] = {}
        self.uic_misses: dict[str, date] = {}  # ISIN without a unique Saxo listing on its venue (retried next day)
        self.saxo_error_until: datetime | None = None
        self.eodhd_day: date | None = None
        self.eodhd_calls = 0
        self.last_manual: datetime | None = None
        self._lock = threading.Lock()

    # --- budget ---------------------------------------------------------------------------------------
    def _budget(self, now: datetime) -> None:
        day = now.astimezone(PARIS).date()
        if self.eodhd_day != day:
            self.eodhd_day, self.eodhd_calls = day, 0

    # --- main entry point -----------------------------------------------------------------------------
    def refresh(
        self,
        s: Session,
        config: Any,
        calendars: Any,
        isins: list[str],
        force: bool = False,
        now: datetime | None = None,
    ) -> RefreshResult:
        now = now or datetime.now(UTC)
        lq = live_params(config)
        isins = list(dict.fromkeys(isins))
        with self._lock:
            self._budget(now)
            if self.fetchers is None:
                self.fetchers = default_fetchers(config)
            rows = s.scalars(select(Instrument).where(Instrument.isin.in_(isins))) if isins else []
            insts = {i.isin: i for i in rows}
            mics = {i: _mic(insts.get(i), i) for i in isins}
            open_ = {i: _session_open(calendars, mics[i], now) for i in isins}
            res = RefreshResult(
                {}, any(open_.values()), False, self.eodhd_calls, int(lq["eodhd_daily_calls_max"]), params=lq
            )
            if force:
                res.manual_requested = True
                wait = int(lq["manual_min_seconds"])
                if self.last_manual is not None and now - self.last_manual < timedelta(seconds=wait):
                    left = wait - int((now - self.last_manual).total_seconds())
                    res.message = f"rafraîchissement manuel refusé : le précédent date de moins de {wait} s ({left} s)"
                else:
                    res.manual_accepted = True
                    self.last_manual = now
            live = [i for i in isins if open_[i]] if lq["enabled"] else []
            served = self._saxo(s, insts, live, now, lq, config) if live else set()
            res.saxo_served = bool(served)
            rest = [i for i in live if i not in served]
            self._eodhd(rest, now, lq, res)
            res.eodhd_used = self.eodhd_calls
            max_gap = float(getattr(config.params.data, "plausibility_max_gap_pct", None) or 0.05)
            stale_after = dict(config.params.data.stale_after_minutes)
            for i in isins:
                tz = calendars.venues[mics[i]].tz if mics[i] in getattr(calendars, "venues", {}) else PARIS
                res.views[i] = self._view(s, i, insts.get(i), open_[i], now, max_gap, stale_after, tz)
            if res.manual_accepted and not res.message:
                res.message = (
                    f"cours rafraîchis — budget EODHD d'affichage restant : {res.eodhd_left} / {res.eodhd_max}"
                )
            return res

    # --- Saxo -----------------------------------------------------------------------------------------
    def _saxo(
        self, s: Session, insts: dict[str, Instrument], isins: list[str], now: datetime, lq: dict[str, Any], config: Any
    ) -> set[str]:
        """ISINs served by Saxo this round (fresh value in cache, fetched now or less than ``saxo_seconds`` ago)."""
        fx = self.fetchers
        if fx is None or fx.saxo_fetch is None:
            return set()
        if self.saxo_error_until is not None and now < self.saxo_error_until:
            return {i for i in isins if self._fresh_saxo(i, now, lq)}
        lookups = 0
        for i in isins:
            inst = insts.get(i)
            if inst is None or inst.saxo_uic or fx.saxo_resolve is None or not inst.mic:
                continue
            if self.uic_misses.get(i) == now.date() or lookups >= MAX_UIC_LOOKUPS_PER_REFRESH:
                continue
            lookups += 1
            try:
                uic = fx.saxo_resolve(i, inst.mic)
            except Exception as exc:  # noqa: BLE001 — EODHD serves it; retried tomorrow
                log.warning("Uic Saxo de %s introuvable : %s", i, exc)
                uic = None
            if uic:
                inst.saxo_uic = uic
            else:
                self.uic_misses[i] = now.date()
        due: dict[str, list[str]] = {}
        uic_isin: dict[str, str] = {}
        for i in isins:
            inst = insts.get(i)
            if inst is None or not inst.saxo_uic:
                continue
            last = self.saxo_checked.get(i)
            if last is not None and now - last < timedelta(seconds=int(lq["saxo_seconds"])):
                continue
            due.setdefault("Etf" if inst.kind == "etf" else "Stock", []).append(inst.saxo_uic)
            uic_isin[inst.saxo_uic] = i
        if due:
            try:
                quotes = fx.saxo_fetch(due)
            except Exception as exc:  # noqa: BLE001 — EODHD is used meanwhile; the source is always shown
                log.warning("cours Saxo indisponibles : %s", exc)
                self.saxo_error_until = now + timedelta(seconds=60)
                quotes = []
            for i in uic_isin.values():
                self.saxo_checked[i] = now
            for q in quotes:
                i = uic_isin.get(q.symbol)
                if i is None:
                    continue
                if q.last is not None and q.data_status != DataStatus.UNAVAILABLE:
                    self.cache[i] = _Entry(q, now)
                else:  # NoAccess / no last price: not served by Saxo, EODHD takes over
                    self.saxo_checked[i] = now
        return {i for i in isins if self._fresh_saxo(i, now, lq)}

    def _fresh_saxo(self, isin: str, now: datetime, lq: dict[str, Any]) -> bool:
        e = self.cache.get(isin)
        return (
            e is not None
            and e.quote is not None
            and e.quote.source == "saxo"
            and now - e.fetched_at < timedelta(seconds=2 * int(lq["saxo_seconds"]))
        )

    # --- EODHD ----------------------------------------------------------------------------------------
    def _eodhd(self, isins: list[str], now: datetime, lq: dict[str, Any], res: RefreshResult) -> None:
        fx = self.fetchers
        if not isins or fx is None or fx.eodhd_fetch is None:
            return
        ttl = timedelta(minutes=int(lq["eodhd_minutes"]))
        due = [
            i
            for i in isins
            if res.manual_accepted or (self.eodhd_checked.get(i) is None or now - self.eodhd_checked[i] >= ttl)
        ]
        if not due:
            return
        left = max(int(lq["eodhd_daily_calls_max"]) - self.eodhd_calls, 0)
        if left < len(due):
            res.budget_exhausted = True
            res.message = (
                f"budget EODHD d'affichage épuisé pour aujourd'hui ({lq['eodhd_daily_calls_max']} appels) : "
                "derniers cours conservés"
            )
            due = due[:left]
        if not due:
            return
        self.eodhd_calls += len(due)  # one API call per symbol (EODHD counts each symbol of a request)
        try:
            got = fx.eodhd_fetch(due)
        except Exception as exc:  # noqa: BLE001 — no quote is better than a guessed one
            log.warning("cours EODHD indisponibles : %s", exc)
            got = {}
        for i in due:
            self.eodhd_checked[i] = now
            q = got.get(i)
            if q is not None and q.last is not None:
                self.cache[i] = _Entry(q, now)

    # --- view -----------------------------------------------------------------------------------------
    def _view(
        self,
        s: Session,
        isin: str,
        inst: Instrument | None,
        session_open: bool,
        now: datetime,
        max_gap: float,
        stale_after: dict[str, int],
        tz: Any = PARIS,
    ) -> LiveView:
        bars = list(s.execute(select(PriceEod.day, PriceEod.close, PriceEod.source).where(PriceEod.isin == isin)
                              .order_by(PriceEod.day.desc()).limit(2)))  # fmt: skip
        cur = inst.currency if inst else None
        e = self.cache.get(isin)
        q = e.quote if e else None
        if q is not None and q.last is not None and q.market_timestamp is not None:
            qday = q.market_timestamp.astimezone(tz).date()  # the session's date at the venue
            ref = next((b for b in bars if b.day < qday), None)
            variation = q.last / ref.close - 1 if ref and ref.close else None
            if not session_open and bars and bars[0].day >= qday:
                q = None  # the official close of that session is loaded: it is shown instead
            elif not session_open:
                return LiveView(isin, False, q, q.last, "eod", f"dernier cours du {qday:%d/%m}", q.source,
                                q.market_timestamp, q.received_at, variation, currency=cur)  # fmt: skip
            else:
                status = q.data_status
                if status in (DataStatus.REALTIME, DataStatus.DELAYED, DataStatus.STALE):
                    status = classify_status(q.market_timestamp, now, int(q.declared_delay_minutes or 0), stale_after)
                label = {
                    DataStatus.REALTIME: "temps réel",
                    DataStatus.DELAYED: f"différé {q.declared_delay_minutes or ''} min".replace("  ", " "),
                    DataStatus.STALE: "périmé",
                    DataStatus.EOD: "clôture",
                    DataStatus.UNAVAILABLE: "indisponible",
                }[status]
                from app.services.quotes import check

                chk = check(s, isin, q, max_gap, now)
                return LiveView(isin, True, q, q.last, status.value, label, q.source, q.market_timestamp,
                                q.received_at, variation, chk.plausible, chk.note, cur)  # fmt: skip
        if session_open:
            note = f"dernière clôture {fmt_price(bars[0].close)} ({bars[0].day:%d/%m})" if bars else ""
            return LiveView(isin, True, None, None, "unavailable", "indisponible", None, None, None, None,
                            note=note, currency=cur)  # fmt: skip
        if not bars:
            return LiveView(isin, False, None, None, "unavailable", "indisponible", None, None, None, None,
                            currency=cur)  # fmt: skip
        last = bars[0]
        variation = last.close / bars[1].close - 1 if len(bars) > 1 and bars[1].close else None
        return LiveView(isin, False, None, last.close, "eod", f"clôture du {last.day:%d/%m}", last.source, None,
                        None, variation, currency=cur)  # fmt: skip

    def quotes(
        self, s: Session, config: Any, calendars: Any, isins: list[str], now: datetime | None = None
    ) -> dict[str, Quote]:
        """Fresh quotes of the instruments whose venue is in session (``intraday_reeval``), same cache and cadence
        as the pages: Saxo every ``saxo_seconds``, EODHD at its ``eodhd_minutes`` cadence only."""
        res = self.refresh(s, config, calendars, isins, now=now)
        return {i: v.quote for i, v in res.views.items() if v.session_open and v.quote is not None}


def _mic(inst: Instrument | None, isin: str) -> str | None:
    if inst is not None and inst.mic:
        return inst.mic
    from app.services.instruments import _load as instruments_map

    return instruments_map().get(isin, {}).get("mic")


def _session_open(calendars: Any, mic: str | None, now: datetime) -> bool:
    """Unknown venue or year → closed (no fetch): never Paris by default, never a guess."""
    if mic is None or calendars is None or mic not in getattr(calendars, "venues", {}):
        return False
    try:
        return bool(calendars.get(mic).is_open_at(now))
    except (KeyError, ValueError):
        return False


_SERVICE: LiveQuoteService | None = None


def service() -> LiveQuoteService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = LiveQuoteService()
    return _SERVICE


def set_service(svc: LiveQuoteService | None) -> None:
    """Tests: install a service with fake fetchers (or reset with ``None``)."""
    global _SERVICE
    _SERVICE = svc
