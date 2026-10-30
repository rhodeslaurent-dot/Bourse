# ruff: noqa: E501
"""Confirmatory cells declared for the sealed old replication period (registration n° 7, docs/34 §34.14). Pure.

Why: the lead of registration n° 7 (an opening gap down, bought at the next open) comes from an exploratory study
(docs/36) that read the whole discovery window 2017-2026. A plan tested on that window would be « semi-propre ». The
plan therefore declares ``replication.mode: confirmatory``: its cells are evaluated **once**, by ``research unseal``
and without any candidate file, on the sealed period 2007-01-02 → 2015-09-30 that no research command has read, with
the whole machinery of a registration run on that window (stage 0, Holm over the confirmatory cells, permutation,
A1–A13 with the period's own folds ``replication.wf_test``, portfolio replay). ``research run`` on the discovery
window stays possible but descriptive: it never freezes a candidate file.

Verdict of each confirmatory cell (``thresholds.sealed``), mechanical, in this order:

- **NON CONCLUANT** when A1 (n, weeks, trades per fold) is not met, or when the placebo of the same exit and
  horizon (read first) shows an excess over its matched controls with |t| ≥ ``placebo_max_abs_t`` (the measure
  itself is then suspect);
- **CONFIRMÉ** when A1–A13 are all met **and** the weekly Newey–West t of the excess ≥ ``min_t_week`` **and** the
  deflated Sharpe ratio ≥ ``min_dsr`` with N = ``dsr_prior_trials`` (cells already read by the exploratory study)
  + every non-placebo cell of the plan (all of them are read on the sealed period);
- otherwise **NON CONCLUANT** when the planted-edge power at the decision edge is under ``power_min``, else **NON
  CONFIRMÉ**.

The exploratory cells are descriptive: with no training year before the first fold, none can pass A2 (the family's
walk-forward has no choice that year), so none can be a candidate — they never change anything.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from app.domain.research import stats as S
from app.domain.research.prereg import UNSEAL_KINDS, Cell, Prereg, PreregError

CONFIRMED = "CONFIRMÉ"
NOT_CONFIRMED = "NON CONFIRMÉ"
INCONCLUSIVE = "NON CONCLUANT"
PLACEBO_TRIGGER = "t00"
DESCRIPTIVE_PREFIX = (
    "DESCRIPTIF (découverte déjà lue par une étude exploratoire : aucun candidat, le verdict de ce plan est celui "
    "de la période scellée)"
)
DECISION = (
    "confirmé → suivi en ombre de la cellule confirmée, puis ADR de mise en production à 50 % du risque par trade "
    "avec règle de suspension ; non confirmé ou non concluant → rien ne change, la période scellée n'est pas réutilisée"
)


def sealed_plan(p: Prereg) -> Prereg:
    """The same plan with the replication period as its test window and the replication folds as its walk-forward
    years (its sha256, cells and thresholds unchanged)."""
    if not p.sealed_confirmation or p.replication is None:
        raise PreregError("ce plan ne déclare pas de confirmation sur la période scellée (replication.mode)")
    seg = dataclasses.replace(p.segments, discovery=p.replication, wf_test=p.replication_folds)
    return dataclasses.replace(p, segments=seg)


def check_sealed_once(p: Prereg, rows: Sequence[Mapping[str, str]], candidates_path: str | None) -> None:
    """The single opening of the sealed period by the plan's own declared cells: no candidate file (the cells are in
    the plan), and no earlier ``unseal`` row of this plan in the ledger — the same sha256 **or the same plan id** (a
    plan edited after its opening keeps its id: it may not open the period a second time)."""
    if candidates_path:
        raise PreregError(
            "descellement refusé : ce plan déclare ses cellules pour la période scellée (replication.mode "
            "confirmatory) — aucun fichier de candidats"
        )
    for r in rows:
        if r.get("kind") in UNSEAL_KINDS and (r.get("prereg_sha256") == p.sha256 or r.get("prereg_id") == p.id):
            raise PreregError("descellement refusé : la période scellée de ce plan a déjà été ouverte une fois")


def _num(x: Any) -> float | None:
    if x is None:
        return None
    v = float(x)
    return v if math.isfinite(v) else None


def placebo_of(c: Cell, cells: Sequence[Cell]) -> Cell | None:
    """The placebo cell read before ``c``: the same exit and horizon on random dates (trigger t00)."""
    for x in cells:
        if x.trigger == PLACEBO_TRIGGER and x.exit == c.exit and x.h == c.h:
            return x
    return None


def placebo_summary(s: Mapping[str, Any], max_abs_t: float) -> dict[str, Any]:
    """The placebo's excess over its matched controls; ``suspect`` when |t| ≥ ``max_abs_t`` or not measurable."""
    t = _num(s.get("t_week"))
    return {"n": s.get("n"), "mean_r": s.get("mean_r"), "mean_excess": s.get("mean_excess"),
            "excess_ctrl_ic": s.get("excess_ctrl_ic"), "t_week": s.get("t_week"),
            "suspect": t is None or abs(t) >= max_abs_t}  # fmt: skip


