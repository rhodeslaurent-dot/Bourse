"""Lots M1 and M2 (docs/39, ADR-018) — the active part of the portfolio (the « satellite ») measured against the same
money placed in the core ETF, the core's monthly reminder and the rebalancing bands. Pure: no I/O.

The research of 02/11/2026 (docs/39) found one robust way to « maximise gains » for a single investor: a world index
core bought every month and never sold on a signal; the active part only grows if it beats that core, net of costs
and of the estimated tax. This module measures it with the **public market equivalent** of Kaplan & Schoar (2005):

- every cash flow of the satellite is replayed in the core at the core's close of that day (on or before);
- a purchase of C € buys C × (1 − core cost) / I_t units of the core;
- a sale of D € returned to the investor would have grown as the core since: D × I_T / I_t;
- satellite wealth = value of the open lines at T + the sales grown as the core − the estimated tax on the gains
  realised on the securities account (PFU, losses offset within the year and carried forward), a PEA paying nothing
  before a withdrawal;
- core wealth = the purchases grown as the core; PME = satellite / core (> 1: the satellite did better).

Nothing is estimated: a missing price (core or line) makes the comparison « indisponible » with its reason. Dividends
are not counted on either side (the core's proxy is a price index; a capitalising core ETF would do better).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

TAXABLE_ACCOUNTS = frozenset({"cto_cash", "cto_srd"})


@dataclass(frozen=True)
class Fill:
    """One execution of the satellite (the confirmed line when a declaration was matched)."""

    day: date
    account: str  # pea | cto_cash | cto_srd
    isin: str
    side: str  # buy | sell
    qty: int
    price: float
    fx_rate: float = 1.0  # EUR per unit of the trade's currency
    fees_eur: float = 0.0
    ttf_eur: float = 0.0

    @property
    def cash_eur(self) -> float:
        """Cash paid for a purchase (price, fees, taxes), cash received for a sale (net of fees)."""
        gross = self.qty * self.price * self.fx_rate
        return gross + self.fees_eur + self.ttf_eur if self.side == "buy" else gross - self.fees_eur - self.ttf_eur


@dataclass(frozen=True)
class Holding:
    """An open line of the satellite at the end date: its quantity and last close in EUR (None = unknown)."""

    isin: str
    qty: int
    price_eur: float | None


@dataclass(frozen=True)
class CoreComparison:
    status: str  # ok | indisponible
    reason: str | None
    start: date | None
    end: date
    flows: int
    contributions_eur: float = 0.0
    distributions_eur: float = 0.0
    nav_eur: float | None = None
    tax_eur: float = 0.0
    satellite_eur: float | None = None
    core_eur: float | None = None
    pme: float | None = None
    diff_eur: float | None = None
    realized_by_year: dict[int, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def price_on_or_before(series: Mapping[date, float], day: date) -> float | None:
    """The close of ``day`` or of the last session before it; None before the series starts."""
    days = sorted(series)
    i = bisect_right(days, day)
    return series[days[i - 1]] if i else None


def realized_gains(fills: Sequence[Fill], taxable: frozenset[str] = TAXABLE_ACCOUNTS) -> dict[int, float]:
    """Gains realised per calendar year on the taxable accounts, at the weighted average cost (« PMP », acquisition
    fees included, sale fees deducted). A sale of more than the quantity held raises ``ValueError``."""
    qty: dict[tuple[str, str], int] = {}
    cost: dict[tuple[str, str], float] = {}
    out: dict[int, float] = {}
    for f in sorted(fills, key=lambda x: x.day):
        key = (f.account, f.isin)
        if f.side == "buy":
            qty[key] = qty.get(key, 0) + f.qty
            cost[key] = cost.get(key, 0.0) + f.cash_eur
            continue
        held = qty.get(key, 0)
        if f.qty > held:
            raise ValueError(f"vente de {f.qty} {f.isin} le {f.day:%d/%m/%Y} pour {held} détenus")
        avg = cost.get(key, 0.0) / held if held else 0.0
        gain = f.cash_eur - avg * f.qty
        qty[key] = held - f.qty
        cost[key] = cost.get(key, 0.0) - avg * f.qty
        if f.account in taxable:
            out[f.day.year] = out.get(f.day.year, 0.0) + gain
    return out


def tax_on_gains(by_year: Mapping[int, float], rate: float, carry_years: int = 10) -> float:
    """Flat tax on the net gain of each year; a net loss is carried forward to the next ``carry_years`` years."""
    tax = 0.0
    losses: list[tuple[int, float]] = []  # (year of origin, amount left)
    for year in sorted(by_year):
        net = by_year[year]
        losses = [(y, a) for y, a in losses if year - y <= carry_years and a > 0]
        if net < 0:
            losses.append((year, -net))
            continue
        for i, (y, a) in enumerate(losses):
            used = min(a, net)
            net -= used
            losses[i] = (y, a - used)
            if net <= 0:
                break
        tax += rate * max(0.0, net)
    return tax


def compare_to_core(
    fills: Sequence[Fill],
    holdings: Sequence[Holding],
    core_prices: Mapping[date, float],
    end: date,
    core_cost_pct: float = 0.0,
    tax_rate: float = 0.0,
) -> CoreComparison:
    """The satellite against the core (public market equivalent, module docstring). ``core_prices``: the core's closes
    in EUR by day; ``holdings``: the open lines at ``end``."""
    flows = sorted((f for f in fills if f.day <= end), key=lambda x: x.day)
    start = flows[0].day if flows else None
    if not flows:
        return CoreComparison("indisponible", "aucun ordre exécuté dans la partie active", None, end, 0)
    core_end = price_on_or_before(core_prices, end)
    if core_end is None:
        return CoreComparison("indisponible", "cours du cœur inconnu à la date de fin", start, end, len(flows))
    contrib = distrib = core_w = grown_out = 0.0
    for f in flows:
        it = price_on_or_before(core_prices, f.day)
        if it is None:
            return CoreComparison("indisponible", f"cours du cœur inconnu le {f.day:%d/%m/%Y}", start, end, len(flows))
        if f.side == "buy":
            contrib += f.cash_eur
            core_w += f.cash_eur * (1 - core_cost_pct) * core_end / it
        else:
            distrib += f.cash_eur
            grown_out += f.cash_eur * core_end / it
    missing = [h.isin for h in holdings if h.qty > 0 and h.price_eur is None]
    if missing:
        return CoreComparison(
            "indisponible", f"cours inconnu pour {', '.join(sorted(missing))}", start, end, len(flows)
        )
    nav = sum(h.qty * (h.price_eur or 0.0) for h in holdings if h.qty > 0)
    try:
        by_year = realized_gains(flows)
    except ValueError as exc:
        return CoreComparison("indisponible", str(exc), start, end, len(flows))
    tax = tax_on_gains(by_year, tax_rate)
    sat = nav + grown_out - tax
    return CoreComparison(
        "ok", None, start, end, len(flows), contrib, distrib, nav, tax, sat, core_w,
        sat / core_w if core_w > 0 else None, sat - core_w, by_year,
    )  # fmt: skip


@dataclass(frozen=True)
class Drift:
    pocket: str
    weight: float
    target: float

    @property
    def gap_pts(self) -> float:
        return (self.weight - self.target) * 100


def rebalance_check(weights: Mapping[str, float], targets: Mapping[str, float], band_pts: float) -> list[Drift]:
    """The pockets whose weight left their target by more than ``band_pts`` percentage points (docs/39 §5: new money
    goes first to the pocket under its target; the core is never sold)."""
    out = []
    for pocket, target in targets.items():
        w = float(weights.get(pocket, 0.0))
        if abs(w - target) * 100 > band_pts + 1e-9:
            out.append(Drift(pocket, w, target))
    return out


def first_session_of_month(day: date, is_open: Callable[[date], bool]) -> bool:
    """True when ``day`` is the first open session of its month (``is_open``: the venue's calendar predicate)."""
    if not is_open(day):
        return False
    d = day.replace(day=1)
    while d < day:
        if is_open(d):
            return False
        d += timedelta(days=1)
    return True


def crypto_gates(kind: str, options: Sequence[str], crypto_max_weight: float | None) -> tuple[list[str], list[str]]:
    """docs/39 §4 (crypto): a crypto ETP is never bought with leverage (no SRD) and never in the PEA (not eligible);
    the pocket is closed while ``allocation.crypto_max_weight`` is 0 (decision D5). Returns (accounts left, blocks).
    The weight cap itself is not checked yet: a pocket opened above 0 stays blocked until that check is coded."""
    if kind != "crypto_etp":
        return list(options), []
    left = [a for a in options if a not in ("pea", "cto_srd")]
    w = float(crypto_max_weight or 0.0)
    if w <= 0:
        return left, ["BLOQUÉ : poche crypto fermée (allocation.crypto_max_weight = 0, décision D5 de docs/39)"]
    return left, [
        f"BLOQUÉ : plafond de poids crypto ({w:.0%}) pas encore contrôlé par l'outil — aucune entrée d'ici là"
    ]


__all__ = [
    "CoreComparison",
    "Drift",
    "Fill",
    "Holding",
    "compare_to_core",
    "crypto_gates",
    "first_session_of_month",
    "price_on_or_before",
    "realized_gains",
    "rebalance_check",
    "tax_on_gains",
]
