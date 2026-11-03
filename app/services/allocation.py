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


def _tax_rate(config: LoadedConfig) -> float:
    from app.services.engine import _tax

    return float(_tax(config).get("pfu_total_rate") or 0.0)


def core_series(s: Session, config: LoadedConfig, end: date) -> CoreSeries | None:
    from app.services.market_data import fx_to_eur

    g = allocation_params(config) or {}
    core, _ = _pockets(config)
    for isin in core:
        inst = s.get(Instrument, isin)
        rows = list(s.scalars(select(PriceEod).where(PriceEod.isin == isin, PriceEod.day <= end)))
        if inst is None or not rows:
            continue
        cur = inst.currency or "EUR"
        prices = {}
        for r in rows:
            fx = fx_to_eur(s, cur, r.day)
            if fx is not None:
                prices[r.day] = r.close * fx
        if prices:
            return CoreSeries(prices, f"ETF du cœur {inst.name or isin} ({isin}), clôtures", False)
    name = str(g.get("proxy_series") or "msci_world")
    pts = list(s.scalars(select(MacroPoint).where(MacroPoint.indicator == name, MacroPoint.day <= end)))
    if not pts:
        return None
    prices = {}
    for p in pts:
        fx = fx_to_eur(s, "USD", p.day)  # URTH is quoted in USD
        if fx is not None:
            prices[p.day] = p.value * fx
    if not prices:
        return None
    return CoreSeries(prices, f"proxy déclaré : série {name} (en USD converti en EUR, hors dividendes)", True)


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
        fx = fx_to_eur(s, (inst.currency if inst else None) or row.currency or "EUR", end) if bar else None
        out.append(Holding(row.isin, int(row.qty_held), bar.close * fx if bar and fx is not None else None))
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
    return (
        f"Partie active contre le cœur {since} : elle {verdict} le même argent placé dans le cœur "
        f"({'+' if (res.diff_eur or 0) >= 0 else ''}{_eur(res.diff_eur)}, rapport {pme}, {res.flows} ordre(s){tax}) "
        f"— référence : {src}"
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
        f"  lignes ouvertes (dernière clôture) : {_eur(res.nav_eur)}",
        f"  plus-values réalisées au CTO par année : {years or 'aucune'}",
        f"  impôt estimé (PFU, pertes reportées) : {_eur(res.tax_eur)}",
        f"  richesse de la partie active : {_eur(res.satellite_eur)} · même argent dans le cœur : {_eur(res.core_eur)}",
        "  méthode : équivalent marché public (Kaplan & Schoar 2005) ; dividendes non comptés des deux côtés",
    ]
    return out
