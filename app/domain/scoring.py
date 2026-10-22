"""Score /100 and admission ratio (docs/06 §6.5), computed in pure Python with explanations.

Blocks: C catalysts 0–30, T technique 0–25, F fundamentals 0–20 (« non attribué » without data),
R risk/volatility/agenda 0–15, M sector/macro 0–10. The admission ratio of a detector only puts in
its denominator the blocks that apply to its strategy; C is a capped bonus for D2/D3/D4.
"""

from __future__ import annotations

from dataclasses import dataclass, field

BLOCK_MAX = {"C": 30, "T": 25, "F": 20, "R": 15, "M": 10}


@dataclass(frozen=True)
class FundamentalFacts:
    """Free fundamentals (docs/06 §6.5 block F); any field may be missing (never estimated)."""

    revenue_growth: float | None = None  # fraction (0.12 = +12 %)
    operating_margin: float | None = None
    debt_to_ebitda: float | None = None
    pe: float | None = None
    sector_median_pe: float | None = None
    eps_revisions_positive: bool | None = None  # no free source yet → None


def fundamentals_block(f: FundamentalFacts | None, min_available: int) -> tuple[float | None, int, int, list[str]]:
    """(points, attainable maximum, available sub-criteria, explanations); points ``None`` when
    fewer than ``min_available`` sub-criteria are available (F « non attribué »)."""
    if f is None:
        return None, 0, 0, ["fondamentaux non attribués (aucune donnée)"]
    pts, mx, ex, n = 0.0, 0, [], 0
    if f.revenue_growth is not None:
        n, mx = n + 1, mx + 5
        if f.revenue_growth > 0.10:
            pts += 5
            ex.append(f"croissance du chiffre d'affaires {f.revenue_growth:+.0%} → +5")
    if f.operating_margin is not None:
        n, mx = n + 1, mx + 4
        if f.operating_margin > 0.10:
            pts += 4
            ex.append(f"marge opérationnelle {f.operating_margin:.0%} → +4")
    if f.eps_revisions_positive is not None:
        n, mx = n + 1, mx + 5
        if f.eps_revisions_positive:
            pts += 5
    if f.debt_to_ebitda is not None:
        n, mx = n + 1, mx + 3
        if f.debt_to_ebitda < 2:
            pts += 3
            ex.append(f"dette / EBITDA {f.debt_to_ebitda:.1f} → +3")
    if f.pe is not None and f.sector_median_pe:
        n, mx = n + 1, mx + 3
        if 0 < f.pe < 2 * f.sector_median_pe:
            pts += 3
            ex.append(f"PER {f.pe:.0f} < 2 × médiane du secteur {f.sector_median_pe:.0f} → +3")
    if n < min_available:
        return None, 0, n, [f"fondamentaux non attribués ({n} critère(s) disponible(s) sur 5)"]
    ex.append(f"fondamentaux : {n}/5 critères disponibles (source gratuite)")
    return pts, mx, n, ex


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
    insider_buy_days: int | None = None  # docs/23 §23.3: days since the latest purchase by a manager (window)
    insider_buy_points: float = 5.0
    small_cap: bool = False
    fundamentals: FundamentalFacts | None = None


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


def score(
    inp: ScoreInput,
    admission_blocks: list[str],
    c_bonus_cap: float,
    data_complete: bool = True,
    f_min_available: int = 2,
) -> Score:
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
    if inp.insider_buy_days is not None and inp.insider_buy_points:
        c += inp.insider_buy_points
        ex.append(f"achat d'un dirigeant il y a {inp.insider_buy_days} j → +{inp.insider_buy_points:g}")
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
    f_pts, f_max, f_n, f_ex = fundamentals_block(inp.fundamentals, f_min_available)
    ex += f_ex
    blocks: dict[str, float | None] = {"C": c, "T": t, "F": f_pts, "R": r, "M": m}
    maxima = {**BLOCK_MAX, "F": f_max}
    attainable = sum(maxima[k] for k, v in blocks.items() if v is not None)
    total = sum(v for v in blocks.values() if v is not None) / attainable * 100
    denom = sum(maxima[k] for k in admission_blocks if blocks.get(k) is not None)
    num = sum(blocks[k] or 0 for k in admission_blocks if blocks.get(k) is not None)
    if "C" not in admission_blocks:
        num += min(c, c_bonus_cap)
    ratio = min(1.0, num / denom) if denom else 0.0
    confidence = 4 if data_complete else 3
    if f_pts is None or f_n < 3:
        confidence -= 1  # docs/06 §6.5: confidence −1 under 3/5 fundamental sub-criteria
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
