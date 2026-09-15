"""Market regime (docs/06 §6.2): four components → green / orange / red. Pure."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Light(StrEnum):
    GREEN = "green"
    ORANGE = "orange"
    RED = "red"


@dataclass(frozen=True)
class RegimeParams:
    red_min_components: int
    breadth_green: float
    breadth_orange: float
    distribution_days_orange: int
    distribution_days_red: int
    vol_pct_orange: float
    vol_pct_red: float


@dataclass(frozen=True)
class RegimeInputs:
    cac_above_mm50: bool | None
    stoxx_above_mm50: bool | None
    breadth_mm50: float | None
    distribution_days: int | None
    vol_percentile: float | None


@dataclass(frozen=True)
class Regime:
    light: Light
    components: dict[str, Light | None]
    missing: list[str]

    @property
    def size_multiplier_key(self) -> str | None:
        return "regime_orange" if self.light == Light.ORANGE else None


def _indices(cac: bool | None, stoxx: bool | None) -> Light | None:
    if cac is None or stoxx is None:
        return None
    n = int(cac) + int(stoxx)
    return Light.GREEN if n == 2 else Light.ORANGE if n == 1 else Light.RED


def _breadth(b: float | None, p: RegimeParams) -> Light | None:
    if b is None:
        return None
    return Light.GREEN if b >= p.breadth_green else Light.ORANGE if b >= p.breadth_orange else Light.RED


def _dist(d: int | None, p: RegimeParams) -> Light | None:
    if d is None:
        return None
    return (
        Light.RED if d >= p.distribution_days_red else Light.ORANGE if d >= p.distribution_days_orange else Light.GREEN
    )


def _vol(v: float | None, p: RegimeParams) -> Light | None:
    if v is None:
        return None
    return Light.RED if v > p.vol_pct_red else Light.ORANGE if v >= p.vol_pct_orange else Light.GREEN


def compute_regime(inp: RegimeInputs, p: RegimeParams) -> Regime:
    comps: dict[str, Light | None] = {
        "indices_vs_mm50": _indices(inp.cac_above_mm50, inp.stoxx_above_mm50),
        "breadth_mm50": _breadth(inp.breadth_mm50, p),
        "distribution_days": _dist(inp.distribution_days, p),
        "volatility": _vol(inp.vol_percentile, p),
    }
    missing = [k for k, v in comps.items() if v is None]
    reds = sum(1 for v in comps.values() if v == Light.RED)
    oranges = sum(1 for v in comps.values() if v == Light.ORANGE)
    if reds >= p.red_min_components:
        light = Light.RED
    elif oranges >= 1 or reds >= 1:
        light = Light.ORANGE
    else:
        light = Light.GREEN
    return Regime(light, comps, missing)
