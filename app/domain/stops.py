"""Stop rules of the management framework (docs/07 §7.9.2, docs/22 R1–R3). Pure.

1. Placement: stop = support zone low × (1 − wick buffer); never inside the zone nor at its top.
2. Invariant: a stop never goes down (only exception: a dividend detachment, by its amount).
3. Ratchet: a net close above an identified obstacle lifts the stop to obstacle × (1 − buffer).
Priority: invariant > placement > ratchet.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Zone:
    low: float
    high: float

    def contains(self, level: float) -> bool:
        return self.low <= level <= self.high


def place_stop(support: Zone, wick_buffer_pct: float) -> float:
    return support.low * (1 - wick_buffer_pct)


def placement_violation(stop: float, supports: list[Zone]) -> Zone | None:
    """The support zone the stop sits in (inside or at its top bound), if any."""
    for z in supports:
        if z.contains(stop):
            return z
    return None


def clear_of_supports(
    stop: float, price: float, supports: list[Zone], wick_buffer_pct: float, margin: float
) -> tuple[float, Zone | None]:
    """A stop in the test path of a support goes under it (user decision of 05/11/2026, « l'erreur Airbus »: a stop
    just above the MM50 or a pivot zone is hit by the first test of the support, the very move the support is
    meant to absorb). A support under ``price`` is in the path when the stop lies inside it or above its high by
    less than ``margin`` (in price, e.g. 0.5 × ATR14); the stop then becomes its low × (1 − wick buffer), again
    while the new stop lands in the path of a lower support. Returns (stop, the supports' span it was moved under —
    None when unchanged). A stop is only ever lowered here: the risk is recomputed on it (rule 7)."""
    cur, span = stop, None
    for _ in range(len(supports) + 1):
        hit = next(
            (z for z in sorted(supports, key=lambda z: -z.high) if z.low < price and z.low <= cur <= z.high + margin),
            None,
        )
        if hit is None:
            break
        new = place_stop(hit, wick_buffer_pct)
        if new >= cur:
            break
        cur = new
        span = Zone(hit.low, max(hit.high, span.high) if span else hit.high)
    return cur, span


@dataclass
class StopDecision:
    stop: float
    changed: bool
    reason: str
    notes: list[str] = field(default_factory=list)


def next_stop(
    current: float,
    close: float,
    obstacles: list[float],
    supports: list[Zone],
    wick_buffer_pct: float,
    dividend: float = 0.0,
) -> StopDecision:
    """Evening update of a position's stop. ``obstacles`` are resistance levels identified before
    the session; ``supports`` the zones under the price; ``dividend`` the amount detached today."""
    base = current - dividend if dividend > 0 else current
    notes: list[str] = []
    if dividend > 0:
        notes.append(f"détachement de {dividend:.4g} : stop ajusté de {current:.4g} à {base:.4g} (seule exception)")
    crossed = [o for o in obstacles if close > o]
    if not crossed:
        dec = StopDecision(base, base != current, "aucun obstacle franchi en clôture", notes)
    else:
        obstacle = max(crossed)
        candidate = obstacle * (1 - wick_buffer_pct)
        zone = placement_violation(candidate, supports)
        if candidate <= base:
            dec = StopDecision(
                base, base != current, f"cliquet {candidate:.4g} ≤ stop actuel : inchangé (invariant)", notes
            )
        elif zone is not None:
            dec = StopDecision(
                base,
                base != current,
                f"cliquet {candidate:.4g} écarté : dans la zone de support {zone.low:.4g}–{zone.high:.4g} "
                "(la règle de placement prime)",
                notes,
            )
        else:
            dec = StopDecision(
                candidate,
                True,
                f"cliquet : clôture {close:.4g} > obstacle {obstacle:.4g} → stop {candidate:.4g}",
                notes,
            )
    zone_now = placement_violation(dec.stop, supports)
    if zone_now is not None:
        dec.notes.append(
            f"stop dans la zone de support {zone_now.low:.4g}–{zone_now.high:.4g} : conservé (l'invariant prime), "
            "risque de sortie sur mèche accepté"
        )
    return dec


def refuse_lowering(current: float, requested: float) -> str | None:
    """Any request to lower a stop (or cancel a triggered one) is refused and logged."""
    if requested < current:
        return f"refusé : un stop ne descend jamais ({requested:.4g} < {current:.4g})"
    return None
