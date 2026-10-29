"""Intraday re-evaluation of an open proposal against a fresh quote (user request of 28/10/2026). Pure.

The admission of the proposal is recomputed with :func:`app.domain.admission.admit` on the values stored at
creation (first target, stop, setup, support / obstacle low, round-trip fees of the recommended account,
``admission.min_reward_risk``) and the stored maximum entry price as cap — never a new P_max:

- (a) a limit waiting for a pullback becomes reachable (price ≤ P_max, structure intact) → ``entree_atteinte``;
- (b) a breakout trigger is crossed while the price stays ≤ P_max → ``declencheur_franchi``;
- (c) the structure is lost (price < support / obstacle low) → ``configuration_degradee``: the tool reminds
  to cancel a pending limit order at the broker, it never cancels anything (rule 9);
- (d) the price runs above P_max → ``limite_depassee`` (never chase the price).

Rule 2: (a) and (b) are *actionable* only with a realtime quote whose market timestamp is at most
``data.buy_intraday_max_market_age_minutes`` old; otherwise they are « à vérifier » and give no order card.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.admission import AdmissionInput, Setup, Status, admit


class ReevalStatus(StrEnum):
    WAITING = "en_attente"  # breakout: trigger not crossed yet, structure intact, ≤ P_max
    ENTRY_REACHED = "entree_atteinte"  # (a)
    TRIGGER_CROSSED = "declencheur_franchi"  # (b)
    DEGRADED = "configuration_degradee"  # (c)
    ABOVE_PMAX = "limite_depassee"  # (d)
    NOT_TESTABLE = "non_testable"


ENTRY_STATUSES = (ReevalStatus.ENTRY_REACHED, ReevalStatus.TRIGGER_CROSSED)

LABELS: dict[str, str] = {
    ReevalStatus.WAITING.value: "déclencheur pas encore franchi",
    ReevalStatus.ENTRY_REACHED.value: "prix d'entrée atteint",
    ReevalStatus.TRIGGER_CROSSED.value: "déclencheur franchi sous la limite",
    ReevalStatus.DEGRADED.value: "configuration dégradée en séance : revoir l'ordre limite en attente",
    ReevalStatus.ABOVE_PMAX.value: "limite dépassée, ne pas courir après le prix",
    ReevalStatus.NOT_TESTABLE.value: "non testable",
}


@dataclass(frozen=True)
class ReevalInput:
    price: float
    data_status: str  # realtime | delayed | stale | eod
    market_timestamp: datetime | None
    now: datetime
    target: float | None
    stop: float | None
    setup: Setup
    level_low: float | None  # support low (pullback) / crossed obstacle low (breakout); None = not stored
    fee_fraction: float
    min_reward_risk: float
    p_max: float  # maximum entry price stored on the proposal (the limit the user may have posted)
    trigger: float | None  # breakout trigger (``details.entree.declencheur``)
    max_market_age_minutes: int  # data.buy_intraday_max_market_age_minutes
    delay_minutes: int | None = None  # declared delay of the source (for the « différé n min » wording)
    blocked: tuple[str, ...] = ()  # « BLOQUÉ : <règle> » gates of the proposal
    to_verify: bool = False


@dataclass
class Reevaluation:
    status: ReevalStatus
    actionable: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, bool]:
        return (self.status.value, self.actionable)


def fresh_realtime(data_status: str, market_timestamp: datetime | None, now: datetime, max_minutes: int) -> bool:
    """Rule 2: realtime **and** ``now − market_timestamp ≤ max`` (the download time never counts)."""
    if data_status != "realtime" or market_timestamp is None:
        return False
    return now - market_timestamp <= timedelta(minutes=max_minutes)


def classify(inp: ReevalInput) -> Reevaluation:
    if inp.stop is not None and inp.price <= inp.stop:  # never an entry at or under its own stop
        return Reevaluation(
            ReevalStatus.DEGRADED,
            False,
            [f"cours {inp.price:.4g} au niveau du stop {inp.stop:.4g} ou dessous, en séance (à confirmer en clôture)"],
        )
    adm = admit(
        AdmissionInput(
            inp.price,
            inp.target,
            inp.stop,
            inp.setup,
            inp.level_low,
            inp.fee_fraction,
            inp.min_reward_risk,
            inp.p_max,  # the stored limit caps the recomputed one: never a higher P_max than the proposal's
        )
    )
    if adm.status == Status.NOT_TESTABLE:
        return Reevaluation(ReevalStatus.NOT_TESTABLE, False, list(adm.reasons))
    if adm.status == Status.DEGRADED:
        # R5 is a rule on closes: an intraday last under the support is a warning, confirmed (or not) at the close
        intraday = [r.replace(" en clôture", " en séance (à confirmer en clôture)") for r in adm.reasons]
        return Reevaluation(ReevalStatus.DEGRADED, False, intraday)
    if adm.status == Status.REFUSED:
        return Reevaluation(ReevalStatus.ABOVE_PMAX, False, list(adm.reasons))
    pmax = adm.p_max if adm.p_max is not None else inp.p_max
    if inp.setup == Setup.BREAKOUT and inp.trigger is not None and inp.price < inp.trigger:
        return Reevaluation(
            ReevalStatus.WAITING, False, [f"cours {inp.price:.4g} sous le déclencheur {inp.trigger:.4g}"]
        )
    status = ReevalStatus.TRIGGER_CROSSED if inp.setup == Setup.BREAKOUT else ReevalStatus.ENTRY_REACHED
    reasons = [f"cours {inp.price:.4g} ≤ prix maximal {pmax:.4g}, structure intacte"]
    actionable = True
    if inp.level_low is None:  # the structure cannot be checked: never an order card on it
        actionable = False
        reasons.append("à vérifier : niveau de structure non enregistré, tenue du support non vérifiée")
    if not fresh_realtime(inp.data_status, inp.market_timestamp, inp.now, inp.max_market_age_minutes):
        actionable = False
        if inp.data_status == "delayed":
            delay = f" {inp.delay_minutes} min" if inp.delay_minutes else ""
            reasons.append(f"à vérifier : cours différé{delay}")
        else:
            reasons.append(f"à vérifier : cours {inp.data_status} (âge de marché > {inp.max_market_age_minutes} min)")
    if inp.blocked:
        actionable = False
        reasons += list(inp.blocked)
    if inp.to_verify:
        actionable = False
        reasons.append("proposition à vérifier : pas de fiche d'ordre")
    return Reevaluation(status, actionable, reasons)


def should_store(previous: tuple[str, bool] | None, new: Reevaluation) -> bool:
    """Only changes are stored (status or actionability), never every tick."""
    return previous != new.key


def notify_kind(previous: tuple[str, bool] | None, baseline: str | None, new: Reevaluation) -> str | None:
    """``reeval_entry`` when an entry becomes actionable (realtime quote) after a different state — a BUY
    already at its entry at creation is not announced again; ``reeval_degraded`` on entering (c), whatever the
    quote. ``previous`` is the last stored re-evaluation, ``baseline`` the status at creation."""
    if new.status in ENTRY_STATUSES and new.actionable:
        if previous is not None:  # a stored « à vérifier » entry confirmed by a realtime quote is announced too
            return None if previous[0] in ENTRY_STATUSES and previous[1] else "reeval_entry"
        return None if baseline in ENTRY_STATUSES else "reeval_entry"
    if new.status == ReevalStatus.DEGRADED:
        prev_status = previous[0] if previous is not None else baseline
        return None if prev_status == ReevalStatus.DEGRADED.value else "reeval_degraded"
    return None
