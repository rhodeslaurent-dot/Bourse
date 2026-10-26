# ruff: noqa: E501
"""Mechanical acceptance criteria A1–A13 and tiers (docs/31 §3.4). Pure: reads the statistics of a cell.

A criterion that could not be evaluated counts as **not met** (conservative) and is reported as such.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Any


class Tier(StrEnum):
    CANDIDATE = "CANDIDAT"
    WATCHLIST = "SURVEILLANCE"
    REJECT = "REJET"
    DIAGNOSTIC = "DIAGNOSTIC"


def _num(x: Any) -> float | None:
    if x is None:
        return None
    if isinstance(x, str):
        return math.inf if x == "infini" else None
    v = float(x)
    return v if not math.isnan(v) else None


def _ge(x: Any, bound: float) -> bool:
    v = _num(x)
    return v is not None and v >= bound


def _gt(x: Any, bound: float) -> bool:
    v = _num(x)
    return v is not None and v > bound


def _le(x: Any, bound: float) -> bool:
    v = _num(x)
    return v is not None and v <= bound


def _lt(x: Any, bound: float) -> bool:
    v = _num(x)
    return v is not None and v < bound


def a2_met(s: dict[str, Any], th: dict[str, Any]) -> bool:
    """A2 effect on a summary (mean R, € per 300 € of risk budget, profit factor, % of notional)."""
    a2 = th["a2"]
    return (_ge(s.get("mean_r"), a2["min_mean_r"]) and _ge(s.get("mean_eur_300"), a2["min_eur_per_budget"])
            and _ge(s.get("profit_factor"), a2["min_profit_factor"]) and _gt(s.get("mean_pct_notionnel"), 0.0))  # fmt: skip


def unseal_confirmed(sealed: dict[str, Any], discovery: dict[str, Any], th: dict[str, Any]) -> bool:
    """Verdict of the single holdout unseal for one candidate: the sealed-period effect meets A2 in full
    (``a2_met``: mean R, € per 300 € of risk budget, profit factor **and** a positive mean % of notional) and
    has the same sign as its discovery mean R."""
    s, d = _num(sealed.get("mean_r")), _num(discovery.get("mean_r"))
    same_sign = s is not None and d is not None and s * d > 0
    return same_sign and a2_met(sealed, th)


def criteria(c: dict[str, Any], th: dict[str, Any], universe_label: str) -> dict[str, bool]:
    """A1–A13 of one cell (exploratory or confirmatory)."""
    a1, a3, a4 = th["a1"], th["a3"], th["a4"]
    monthly = c.get("mode", "event") != "event"
    oos = c.get("oos") or {}
    out: dict[str, bool] = {}
    if monthly:
        out["A1"] = _ge(c.get("mois"), a1["rank_min_months"]) and _ge(c.get("trades_portefeuille"),
                                                                       a1["rank_min_trades_slot"])  # fmt: skip
    else:
        out["A1"] = (_ge(c.get("n"), a1["event_min_n"]) and _ge(c.get("semaines"), a1["event_min_weeks"])
                     and _ge(c.get("pli_min_n"), a1["fold_min_trades"]))  # fmt: skip

    def a2_ok(s: dict[str, Any]) -> bool:
        return a2_met(s, th)

    # an exploratory cell was selected among its family on the whole discovery window, test years included:
    # A2, A5 (and A11) are judged on the family's selection walk-forward, and only when the cell is its choice
    explo = c.get("tier") == "exploratory"
    wf = c.get("walk_forward_famille") or {}
    chosen = bool(wf.get("choix_cellule", False))
    out["A2"] = chosen and a2_ok(wf.get("oos") or {}) if explo else a2_ok(oos)
    if c.get("tier") == "confirmatory":
        out["A3"] = _le(c.get("p_holm"), a3["holm_alpha"]) and _gt(oos.get("mean_r"), 0.0)
    else:
        out["A3"] = (_le(c.get("p_romano_wolf"), a3["rw_alpha"]) and _ge(c.get("t_week"), a3["min_t"])
                     and _ge(c.get("dsr"), a3["min_dsr"]) and _le(c.get("pbo_famille"), a3["max_pbo"]))  # fmt: skip
    ci = c.get("excess_ctrl_ic") or [None, None]
    out["A4"] = (bool(c.get("gate")) and _ge(c.get("mean_excess_ctrl"), a4["min_excess"]) and _gt(ci[0], 0.0)
                 and _le(c.get("p_permutation"), a4["max_perm_p"]))  # fmt: skip
    folds = wf.get("plis_positifs") if explo else c.get("plis_positifs")
    out["A5"] = (chosen or not explo) and _ge(folds, th["a5"]["min_positive_folds"])
    out["A6"] = _gt(c.get("mean_r_stress"), 0.0)
    reg = c.get("regime") or {}
    both = _gt((reg.get("t13_vrai") or {}).get("mean_r"), 0.0) and _gt((reg.get("t13_faux") or {}).get("mean_r"), 0.0)
    out["A7"] = both or "t13" in str(c.get("gate_label", ""))
    out["A8"] = not bool((c.get("dividendes") or {}).get("fragile", True))
    surv = c.get("survie") or {}
    # A9 also needs the cell to stay positive when every exit on an interrupted listing loses 100 % of the notional
    out["A9"] = _ge(surv.get("multiple_risque_equilibre"), th["a9"]["min_hazard_multiple"]) and (
        universe_label != "in_universe" and _gt(c.get("mean_r"), 0.0)
        and _gt(surv.get("mean_r_radiees_perte_totale"), 0.0)
    )  # fmt: skip
    conc = c.get("concentration") or {}
    out["A10"] = _lt(conc.get("part_top10_valeurs"), th["a10"]["max_top10_share"]) and _gt(
        conc.get("mean_r_hors_top5_valeurs"), 0.0
    )
    cap_oos = wf.get("oos_capacite") if explo else (c.get("capacite") or {}).get("oos")
    out["A11"] = (chosen or not explo) and a2_ok(cap_oos or {})
    out["A12"] = int(c.get("duree_max", c.get("h", 0)) or 0) <= int(th["a12"]["max_hold_sessions"])
    out["A13"] = (not bool(c.get("ambigu", True))) and bool(c.get("portefeuille_ok", False))
    return out


def classify(c: dict[str, Any], th: dict[str, Any], universe_label: str) -> tuple[Tier, dict[str, bool]]:
    if c.get("tier") == "f0":
        return Tier.DIAGNOSTIC, {}
    a = criteria(c, th, universe_label)
    if all(a.values()) and c.get("declencheur") != "d2x":
        return Tier.CANDIDATE, a
    watch = all(a[k] for k in ("A1", "A2", "A4", "A5", "A6")) and _le(c.get("q_bh"), th["watchlist_bh_q"])
    return (Tier.WATCHLIST if watch else Tier.REJECT), a


def potential_candidate(c: dict[str, Any], th: dict[str, Any], universe_label: str) -> bool:
    """A1–A12 met: worth a stage-2 portfolio replay (A13)."""
    a = criteria(c, th, universe_label)
    return all(v for k, v in a.items() if k != "A13")
