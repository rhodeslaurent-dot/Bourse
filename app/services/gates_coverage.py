"""``gates-coverage`` (audit of 29/10/2026): how often each gate of the decision funnel can be passed, measured on
the stored data. Output only: nothing is written, no parameter is changed.

Per venue: share of the universe with a next earnings date, a sector strength, ≥ ``scoring.f_min_available_subcriteria``
fundamental sub-criteria; per market: share of green / orange / red days; the admission ratio of the proposals by
regime; the proposals blocked by each gate reason (its prefix, numbers removed) over the period.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import Instrument, MarketRegimeRow, Proposal
from app.domain.scoring import fundamentals_block
from app.domain.timeutil import PARIS

_NUM = re.compile(r"\d")


def reason_prefix(reason: str, words: int = 7) -> str:
    """« BLOQUÉ : USD Federal Funds Rate dans les 30 h — … » → « BLOQUÉ : USD Federal Funds Rate dans les »."""
    head = reason.split(" — ")[0].split(" (")[0]
    kept = [w for w in head.split() if not _NUM.search(w)]
    return " ".join(kept[:words])


def _quantiles(xs: list[float]) -> dict[str, float | int]:
    if not xs:
        return {"n": 0}
    xs = sorted(xs)
    q = statistics.quantiles(xs, n=4) if len(xs) >= 2 else [xs[0]] * 3
    return {"n": len(xs), "min": round(xs[0], 3), "q1": round(q[0], 3), "mediane": round(q[1], 3),
            "q3": round(q[2], 3), "max": round(xs[-1], 3)}  # fmt: skip


def coverage(s: Session, config: LoadedConfig, start: date, end: date) -> dict[str, Any]:
    from app.services.fundamentals import facts
    from app.services.rotation import sector_strength

    sp = config.params.scoring or {}
    f_min = int(sp.get("f_min_available_subcriteria", 2))
    sectors = sector_strength(s, end)
    venues: dict[str, Counter[str]] = defaultdict(Counter)
    for inst in s.scalars(select(Instrument).where(Instrument.active.is_(True), Instrument.in_universe.is_(True))):
        c = venues[inst.mic or "?"]
        c["valeurs"] += 1
        c["date_resultats"] += int(inst.earnings_next_date is not None and inst.earnings_next_date >= start)
        c["force_secteur"] += int((sectors.get(inst.sector or "") or {}).get("centile") is not None)
        ff, _ = facts(s, inst)
        c["fondamentaux"] += int(fundamentals_block(ff, f_min)[2] >= f_min)
    per_venue = {
        mic: {
            "valeurs": c["valeurs"],
            "date_resultats": round(c["date_resultats"] / c["valeurs"], 3),
            "force_secteur": round(c["force_secteur"] / c["valeurs"], 3),
            "fondamentaux": round(c["fondamentaux"] / c["valeurs"], 3),
        }
        for mic, c in sorted(venues.items(), key=lambda kv: -kv[1]["valeurs"])
    }
    regimes: dict[str, Counter[str]] = defaultdict(Counter)
    days_seen: set[tuple[str, date]] = set()
    for r in s.scalars(
        select(MarketRegimeRow)
        .where(MarketRegimeRow.day >= start, MarketRegimeRow.day <= end)
        .order_by(MarketRegimeRow.day, MarketRegimeRow.ts.desc())
    ):
        if (r.market, r.day) in days_seen:
            continue  # the latest computation of the day
        days_seen.add((r.market, r.day))
        regimes[r.market][r.regime] += 1
    per_market = {
        m: {"jours": sum(c.values()), **{k: round(c[k] / sum(c.values()), 3) for k in ("green", "orange", "red")}}
        for m, c in sorted(regimes.items())
    }
    t0 = datetime.combine(start, datetime.min.time(), tzinfo=PARIS).astimezone(UTC)
    t1 = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=PARIS).astimezone(UTC)
    ratios: dict[str, list[float]] = defaultdict(list)
    blocked: Counter[str] = Counter()
    actions: Counter[str] = Counter()
    n_props = 0
    for p in s.scalars(
        select(Proposal).where(Proposal.created_at >= t0, Proposal.created_at < t1, Proposal.deleted_at.is_(None))
    ):
        n_props += 1
        actions[p.action] += 1
        d = p.details or {}
        ratio = (d.get("score") or {}).get("ratio_admission")
        if isinstance(ratio, int | float):
            ratios[str((d.get("contexte_marche") or {}).get("regime") or "inconnu")].append(float(ratio))
        for pref in {reason_prefix(b) for b in (p.gates or {}).get("blocked", []) if isinstance(b, str)}:
            blocked[pref] += 1
    thresholds = {"buy_min_ratio": sp.get("buy_min_ratio"), "buy_min_ratio_orange": sp.get("buy_min_ratio_orange")}
    return {
        "periode": [start.isoformat(), end.isoformat()],
        "par_place": per_venue,
        "regimes": per_market,
        "propositions": n_props,
        "actions": dict(actions),
        "ratio_admission_par_regime": {k: _quantiles(v) for k, v in sorted(ratios.items())},
        "seuils": thresholds,
        "blocages": dict(blocked.most_common()),
    }


def text(rep: dict[str, Any]) -> str:
    out = [f"Couverture des portes de décision du {rep['periode'][0]} au {rep['periode'][1]}"]
    out.append("Par place (part des valeurs de l'univers) : date de résultats · force du secteur · fondamentaux")
    for mic, v in rep["par_place"].items():
        out.append(f"  {mic:<6} {v['valeurs']:>5} valeurs · {v['date_resultats']:.0%} · {v['force_secteur']:.0%} · "
                   f"{v['fondamentaux']:.0%}")  # fmt: skip
    out.append("Régimes (part des jours vert / orange / rouge)")
    for m, v in rep["regimes"].items():
        out.append(f"  {m:<6} {v['jours']:>4} jours · {v['green']:.0%} / {v['orange']:.0%} / {v['red']:.0%}")
    out.append(f"Propositions : {rep['propositions']} {rep['actions']} · seuils {rep['seuils']}")
    for reg, q in rep["ratio_admission_par_regime"].items():
        out.append(f"  ratio d'admission, régime {reg} : {q}")
    out.append("Propositions bloquées par motif (préfixe)")
    for k, n in rep["blocages"].items():
        out.append(f"  {n:>5}  {k}")
    return "\n".join(out)