def dsr_trials(p: Prereg) -> int:
    """N of the deflated Sharpe ratio: the cells already read by the exploratory study (declared) + every non-placebo
    cell of this plan."""
    own = sum(1 for c in p.cells if c.tier != "f0" and c.trigger != PLACEBO_TRIGGER)
    return int(p.thresholds["sealed"]["dsr_prior_trials"]) + own


def sealed_verdicts(res: Mapping[str, Any], p: Prereg) -> dict[str, Any]:
    """Verdicts of the confirmatory cells from the statistics of the registration run on the sealed period (the
    ``cellules`` with their A1–A13 ``criteres`` and the ``familles`` power); sets each one's ``dsr`` (N declared)."""
    th = p.thresholds
    se = th["sealed"]
    stats: Mapping[str, dict[str, Any]] = res.get("cellules") or {}
    pool = [c for c in p.cells if c.tier != "f0" and c.trigger != PLACEBO_TRIGGER]
    n_trials = dsr_trials(p)
    srs = [v for c in pool if (v := _num((stats.get(c.id) or {}).get("_sr"))) is not None]
    var_sr = float(np.var(srs, ddof=1)) if len(srs) > 1 else 0.0
    placebos: dict[str, Any] = {}
    out: dict[str, Any] = {}
    for c in p.confirmatory:
        s = stats.get(c.id) or {}
        dsr = S.deflated_sharpe(float(_num(s.get("_sr")) or math.nan), n_trials, var_sr, int(s.get("_nobs") or 0),
                                float(_num(s.get("_skew")) or 0.0), float(_num(s.get("_kurt")) or 3.0))  # fmt: skip
        s["dsr"] = round(dsr, 4) if math.isfinite(dsr) else None
        s["dsr_essais"] = n_trials
        pl = placebo_of(c, p.cells)
        pls = placebo_summary(stats.get(pl.id) or {}, float(se["placebo_max_abs_t"])) if pl is not None else None
        if pl is not None and pls is not None:
            placebos[pl.id] = pls
        crit: dict[str, bool] = dict(s.get("criteres") or {})
        crit["t_semaine"] = (_num(s.get("t_week")) or -math.inf) >= float(se["min_t_week"])
        crit["dsr"] = (_num(s.get("dsr")) or -math.inf) >= float(se["min_dsr"])
        crit["placebo"] = pls is not None and not pls["suspect"]
        power = _num(((res.get("familles") or {}).get(c.id) or {}).get("puissance_decision"))
        if not crit.get("A1", False):
            verdict, why = INCONCLUSIVE, "échantillon sous A1 (n, semaines ou trades par pli)"
        elif not crit["placebo"]:
            verdict, why = INCONCLUSIVE, "placebo suspect ou absent : la mesure elle-même est en cause"
        elif all(crit.values()):
            verdict, why = CONFIRMED, "suivi en ombre, puis ADR à 50 % du risque par trade"
        elif power is not None and power < float(th["power_min"]):
            verdict, why = INCONCLUSIVE, f"puissance {power} < {th['power_min']} à l'effet de décision"
        else:
            verdict, why = NOT_CONFIRMED, "critères non remplis : " + ", ".join(k for k, v in crit.items() if not v)
        out[c.id] = {
            "verdict": verdict, "raison": why, "n": s.get("n"), "semaines": s.get("semaines"),
            "mean_r": s.get("mean_r"), "mean_eur_300": s.get("mean_eur_300"), "profit_factor": s.get("profit_factor"),
            "mean_pct_notionnel": s.get("mean_pct_notionnel"), "mean_excess": s.get("mean_excess"),
            "excess_ctrl_ic": s.get("excess_ctrl_ic"), "t_week": s.get("t_week"), "p_holm": s.get("p_holm"),
            "plis_positifs": s.get("plis_positifs"), "dsr": s.get("dsr"), "dsr_essais": n_trials,
            "puissance_decision": power, "placebo": pl.id if pl is not None else None,
            "criteres": crit, "manquants": [k for k, v in crit.items() if not v],
            "pea": (s.get("pea") or {}).get("mean_r"),
        }  # fmt: skip
    a, b = p.replication or (None, None)
    confirmed = [cid for cid, v in out.items() if v["verdict"] == CONFIRMED]
    title = f"PÉRIODE SCELLÉE {a} → {b} — " + " ; ".join(f"{cid} : {v['verdict']}" for cid, v in out.items())
    return {
        "titre": title + (" → suivi en ombre, puis ADR" if confirmed else " → rien ne change"),
        "periode": [str(a), str(b)],
        "plis": [f"{x} → {y}" for x, y in p.replication_folds],
        "ordre_de_lecture": [*placebos, *out, "exploratoires (descriptives, jamais une conséquence)"],
        "placebo": placebos,
        "confirmatoires": out,
        "confirmes": confirmed,
        "dsr_essais": n_trials,
        "seuils": dict(se),
        "decision": DECISION,
    }


