"""docs/28 — the daily risk level (« niveau de risque du jour »): a multiplier of the reference risk per
trade, recommended every morning from the market, the portfolio, the recent results, the measured edge
and the day's opportunities, with a plain explanation of each component. Applied by default; the user may
override it. Pure functions — no I/O, no LLM.

Principle (decision of the user, 25/10/2026): **gain first, then risk** — diluting the risk over 5–7
simultaneous lines is the main protection; the level only tilts the size, it never freezes the tool."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DialParams:
    min_level: float = 0.5
    max_level: float = 1.33  # 0.75 % × 1.33 ≈ 1 % of the capital per trade, never more
    market_red: float = 0.5
    market_high: float = 0.7  # high conjunctural risk
    market_orange: float = 0.85
    market_green_calm: float = 1.1  # green regime and low conjunctural risk
    target_lines_min: int = 5
    target_lines_max: int = 7
    underinvested_boost: float = 1.1  # few lines and several solid opportunities
    diversified_trim: float = 0.9  # many lines already: new ones a little smaller
    strong_setups_min: int = 3
    conviction_boost: float = 1.15  # per line: measured expectancy ≥ conviction_min_r
    conviction_min_r: float = 0.15


@dataclass(frozen=True)
class DialInput:
    regime: str | None  # green | orange | red
    conjuncture: str | None  # faible | modere | eleve
    open_lines: int
    strong_setups: int  # the day's setups with a technical reward/risk ≥ 2.5
    results_multiplier: float  # circuit breaker reduction (1.0 = none)
    results_reason: str
    edge_measured: bool  # at least one detector with a measured, positive expectancy


@dataclass(frozen=True)
class DialComponent:
    label: str
    factor: float
    why: str


@dataclass(frozen=True)
class Dial:
    level: float
    components: list[DialComponent] = field(default_factory=list)
    capped: str = ""

    def explanation(self, r_eur: float | None = None) -> list[str]:
        head = f"Niveau de risque conseillé : × {self.level:.2f}"
        if r_eur is not None:
            head += f" — risque par ligne {r_eur * self.level:.0f} € (au lieu de {r_eur:.0f} €)"
        lines = [head]
        lines += [f"{c.label} : × {c.factor:g} — {c.why}" for c in self.components]
        if self.capped:
            lines.append(self.capped)
        return lines


def recommend(inp: DialInput, p: DialParams) -> Dial:
    comps: list[DialComponent] = []
    if inp.regime == "red":
        comps.append(DialComponent("Marché", p.market_red, "régime rouge : on n'entre qu'avec prudence"))
    elif inp.conjuncture == "eleve":
        comps.append(DialComponent("Marché", p.market_high, "risque conjoncturel élevé (volatilité, taux, indice)"))
    elif inp.regime == "orange":
        comps.append(DialComponent("Marché", p.market_orange, "régime orange : tendance de fond moins porteuse"))
    elif inp.regime == "green" and inp.conjuncture == "faible":
        comps.append(DialComponent("Marché", p.market_green_calm, "régime vert et contexte calme : marché porteur"))
    else:
        comps.append(DialComponent("Marché", 1.0, "contexte normal"))
    if inp.results_multiplier < 1:
        comps.append(DialComponent("Résultats récents", inp.results_multiplier, inp.results_reason))
    else:
        comps.append(DialComponent("Résultats récents", 1.0, "pas de série de pertes"))
    if inp.open_lines < p.target_lines_min and inp.strong_setups >= p.strong_setups_min:
        comps.append(
            DialComponent(
                "Portefeuille",
                p.underinvested_boost,
                f"{inp.open_lines} ligne(s) ouverte(s) pour un objectif de {p.target_lines_min} à "
                f"{p.target_lines_max}, et {inp.strong_setups} opportunités solides aujourd'hui : diluer le risque",
            )
        )
    elif inp.open_lines >= p.target_lines_max:
        comps.append(
            DialComponent(
                "Portefeuille",
                p.diversified_trim,
                f"{inp.open_lines} lignes déjà ouvertes : nouvelles lignes un peu plus petites (plafond de risque)",
            )
        )
    else:
        comps.append(DialComponent("Portefeuille", 1.0, f"{inp.open_lines} ligne(s) ouverte(s)"))
    level = 1.0
    for c in comps:
        level *= c.factor
    capped = ""
    top = p.max_level if inp.edge_measured else 1.0
    if level > top:
        capped = f"plafonné à × {top:g} : " + (
            "maximum autorisé" if inp.edge_measured else "l'espérance des signaux n'est pas encore mesurée"
        )
        level = top
    if level < p.min_level:
        capped = f"jamais en dessous de × {p.min_level:g} : la dilution sur plusieurs lignes reste la protection"
        level = p.min_level
    return Dial(round(level, 3), comps, capped)


def conviction(expected_r: float | None, p: DialParams) -> tuple[float, str]:
    """Per line: a measured expectancy above ``conviction_min_r`` earns a slightly bigger line; never a penalty
    for an unmeasured signal (it is simply normal size)."""
    if expected_r is not None and expected_r >= p.conviction_min_r:
        return p.conviction_boost, f"espérance mesurée {expected_r:+.2f} R : conviction plus forte"
    return 1.0, ""
