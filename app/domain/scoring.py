"""Score /100 and admission ratio (docs/06 §6.5), computed in pure Python with explanations.

Blocks: C catalysts 0–30, T technique 0–25, F fundamentals 0–20 (« non attribué » without data),
R risk/volatility/agenda 0–15, M sector/macro 0–10. The admission ratio of a detector only puts in
its denominator the blocks that apply to its strategy; C is a capped bonus for D2/D3/D4.
"""

from __future__ import annotations

from dataclasses import dataclass, field

BLOCK_MAX = {"C": 30, "T": 25, "F": 20, "R": 15, "M": 10}


@dataclass(frozen=True)
class ScoreInput:
    rs_rank: float | None
    above_mm50_mm200: bool
    mm50_above_mm200: bool
    above_rising_mm20: bool
    rvol: float | None
    clean_trigger: bool
    over_extended: bool
    resistance_within_3pct: bool
    stop_distance_adr: float | None  # (entry − stop) / (ADR20 × entry)
    earnings_within_10_days: bool | None  # None = date unknown
    adv_eur: float | None
    market_cap_eur: float | None
    gap_pct: float | None
    non_eur: bool
    regime: str | None
    sector_rs_pct: float | None = None
    major_macro_event_today: bool = False
    same_sector_positions: int = 0
    catalyst_strength: str | None = None  # forte | moyenne | faible
    catalyst_positive: bool = False
    catalyst_age_days: int | None = None
    external_confirmation: bool = False
    small_cap: bool = False


@dataclass
class Score:
    blocks: dict[str, float | None]
    total: float
    attainable: int
    admission_ratio: float
    confidence: int
    risk: str  # faible | moyen | eleve
    explanations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "blocs": self.blocks,
            "total": round(self.total, 1),
            "atteignable": self.attainable,
            "ratio_admission": round(self.admission_ratio, 3),
            "confiance": self.confidence,
            "risque": self.risk,
            "explications": self.explanations,
        }


def _clamp(v: float, hi: float) -> float:
    return max(0.0, min(hi, v))


def score(inp: ScoreInput, admission_blocks: list[str], c_bonus_cap: float, data_complete: bool = True) -> Score:
    ex: list[str] = []
    # C — catalysts
    c = 0.0
    if inp.catalyst_strength and inp.catalyst_positive:
        pts: float = {"forte": 15, "moyenne": 9, "faible": 4}[inp.catalyst_strength]
        if inp.catalyst_age_days is not None and inp.catalyst_age_days > 3:
            pts /= 2
            ex.append(f"catalyseur {inp.catalyst_strength} de plus de 3 jours → +{pts:g}")
        else:
            ex.append(f"catalyseur {inp.catalyst_strength} → +{pts:g}")
        c += pts
    if inp.external_confirmation:
        c += 5
        ex.append("confirmation par une lettre → +5")
    c = _clamp(c, 30)
    # T — technique
    t = 0.0
    if inp.rs_rank is not None:
        pts = 8 if inp.rs_rank >= 90 else 6 if inp.rs_rank >= 80 else 3 if inp.rs_rank >= 70 else 0
        t += pts
        ex.append(f"force relative rang {inp.rs_rank:.0f} → +{pts}")
    if inp.above_mm50_mm200 and inp.mm50_above_mm200:
        t += 5
        ex.append("structure haussière (MM50 > MM200, cours au-dessus) → +5")
    if inp.above_rising_mm20:
        t += 2
    if inp.rvol is not None:
        pts = 5 if inp.rvol >= 2 else 3 if inp.rvol >= 1.5 else 0
        t += pts
        if pts:
            ex.append(f"volume relatif {inp.rvol:.1f} → +{pts}")
    if inp.clean_trigger:
        t += 5
        ex.append("déclencheur net → +5")
    if inp.over_extended:
        t -= 4
        ex.append("extension excessive → −4")
    if inp.resistance_within_3pct:
        t -= 3
        ex.append("résistance majeure à moins de 3 % → −3")
    t = _clamp(t, 25)
    # R — risk / volatility / agenda
    r = 0.0
    if inp.stop_distance_adr is not None:
        r += 5 if inp.stop_distance_adr <= 1.5 else 3 if inp.stop_distance_adr <= 2.5 else 0
    if inp.earnings_within_10_days is False:
        r += 4
    elif inp.earnings_within_10_days is None:
        ex.append("date de résultats inconnue → +0")
    else:
        ex.append("résultats sous 10 jours → +0")
    if inp.adv_eur is not None:
        r += 3 if inp.adv_eur >= 5e6 else 2 if inp.adv_eur >= 1e6 else 0
    if inp.market_cap_eur is not None and inp.market_cap_eur >= 1e9:
        r += 3
    if inp.gap_pct is not None and abs(inp.gap_pct) >= 0.15:
        r -= 4
    if inp.non_eur:
        r -= 2
    r = _clamp(r, 15)
    # M — sector / macro
    m = {"green": 5.0, "orange": 2.0}.get(inp.regime or "", 0.0)
    if inp.sector_rs_pct is not None and inp.sector_rs_pct >= 60:
        m += 3
    if not inp.major_macro_event_today:
        m += 2
    if inp.same_sector_positions >= 2:
        m -= 3
    m = _clamp(m, 10)
    blocks: dict[str, float | None] = {"C": c, "T": t, "F": None, "R": r, "M": m}
    attainable = sum(BLOCK_MAX[k] for k, v in blocks.items() if v is not None)
    total = sum(v for v in blocks.values() if v is not None) / attainable * 100
    denom = sum(BLOCK_MAX[k] for k in admission_blocks if blocks.get(k) is not None)
    num = sum(blocks[k] or 0 for k in admission_blocks if blocks.get(k) is not None)
    if "C" not in admission_blocks:
        num += min(c, c_bonus_cap)
    ratio = min(1.0, num / denom) if denom else 0.0
    ex.append("fondamentaux non attribués (aucune source gratuite qualifiée)")
    confidence = 4 if data_complete else 3
    confidence -= 1  # F unavailable (docs/06 §6.5: confidence −1 under 3/5 sub-criteria)
    if inp.external_confirmation or (inp.catalyst_strength and inp.catalyst_positive):
        confidence += 1
    confidence = max(1, min(5, confidence))
    risk_pts = 0
    risk_pts += 1 if (inp.stop_distance_adr or 0) > 2.0 else 0
    risk_pts += 1 if inp.small_cap else 0
    risk_pts += 1 if inp.adv_eur is not None and inp.adv_eur < 1e6 else 0
    risk_pts += 1 if inp.non_eur else 0
    risk_pts += 1 if inp.gap_pct is not None and abs(inp.gap_pct) >= 0.15 else 0
    risk = "faible" if risk_pts == 0 else "moyen" if risk_pts == 1 else "eleve"
    return Score(blocks, total, attainable, ratio, confidence, risk, ex)
