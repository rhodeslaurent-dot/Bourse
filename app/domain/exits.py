"""Review of an open position (docs/06 §6.4 S1–S6, docs/07 §7.9.2, docs/22 §22.5). Pure.

Verdict per line: CONSERVER / ALLÉGER / VENDRE (a losing line is never reinforced; RENFORCER is only
*noted* as possible when the gain is ≥ ``pyramiding_min_gain_r`` R). The stop proposal follows
the invariant > placement > ratchet order and never goes down (except by a detached dividend).
An event a stop does not cover applies the exit rule *before* the event (framework §6.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.stops import StopDecision, Zone, next_stop


@dataclass(frozen=True)
class ExitParams:
    reduce_at_target1_pct: float  # risk.stop.reduce_at_target1_pct
    time_stop_sessions: int  # detectors.exits.time_stop_sessions_default
    rs_rank_exit: float  # detectors.exits.rs_rank_exit
    thesis_mm20_days: int  # detectors.exits.thesis_mm20_close_below_days
    earnings_blackout_hours: float  # risk.earnings_blackout_hours
    earnings_policy: str  # risk.earnings_policy_on_position: reduce_50 | sell | hold_if_gain_ge_1r
    wick_buffer_pct: float  # risk.stop.wick_buffer_pct
    pyramiding_min_gain_r: float = 1.0
    event_stop_distance_pct: float = 0.02  # events.stop_distance_pct


@dataclass(frozen=True)
class PositionFacts:
    entry_price: float  # average cost (PRU)
    stop: float | None  # current stop
    stop_initial: float | None
    close: float
    closes_below_mm20: int  # consecutive closes under the 20-day average (0 if above)
    rs_rank: float | None
    targets: list[float]
    sessions_held: int
    detector: str | None
    obstacles: list[float]  # resistance levels identified before the session
    supports: list[Zone]
    earnings_in_hours: float | None = None
    macro_event_in_hours: float | None = None  # next major macro event (central bank, CPI, NFP)
    dividend_today: float = 0.0


@dataclass
class PositionReview:
    verdict: str  # CONSERVER | ALLÉGER | VENDRE
    reduce_pct: float | None
    stop: StopDecision | None
    r_multiple: float | None
    gain_pct: float
    signals: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def review_position(f: PositionFacts, p: ExitParams) -> PositionReview:
    gain_pct = f.close / f.entry_price - 1 if f.entry_price else 0.0
    r0 = (f.entry_price - f.stop_initial) if f.stop_initial is not None and f.stop_initial < f.entry_price else None
    r_mult = (f.close - f.entry_price) / r0 if r0 else None
    rv = PositionReview("CONSERVER", None, None, r_mult, gain_pct)
    # stop proposal (R2/R3), then S2 break-even — never lower
    if f.stop is not None:
        rv.stop = next_stop(f.stop, f.close, f.obstacles, f.supports, p.wick_buffer_pct, f.dividend_today)
    else:
        rv.signals.append("S1 : position sans stop enregistré — à protéger immédiatement")
        rv.verdict = "VENDRE" if gain_pct < 0 else "CONSERVER"
    # S1 — stop crossed
    if f.stop is not None and f.close <= f.stop:
        rv.verdict = "VENDRE"
        rv.signals.append(f"S1 : clôture {f.close:.4g} ≤ stop {f.stop:.4g} — vérifier l'exécution chez le courtier")
        return rv
    # S4 — thesis invalidated
    if f.detector in ("d2_breakout", "d4_pullback") and f.closes_below_mm20 >= p.thesis_mm20_days:
        rv.verdict = "VENDRE"
        rv.signals.append(f"S4 : {f.closes_below_mm20} clôtures sous la moyenne 20 jours — thèse invalidée")
    if f.rs_rank is not None and f.rs_rank < p.rs_rank_exit:
        rv.verdict = "VENDRE"
        rv.signals.append(f"S4 : force relative au rang {f.rs_rank:.0f} < {p.rs_rank_exit:.0f} — leadership perdu")
    # S6 / R14 — event a stop does not cover: the exit rule applies before the event
    if f.earnings_in_hours is not None and 0 <= f.earnings_in_hours <= p.earnings_blackout_hours:
        weak = r_mult is None or r_mult < 1
        if p.earnings_policy == "sell" or (p.earnings_policy == "hold_if_gain_ge_1r" and weak):
            rv.verdict = "VENDRE"
            rv.signals.append("S6 : résultats imminents — un stop ne protège pas d'un gap ; sortie avant publication")
        elif p.earnings_policy == "reduce_50" and weak and rv.verdict != "VENDRE":
            rv.verdict, rv.reduce_pct = "ALLÉGER", 0.5
            rv.signals.append("S6 : résultats imminents et gain < 1 R — alléger de moitié avant publication")
    if (
        f.macro_event_in_hours is not None
        and 0 <= f.macro_event_in_hours <= 30
        and f.stop is not None
        and f.close > 0
        and (f.close - f.stop) / f.close <= p.event_stop_distance_pct
        and rv.verdict == "CONSERVER"
    ):
        rv.verdict = "VENDRE"
        rv.signals.append(
            f"R14 : annonce macro dans les 30 h et stop à {(f.close - f.stop) / f.close:.1%} du cours — "
            "la règle de sortie s'applique avant l'événement"
        )
    # S2 — first target reached
    if f.targets and f.close >= f.targets[0] and rv.verdict == "CONSERVER":
        rv.verdict, rv.reduce_pct = "ALLÉGER", p.reduce_at_target1_pct
        rv.signals.append(f"S2 : objectif 1 {f.targets[0]:.4g} atteint — alléger de {p.reduce_at_target1_pct:.0%}")
        if rv.stop is not None and rv.stop.stop < f.entry_price < f.close:
            rv.stop = StopDecision(f.entry_price, True, "S2 : stop remonté au prix de revient", rv.stop.notes)
    # S5 — time stop
    reached = bool(f.targets) and f.close >= f.targets[0]
    if f.sessions_held >= p.time_stop_sessions and not reached and rv.verdict == "CONSERVER":
        if r_mult is not None and r_mult < 0.5:
            rv.verdict = "VENDRE"
        else:
            rv.verdict, rv.reduce_pct = "ALLÉGER", 0.5
        rv.signals.append(f"S5 : {f.sessions_held} séances sans objectif atteint — sortie temps")
    if rv.verdict == "CONSERVER" and r_mult is not None and r_mult >= p.pyramiding_min_gain_r:
        rv.notes.append(f"gain de {r_mult:.1f} R : renforcement possible sur une nouvelle configuration du moteur")
    if gain_pct < 0:
        rv.notes.append("ligne perdante : jamais renforcée")
    return rv
