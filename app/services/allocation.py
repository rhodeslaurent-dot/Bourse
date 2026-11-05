"""Lot M1 (docs/39, ADR-018) — the satellite against the core, from the stored executions, closes and exchange rates.

- **Satellite**: every execution (the confirmed line when a declaration was matched) except the core pocket
  (``pockets.dca.isins``, R17) and the tax pocket (``pockets.tax_harvest``, R18), and the open lines at the end date
  valued at their last close in EUR.
- **Core**: the first ISIN of ``pockets.dca.isins`` with stored closes; otherwise the macro series
  ``allocation.proxy_series`` (``msci_world``: the URTH ETF in USD, converted with the stored EUR/USD rate) — a
  **declared proxy**, said on every line.
- Costs of the core (``allocation.core_cost_pct``) and the flat tax in force (``tax.pfu_total_rate``) come from
  ``params.yaml``. Nothing is shown when a price is missing (« indisponible »), never an estimate.

Display only: the comparison never changes a proposal, a size or a verdict (ADR-018).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import Account, Instrument, MacroPoint, PositionRow, PriceEod, Trade
from app.domain.allocation import CoreComparison, Fill, Holding, compare_to_core
from app.domain.timeutil import PARIS, from_store


@dataclass(frozen=True)
class CoreSeries:
    prices: dict[date, float]  # closes in EUR
    label: str
    proxy: bool


BROKER_PRICE_MAX_DAYS = 10  # a broker's last price older than this is not used (the line stays « inconnu »)


def allocation_params(config: LoadedConfig) -> dict[str, Any] | None:
    """``allocation`` (dated, optional): absent or ``enabled: false`` → no comparison."""
    g = getattr(config.params, "allocation", None)
    if not isinstance(g, dict) or not g.get("enabled", True):
        return None
    return g


def _pockets(config: LoadedConfig) -> tuple[list[str], set[str]]:
    p = getattr(config.params, "pockets", None) or {}
    core = [str(x) for x in ((p.get("dca") or {}).get("isins") or []) if x]
    harvest = {str(x.get("isin")) for x in (p.get("tax_harvest") or []) if isinstance(x, dict) and x.get("isin")}
    return core, harvest


def core_isins(config: LoadedConfig) -> set[str]:
    """The core pocket (``pockets.dca.isins``, R17): out of the pilot capital, the caps, the stops and the circuit
    breaker — bought every month, never sold on a signal (docs/39 §5)."""
    return set(_pockets(config)[0])


def _tax_rate(config: LoadedConfig) -> float:
    from app.services.engine import _tax

    return float(_tax(config).get("pfu_total_rate") or 0.0)


def _proxy_prices(s: Session, name: str, end: date) -> dict[date, float]:
    """The declared proxy (``allocation.proxy_series``, URTH in USD) converted with the stored EUR/USD rate."""
    from app.services.market_data import fx_to_eur

    prices: dict[date, float] = {}
    for p in s.scalars(select(MacroPoint).where(MacroPoint.indicator == name, MacroPoint.day <= end)):
        fx = fx_to_eur(s, "USD", p.day)  # URTH is quoted in USD
        if fx is not None:
            prices[p.day] = p.value * fx
    return prices


def core_series(s: Session, config: LoadedConfig, end: date) -> CoreSeries | None:
    """The core's closes in EUR: the first ISIN of ``pockets.dca.isins`` with stored closes; before its first close
    (an ETF younger than the first order), the declared proxy's returns chained to it —
    said on the line; without the ETF, the declared proxy alone."""
    from app.domain.allocation import price_on_or_before
    from app.services.market_data import fx_to_eur

    g = allocation_params(config) or {}
    core, _ = _pockets(config)
    name = str(g.get("proxy_series") or "msci_world")
    proxy = _proxy_prices(s, name, end)
    plabel = f"série {name} (en USD converti en EUR, hors dividendes)"
    for isin in core:
        inst = s.get(Instrument, isin)
        rows = list(s.scalars(select(PriceEod).where(PriceEod.isin == isin, PriceEod.day <= end)))
        if inst is None or not rows:
            continue
        cur = inst.currency or "EUR"
        etf = {}
        for r in rows:
            fx = fx_to_eur(s, cur, r.day)
            if fx is not None:
                etf[r.day] = r.close * fx
        if not etf:
            continue
        label = f"ETF du cœur {inst.name or isin} ({isin}), clôtures"
        first = min(etf)
        anchor = price_on_or_before(proxy, first)
        before = {d: v for d, v in proxy.items() if d < first}
        if anchor and before:  # chain-linked: the proxy's own returns before the ETF existed, never a level guess
            k = etf[first] / anchor
            return CoreSeries(
                {**{d: v * k for d, v in before.items()}, **etf},
                f"{label} depuis le {first:%d/%m/%Y} ; avant : proxy déclaré {plabel}, raccordé",
                True,
            )
        return CoreSeries(etf, label, False)
    if not proxy:
        return None
    return CoreSeries(proxy, f"proxy déclaré : {plabel}", True)


def satellite_fills(s: Session, config: LoadedConfig, end: date) -> list[Fill]:
    core, harvest = _pockets(config)
    excluded = set(core) | harvest
    accounts = {a.id: a.type for a in s.scalars(select(Account))}
    out: list[Fill] = []
    for t in s.scalars(select(Trade).where(Trade.deleted_at.is_(None)).order_by(Trade.ts, Trade.id)):
        if t.isin in excluded:
            continue
        if t.kind == "declared" and (t.matched_trade_id is not None or "superseded?" in (t.tags or [])):
            continue  # the confirmed line carries the execution (as for the positions)
        day = from_store(t.ts).astimezone(PARIS).date()
        if day > end:
            continue
        out.append(Fill(day, accounts.get(t.account_id, "?"), t.isin, t.side, int(t.qty), float(t.price),
                        float(t.fx_rate or 1.0), float(t.fees or 0.0), float(t.ttf or 0.0)))  # fmt: skip
    return out


def satellite_holdings(s: Session, config: LoadedConfig, end: date) -> list[Holding]:
    from app.services.market_data import fx_to_eur

    core, harvest = _pockets(config)
    excluded = set(core) | harvest
    out: list[Holding] = []
    for row in s.scalars(select(PositionRow).where(PositionRow.qty_held > 0)):
        if row.isin in excluded:
            continue
        bar = s.scalar(
            select(PriceEod).where(PriceEod.isin == row.isin, PriceEod.day <= end).order_by(PriceEod.day.desc())
        )
        inst = s.get(Instrument, row.isin)
        fx = fx_to_eur(s, (inst.currency if inst else None) or row.currency or "EUR", end)
        if bar is not None and fx is not None:
            out.append(Holding(row.isin, int(row.qty_held), bar.close * fx))
            continue
        # 04/11/2026: a line without stored closes (not followed by the universe) — the broker's last price, dated
        # and recent, is a real quote; older or absent → unknown, never estimated
        at = from_store(row.last_price_at).date() if row.last_price_at else None
        recent = at is not None and 0 <= (end - at).days <= BROKER_PRICE_MAX_DAYS
        price = row.last_price * fx if row.last_price and recent and fx is not None else None
        out.append(Holding(row.isin, int(row.qty_held), price, "courtier" if price is not None else "clôture"))
    return out


def compare(s: Session, config: LoadedConfig, end: date) -> tuple[CoreComparison, CoreSeries | None]:
    g = allocation_params(config)
    if g is None:
        return CoreComparison("indisponible", "groupe allocation absent ou désactivé", None, end, 0), None
    series = core_series(s, config, end)
    fills = satellite_fills(s, config, end)
    if series is None:
        return CoreComparison("indisponible", "aucun cours du cœur (ni ETF du cœur ni série proxy)", None, end,
                              len(fills)), None  # fmt: skip
    tax = _tax_rate(config) if g.get("tax_estimate", True) else 0.0
    res = compare_to_core(fills, satellite_holdings(s, config, end), series.prices, end,
                          float(g.get("core_cost_pct", 0.0)), tax)  # fmt: skip
    if not res.ok and (res.reason or "").startswith("cours inconnu pour "):
        isins = (res.reason or "").removeprefix("cours inconnu pour ").split(", ")
        start = f"{res.start:%Y-%m-%d}" if res.start else "AAAA-MM-JJ"
        hint = (f" — charger son historique : backfill --isin {','.join(isins)} --from {start} ; "
                "ou vérifier que la ligne est encore détenue (relevé du courtier)")  # fmt: skip
        res = replace(res, reason=(res.reason or "") + hint)
    if not res.ok and res.start is not None and (res.reason or "").startswith("cours du cœur inconnu le"):
        cmd = "macro-backfill" if series.proxy else "backfill --isin <ISIN du cœur>"
        hint = f" — charger l'historique du cœur : {cmd} --from {res.start:%Y-%m-%d}"
        res = replace(res, reason=(res.reason or "") + hint)
    return res, series


def line(res: CoreComparison, series: CoreSeries | None) -> str:
    """One sentence for the review: the satellite against the core, or why it is unavailable."""
    if not res.ok:
        return f"Partie active contre le cœur : indisponible ({res.reason})"
    since = f"depuis le {res.start:%d/%m/%Y}" if res.start else ""
    pme = f"{res.pme:.2f}" if res.pme is not None else "—"
    verdict = "fait mieux que" if (res.diff_eur or 0) > 0 else "fait moins bien que"
    tax = ", impôt estimé déduit" if res.tax_eur else ""
    src = series.label if series else "—"
    broker = (f" ; au dernier cours du courtier faute de clôture stockée : {', '.join(res.broker_priced)}"
              if res.broker_priced else "")  # fmt: skip
    return (
        f"Partie active contre le cœur {since} : elle {verdict} le même argent placé dans le cœur "
        f"({'+' if (res.diff_eur or 0) >= 0 else ''}{_eur(res.diff_eur)}, rapport {pme}, {res.flows} ordre(s){tax}) "
        f"— référence : {src}{broker}"
    )


def _eur(x: float | None) -> str:
    """French money format: 1 234,56 €."""
    if x is None:
        return "—"
    return f"{x:,.2f}".replace(",", " ").replace(".", ",") + " €"


def report_lines(res: CoreComparison, series: CoreSeries | None) -> list[str]:
    """The detail for ``core-compare`` (printed on the user's PC only, never written to a committed file)."""
    out = [line(res, series)]
    if not res.ok or res.start is None:
        return out
    years = ", ".join(f"{y} {'+' if v >= 0 else ''}{_eur(v)}" for y, v in sorted(res.realized_by_year.items()))
    out += [
        f"  période : {res.start:%d/%m/%Y} → {res.end:%d/%m/%Y} · {res.flows} ordre(s) exécuté(s)",
        f"  achats de la partie active : {_eur(res.contributions_eur)} · ventes : {_eur(res.distributions_eur)}",
        f"  lignes ouvertes (dernière clôture, ou dernier cours du courtier s'il manque) : {_eur(res.nav_eur)}",
        f"  plus-values réalisées au CTO par année : {years or 'aucune'}",
        f"  impôt estimé (PFU, pertes reportées) : {_eur(res.tax_eur)}",
        f"  richesse de la partie active : {_eur(res.satellite_eur)} · même argent dans le cœur : {_eur(res.core_eur)}",
        "  méthode : équivalent marché public (Kaplan & Schoar 2005) ; dividendes non comptés des deux côtés",
    ]
    return out


@dataclass(frozen=True)
class CoreSetup:
    isin: str
    status: str  # « enregistré » | « déjà enregistré » | « introuvable » | « erreur »
    ticker: str | None = None
    name: str | None = None
    bars: int = 0
    first: date | None = None
    last: date | None = None
    note: str = ""


def setup_core(
    s: Session,
    provider: Any,
    config: LoadedConfig,
    start: date,
    end: date,
    exchanges: tuple[str, ...] = ("PA",),
    mic_of: dict[str, str] | None = None,
) -> list[CoreSetup]:
    """Lot C1 (docs/39 §5, D1): each ISIN of ``pockets.dca.isins`` registered as an instrument (ETF, out of the
    universe: never a signal card, never a cap) from the provider's ETF list of ``exchanges``, then its stored closes
    from ``start`` — the daily EOD job keeps them up to date (it loads every active instrument)."""
    from app.services.market_data import upsert_eod

    mic_of = mic_of or {"PA": "XPAR"}
    out: list[CoreSetup] = []
    listed: dict[str, list[dict[str, str]]] = {}
    for isin in sorted(core_isins(config)):
        inst = s.get(Instrument, isin)
        status = "déjà enregistré" if inst is not None and inst.ticker_eodhd else "enregistré"
        if inst is None or not inst.ticker_eodhd:
            row, ex = None, None
            for ex_ in exchanges:
                if ex_ not in listed:
                    try:
                        listed[ex_] = provider.exchange_symbols(ex_, ("etf",))
                    except Exception as exc:  # noqa: BLE001 — said, never guessed
                        out.append(CoreSetup(isin, "erreur", note=f"liste des ETF {ex_} : {str(exc)[:100]}"))
                        listed[ex_] = []
                row = next((r for r in listed[ex_] if str(r.get("Isin") or "").upper() == isin), None)
                if row is not None:
                    ex = ex_
                    break
            if row is None or ex is None:
                out.append(CoreSetup(isin, "introuvable", note=f"absent de la liste des ETF ({', '.join(exchanges)})"))
                continue
            if inst is None:
                inst = Instrument(isin=isin, name=str(row.get("Name") or isin), mic=mic_of.get(ex, ex))
                s.add(inst)
            inst.name = str(row.get("Name") or inst.name)
            inst.ticker_local, inst.exchange, inst.mic = str(row.get("Code")), ex, mic_of.get(ex, ex)
            inst.ticker_eodhd = f"{row.get('Code')}.{ex}"
            inst.currency, inst.kind = str(row.get("Currency") or "EUR"), "etf"
            inst.in_universe, inst.active = False, True
            s.flush()
        try:
            bars = provider.fetch_eod([inst.ticker_eodhd], start, end)
        except Exception as exc:  # noqa: BLE001
            out.append(CoreSetup(isin, "erreur", inst.ticker_eodhd, inst.name, note=f"historique : {str(exc)[:100]}"))
            continue
        n = upsert_eod(s, bars, {str(inst.ticker_eodhd): isin})
        days = sorted(b.day for b in bars)
        out.append(CoreSetup(isin, status, inst.ticker_eodhd, inst.name, n, days[0] if days else None,
                             days[-1] if days else None))  # fmt: skip
    return out