LABELS = ("verdict", "raison", "manquants", "placebo", "suspect")  # never statistics: kept on a masked row


def report_view(v: Mapping[str, Any], min_n: int) -> dict[str, Any]:
    """The verdict as written to the report: every row under ``min_n`` observations masked like the rest of the report
    (``report.suppress_deep``), its verdict, reason, missing criteria and placebo flag kept."""
    from app.domain.research.report import suppress_deep

    out: dict[str, Any] = suppress_deep(v, min_n)
    for part in ("confirmatoires", "placebo"):
        for cid, row in (v.get(part) or {}).items():
            out[part][cid].update({k: row[k] for k in LABELS if k in row})
    return out


def render_sealed(v: Mapping[str, Any]) -> str:
    """French lines of the sealed verdict (placebo first, then the confirmatory cells)."""
    lines = ["", f"V — Verdict de la période scellée {' → '.join(v.get('periode') or [])} (plis : {' ; '.join(v.get('plis') or [])})",
             f"  ordre de lecture : {', '.join(v.get('ordre_de_lecture') or [])}"]  # fmt: skip
    for cid, x in (v.get("placebo") or {}).items():
        lines.append(f"  placebo {cid} : n {x.get('n')} excès {x.get('mean_excess')} IC {x.get('excess_ctrl_ic')} "
                     f"t {x.get('t_week')} → {'SUSPECT' if x.get('suspect') else 'sans biais détecté'}")  # fmt: skip
    for cid, x in (v.get("confirmatoires") or {}).items():
        lines.append(f"  {cid} : {x.get('verdict')} — {x.get('raison')}")
        lines.append(f"    n {x.get('n')} R moyen {x.get('mean_r')} € pour 300 € {x.get('mean_eur_300')} excès "
                     f"{x.get('mean_excess')} IC {x.get('excess_ctrl_ic')} t {x.get('t_week')} p Holm {x.get('p_holm')} "
                     f"plis positifs {x.get('plis_positifs')} DSR {x.get('dsr')} (N {x.get('dsr_essais')}) PEA {x.get('pea')}")  # fmt: skip
        if x.get("manquants"):
            lines.append(f"    critères non remplis : {', '.join(x['manquants'])}")
    lines.append(f"  décision : {v.get('decision')}")
    return "\n".join(lines) + "\n"


__all__ = ["CONFIRMED", "DESCRIPTIVE_PREFIX", "INCONCLUSIVE", "NOT_CONFIRMED", "check_sealed_once", "dsr_trials", "report_view",
           "placebo_of", "render_sealed", "sealed_plan", "sealed_verdicts"]  # fmt: skip
