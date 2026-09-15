"""Universe and eligibility rules (docs/05, docs/08 §8.4 bis). Pure; thresholds from params."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

EU_EEA_PREFIXES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT",
    "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO",
}  # fmt: skip
TRAP_PREFIXES = {
    "JE",
    "GG",
    "BM",
    "CH",
    "GB",
    "US",
    "KY",
    "VG",
    "IM",
}  # Jersey, Guernsey, Bermuda, Swiss, UK, US, Cayman…


class SrdStatus(StrEnum):
    COMPLET = "complet"
    LONG_ONLY = "long_only"
    NONE = "none"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PeaEligibility:
    eligible: bool | None  # None = unknown → never proposed for the PEA
    confidence: float
    source: str
    reason: str


def pea_regulatory_eligibility(
    isin: str,
    country_of_domicile: str | None,
    eligible_countries: list[str],
    broker_flag: bool | None = None,
) -> PeaEligibility:
    """Presumption from the domicile, ISIN prefix as fallback, broker flag as confirmation.
    A company listed in Paris but domiciled in Jersey/Bermuda/Switzerland/UK is *not* eligible."""
    prefix = isin[:2].upper()
    allowed = set(eligible_countries)
    if broker_flag is True and (country_of_domicile in allowed or prefix in allowed):
        return PeaEligibility(True, 0.95, "broker_flag+domicile", "drapeau PEA courtier concordant")
    if broker_flag is False:
        return PeaEligibility(False, 0.9, "broker_flag", "drapeau PEA courtier négatif")
    if country_of_domicile:
        if country_of_domicile in allowed:
            return PeaEligibility(True, 0.8, "domicile", f"siège {country_of_domicile} (UE/EEE)")
        return PeaEligibility(False, 0.85, "domicile", f"siège {country_of_domicile} hors UE/EEE")
    if prefix in TRAP_PREFIXES:
        return PeaEligibility(False, 0.7, "isin_prefix", f"préfixe ISIN {prefix} (piège : domicile hors UE/EEE)")
    if prefix in allowed:
        return PeaEligibility(True, 0.6, "isin_prefix", f"préfixe ISIN {prefix} (présomption, à confirmer)")
    return PeaEligibility(None, 0.0, "unknown", "domicile inconnu")


def srd_status_from_rules(
    mic: str, market_cap_eur: float | None, adv_eur: float | None, full: dict[str, float], long_only: dict[str, float]
) -> SrdStatus:
    """Rule-based *presumption* (Euronext factsheet); the maintained list and the user confirmation prevail."""
    if mic != "XPAR":
        return SrdStatus.NONE
    if market_cap_eur is None or adv_eur is None:
        return SrdStatus.UNKNOWN
    if market_cap_eur >= full["min_market_cap_eur"] and adv_eur >= full["min_daily_volume_eur"]:
        return SrdStatus.COMPLET
    if adv_eur >= long_only["min_daily_volume_eur"]:
        return SrdStatus.LONG_ONLY
    return SrdStatus.NONE


@dataclass(frozen=True)
class UniverseFilters:
    min_adv_eur_cto: float
    min_adv_eur_pea: float
    min_market_cap_eur: float
    small_cap_threshold_eur: float
    min_price: float
    min_history_sessions: int
    blacklist: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class InstrumentFacts:
    isin: str
    mic: str
    price: float | None
    market_cap_eur: float | None
    adv_eur_20: float | None
    history_sessions: int
    suspended: bool = False
    takeover: bool = False


@dataclass(frozen=True)
class UniverseDecision:
    included: bool
    reasons: list[str]
    cto_ok: bool
    pea_liquidity_ok: bool
    small_cap: bool


def universe_decision(f: InstrumentFacts, filt: UniverseFilters) -> UniverseDecision:
    reasons: list[str] = []
    if f.isin in filt.blacklist:
        reasons.append("liste noire")
    if f.suspended:
        reasons.append("suspendu")
    if f.takeover:
        reasons.append("OPA en cours")
    if f.price is None or f.price < filt.min_price:
        reasons.append(f"prix < {filt.min_price} €")
    if f.market_cap_eur is None or f.market_cap_eur < filt.min_market_cap_eur:
        reasons.append(f"capitalisation < {filt.min_market_cap_eur / 1e6:.0f} M€")
    if f.history_sessions < filt.min_history_sessions:
        reasons.append(f"historique < {filt.min_history_sessions} séances")
    cto_ok = f.adv_eur_20 is not None and f.adv_eur_20 >= filt.min_adv_eur_cto
    pea_ok = f.adv_eur_20 is not None and f.adv_eur_20 >= filt.min_adv_eur_pea
    if not pea_ok:
        reasons.append(f"ADV20 < {filt.min_adv_eur_pea / 1e6:.1f} M€")
    small = f.market_cap_eur is not None and f.market_cap_eur < filt.small_cap_threshold_eur
    return UniverseDecision(not reasons, reasons, cto_ok, pea_ok, small)


@dataclass(frozen=True)
class MomentumCandidate:
    isin: str
    rs_rank: float
    close: float
    mm50: float | None
    mm200: float | None


def momentum_watchlist(cands: list[MomentumCandidate], rs_rank_min: float, max_size: int) -> list[MomentumCandidate]:
    """Leaders: rs_rank ≥ min, close > MM50 and MM200, MM50 > MM200; best ranks first, ≤ max."""
    ok = [
        c
        for c in cands
        if c.rs_rank >= rs_rank_min
        and c.mm50 is not None
        and c.mm200 is not None
        and c.close > c.mm50
        and c.close > c.mm200
        and c.mm50 > c.mm200
    ]
    ok.sort(key=lambda c: c.rs_rank, reverse=True)
    return ok[:max_size]
