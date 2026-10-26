"""Conjunctural risk level and market gate (docs/07 §7.9.4, docs/22 R10, R12). Pure.

Decision of 22/10/2026: option C (published obstacle crossed at the close + the letter no longer
advising to wait), modulated by the conjunctural risk level, without any absolute block on a
market threshold. Unknown components are shown, never estimated, and do not count.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LEVELS = ("faible", "modere", "eleve")


@dataclass(frozen=True)
class Threshold:
    moderate: float
    high: float
    higher_is_worse: bool = True

    def level(self, value: float) -> str:
        if self.higher_is_worse:
            return "eleve" if value >= self.high else "modere" if value >= self.moderate else "faible"
        return "eleve" if value <= self.high else "modere" if value <= self.moderate else "faible"


@dataclass(frozen=True)
class ConjunctureParams:
    thresholds: dict[str, Threshold]  # vix_pct_1y, oat_bund_spread_bp, us10y_pct, index_5d_change_pct
    high_min_components: int


@dataclass
class Conjuncture:
    level: str
    components: dict[str, str]  # name → level or "inconnu"
    explanations: list[str] = field(default_factory=list)


def conjuncture(regime: str | None, values: dict[str, float | None], p: ConjunctureParams) -> Conjuncture:
    comps: dict[str, str] = {}
    expl: list[str] = []
    if regime is not None:
        comps["regime"] = {"red": "eleve", "orange": "modere"}.get(regime, "faible")
        expl.append(f"régime {regime} → {comps['regime']}")
    else:
        comps["regime"] = "inconnu"
    for name, th in p.thresholds.items():
        v = values.get(name)
        if v is None:
            comps[name] = "inconnu"
            expl.append(f"{name} : indisponible")
            continue
        comps[name] = th.level(v)
        expl.append(f"{name} {v:g} → {comps[name]}")
    known = [lv for lv in comps.values() if lv != "inconnu"]
    n_high = sum(lv == "eleve" for lv in known)
    if regime == "red" or n_high >= p.high_min_components:
        level = "eleve"
    elif any(lv in ("modere", "eleve") for lv in known):
        level = "modere"
    else:
        level = "faible"
    return Conjuncture(level, comps, expl)


@dataclass(frozen=True)
class GateState:
    state: str  # ouvert | restreint | restreint_plus
    reason: str


def market_gate(published_ok: bool | None, level: str, regime: str | None, letters_apply: bool = True) -> GateState:
    """``published_ok``: option C conditions (None = no recent publication → not met). ``letters_apply``:
    False on a venue the letters do not cover (docs/32) — the gate then follows the venue's regime and the
    conjunctural level only, never the conditions published about another market."""
    met = published_ok is True or not letters_apply
    cond = (
        "lettres non applicables à cette place"
        if not letters_apply
        else "conditions publiées remplies"
        if met
        else ("conditions publiées non remplies" if published_ok is False else "pas de publication récente")
    )
    if regime == "red" or (not met and level == "eleve"):
        return GateState(
            "restreint_plus", f"{cond}, risque conjoncturel {level}, régime {regime} : dérogation seulement"
        )
    if met and level in ("faible", "modere"):
        return GateState("ouvert", f"{cond}, risque conjoncturel {level}")
    return GateState("restreint", f"{cond}, risque conjoncturel {level}")


@dataclass(frozen=True)
class BrentParams:
    srd_restrict_above: float
    srd_normal_below: float
    stable_sessions: int


def srd_mode(brent_closes: list[float], previous: str, p: BrentParams) -> str:
    """R12 with hysteresis: above the high threshold → restricted; back to normal only after
    ``stable_sessions`` closes below the low threshold. No data → previous mode kept."""
    if not brent_closes:
        return previous
    if brent_closes[-1] > p.srd_restrict_above:
        return "restreint"
    recent = brent_closes[-p.stable_sessions :]
    if previous == "restreint":
        if len(recent) >= p.stable_sessions and all(c < p.srd_normal_below for c in recent):
            return "normal"
        return "restreint"
    return "normal"
