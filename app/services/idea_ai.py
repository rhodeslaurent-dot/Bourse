"""Step 2, option A (user's decision of 17/11/2026, docs/42, ADR-020) — three authors of levels on the day's ideas,
in **observation**.

For each of the best-ranked ideas, Python's levels (maximal entry price, stop, targets) stand beside those of an AI
analyst; an AI contradictor attacks both and gives its own; for the very best ideas an AI arbiter picks one author
(or none). Python checks every set of levels against the stored closes (entry near the last close, stop under the
entry and not too far, targets above it) and sets aside — never corrects — a set that fails. Python sizes every
valid set by rule 7 with the proposal's own sizing inputs (``details.comptes.<account>.entree_taille``): the AI never
sizes. Nothing is applied: Python stays the default author until the measure (``measure_authors``) says otherwise.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import mean
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import AiIdeaReview, LlmCall, PriceEod, Proposal
from app.domain.timeutil import PARIS

AUTHORS = ("python", "analyste", "contradicteur")


@dataclass
class IdeaAiSettings:
    enabled: bool = False
    top_n: int = 10
    arbiter_top: int = 3
    daily_budget_usd: float = 0.9
    analyst_model: str | None = None  # default: llm.write_model
    contradictor_model: str | None = None
    arbiter_model: str | None = None
    max_tokens: int = 1000
    est_input_tokens: int = 6000
    est_output_tokens: int = 800
    level_band: float = 0.15  # an entry more than 15 % from the last close is not an entry
    max_stop_pct: float = 0.25  # a stop more than 25 % under the entry is not a stop


def settings(config: LoadedConfig) -> IdeaAiSettings:
    raw = (config.raw or {}).get("idea_ai") or {}
    st = IdeaAiSettings()
    for k in st.__dataclass_fields__:
        if k in raw and raw[k] is not None:
            setattr(st, k, type(getattr(st, k))(raw[k]) if getattr(st, k) is not None else raw[k])
    return st


def check_levels(lv: dict[str, Any] | None, last: float | None, st: IdeaAiSettings) -> str | None:
    """Why a set of levels is set aside (None when it holds) — Python's plausibility check (rule 3)."""
    if not lv:
        return "niveaux absents"
    entry, stop, targets = lv.get("entree"), lv.get("stop"), [t for t in lv.get("objectifs") or [] if t is not None]
    if entry is None or stop is None:
        return "entrée ou stop absent"
    if not all(isinstance(x, int | float) and math.isfinite(x) and x > 0 for x in (entry, stop, *targets)):
        return "niveau non numérique ou négatif"
    if last is None:
        return "dernier cours inconnu : niveaux invérifiables"
    if abs(entry / last - 1) > st.level_band:
        return f"entrée {entry:g} à plus de {st.level_band:.0%} du dernier cours {last:g}"
    if stop >= entry:
        return f"stop {stop:g} au-dessus de l'entrée {entry:g}"
    if (entry - stop) / entry > st.max_stop_pct:
        return f"stop {stop:g} à plus de {st.max_stop_pct:.0%} sous l'entrée"
    if not targets:
        return "aucun objectif"
    if any(t <= entry for t in targets):
        return "objectif sous l'entrée"
    return None


def python_levels(p: Proposal) -> dict[str, Any]:
    d = p.details or {}
    return {"entree": d.get("p_max"), "stop": p.stop, "objectifs": list(p.targets or [])[:3]}


def sized(p: Proposal, lv: dict[str, Any]) -> int | None:
    """Rule 7 by Python on the author's levels, with the proposal's own inputs (None when not stored)."""
    from app.domain.risk import resize

    acc = p.account_recommended or next(iter((p.details or {}).get("comptes") or {}), None)
    stored = (((p.details or {}).get("comptes") or {}).get(acc) or {}).get("entree_taille") if acc else None
    if not stored:
        return None
    return resize(stored, float(lv["entree"]), float(lv["stop"])).shares


def last_close(s: Session, isin: str, day: date) -> float | None:
    v = s.scalar(select(PriceEod.close).where(PriceEod.isin == isin, PriceEod.day <= day).order_by(PriceEod.day.desc()))
    return float(v) if v is not None else None


def spent_today(s: Session, now: datetime) -> float:
    day0 = now.astimezone(PARIS).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    q = select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0)).where(
        LlmCall.ts >= day0, LlmCall.purpose.like("idea_ai%")
    )
    return float(s.scalar(q) or 0.0)


def _estimate(gateway: Any, st: IdeaAiSettings, models: list[str]) -> float | None:
    total = 0.0
    for m in models:
        price = gateway.settings.prices.get(m)
        if not price:
            return None
        total += st.est_input_tokens * price["input"] / 1e6 + st.est_output_tokens * price["output"] / 1e6
    return total


def _author(lv: dict[str, Any], last: float | None, st: IdeaAiSettings, p: Proposal) -> dict[str, Any]:
    why = check_levels(lv, last, st)
    return {"niveaux": lv, "controle": why or "ok", "titres": sized(p, lv) if why is None else None}


def deliberate(s: Session, config: LoadedConfig, gateway: Any, p: Proposal, rank: int, day: date,
               now: datetime) -> AiIdeaReview:  # fmt: skip
    from app.llm.gateway import LlmUnavailable, load_prompt
    from app.llm.schemas import IdeaAnalystOut, IdeaArbiterOut, IdeaContradictorOut
    from app.services.committee import _check, _clean
    from app.services.committee import dossier as committee_dossier
    from app.services.insider_caution import caution_for

    st = settings(config)
    facts = committee_dossier(s, p, now)
    py = python_levels(p)
    targets = ", ".join(str(t) for t in py["objectifs"]) or "—"
    text = f"Niveaux de Python : entrée maximale {py['entree']}, stop {py['stop']}, objectifs {targets}"
    facts.append({"id": f"F{len(facts) + 1}", "texte": text})
    caution = caution_for(s, config, p.isin, day)
    if caution:
        facts.append({"id": f"F{len(facts) + 1}", "texte": caution})
    pdir = gateway.settings.prompts_dir if gateway is not None else None
    names = ("idea_analyst", "idea_contradictor", "idea_arbiter")
    versions = "|".join(f"{n}_v{load_prompt(pdir, n)[0]}" for n in names) if pdir else "regles"
    h = hashlib.sha256((json.dumps(facts, ensure_ascii=False, sort_keys=True) + versions).encode()).hexdigest()
    row = s.scalar(select(AiIdeaReview).where(AiIdeaReview.proposal_id == p.id))
    if row is not None and row.status == "ok" and row.dossier_hash == h:
        return row
    row = row or AiIdeaReview(proposal_id=p.id)
    last = last_close(s, p.isin, day)
    row.day, row.isin, row.rank, row.dossier_hash, row.created_at = day, p.isin, rank, h, now
    row.status, row.cost_usd, row.dossier = "indisponible", 0.0, {"faits": facts, "versions": versions, "cours": last}
    row.python = {**_author(py, last, st, p), "avis": p.action, "titres": p.shares}
    s.add(row)
    if gateway is None or not getattr(gateway, "enabled", False):
        row.analyst = {"motif": "IA non configurée : avis non rendu"}
        s.flush()
        return row
    m1 = st.analyst_model or gateway.settings.write_model
    m2 = st.contradictor_model or gateway.settings.write_model
    m3 = st.arbiter_model or gateway.settings.write_model
    with_arbiter = rank <= st.arbiter_top
    est = _estimate(gateway, st, [m1, m2, *([m3] if with_arbiter else [])])
    if est is None or spent_today(s, now) + est > st.daily_budget_usd:
        row.status = "budget"
        row.analyst = {"motif": f"plafond de l'analyse multi-agent ({st.daily_budget_usd} $ / jour) ou tarif inconnu"}
        s.flush()
        return row
    rejected: list[dict[str, str]] = []
    user = "<faits>\n" + json.dumps(facts, ensure_ascii=False, indent=1) + "\n</faits>"
    cost, models = 0.0, [m1, m2]
    try:
        a, u1 = gateway.parse(s, "idea_ai_analyst", "idea_analyst", user, IdeaAnalystOut, model=m1,
                              max_tokens=st.max_tokens)  # fmt: skip
        cost += u1.cost_usd
        row.analyst = {"avis": a.avis, "confiance": a.confiance, "motifs": _check(a.motifs, facts, "motif", rejected),
                       **_author(a.niveaux.model_dump(), last, st, p)}  # fmt: skip
        user2 = user + "\n<avis>\n" + json.dumps(row.analyst, ensure_ascii=False, indent=1) + "\n</avis>"
        c, u2 = gateway.parse(s, "idea_ai_contradictor", "idea_contradictor", user2, IdeaContradictorOut, model=m2,
                              max_tokens=st.max_tokens)  # fmt: skip
        cost += u2.cost_usd
        row.contradictor = {
            "avis": c.avis_revu, "confiance": c.confiance, "raison": _clean(c.raison, facts, "raison", rejected),
            "objections": _check(c.objections, facts, "objection", rejected),
            **_author(c.niveaux_revus.model_dump(), last, st, p),
        }  # fmt: skip
        if with_arbiter:
            props = {"python": row.python, "analyste": row.analyst, "contradicteur": row.contradictor}
            user3 = user + "\n<propositions>\n" + json.dumps(props, ensure_ascii=False, indent=1) + "\n</propositions>"
            b, u3 = gateway.parse(s, "idea_ai_arbiter", "idea_arbiter", user3, IdeaArbiterOut, model=m3,
                                  max_tokens=st.max_tokens)  # fmt: skip
            cost += u3.cost_usd
            models.append(m3)
            chosen = b.choix
            if chosen in AUTHORS and props[chosen].get("controle") != "ok":
                rejected.append({"element": "arbitre", "motif": f"choix « {chosen} » écarté par le contrôle : aucun"})
                chosen = "aucun"
            why = _clean(b.raison, facts, "raison arbitre", rejected)
            row.arbiter = {"choix": chosen, "confiance": b.confiance, "raison": why}
            row.chosen = chosen
    except LlmUnavailable as exc:
        row.status, row.cost_usd = "indisponible", cost
        row.analyst = {**(row.analyst or {}), "motif": f"IA indisponible : {exc}"}
        s.flush()
        return row
    row.status, row.rejected, row.cost_usd, row.models = "ok", rejected, cost, models
    s.flush()
    return row


def run_day(s: Session, config: LoadedConfig, gateway: Any, day: date,
            now: datetime | None = None) -> list[AiIdeaReview]:  # fmt: skip
    """The day's best-ranked ideas (one per value, BUY or WATCH), ``top_n`` of them; the arbiter on the first ones."""
    from app.services.engine import todays_ranked

    now = now or datetime.now(UTC)
    st = settings(config)
    out: list[AiIdeaReview] = []
    seen: set[str] = set()
    for p in todays_ranked(s, day):
        if p.isin in seen or p.action not in ("BUY", "WATCH") or not (p.details or {}).get("p_max"):
            continue
        seen.add(p.isin)
        out.append(deliberate(s, config, gateway, p, len(out) + 1, day, now))
        if len(out) >= st.top_n:
            break
    return out


def latest_for(s: Session, proposal_id: int) -> AiIdeaReview | None:
    return s.scalar(select(AiIdeaReview).where(AiIdeaReview.proposal_id == proposal_id))


def summary(r: AiIdeaReview | None) -> str | None:
    """One line for the idea card (observation, never applied)."""
    if r is None:
        return None
    if r.status != "ok":
        return f"Avis IA (observation) : non rendu — {(r.analyst or {}).get('motif') or r.status}"

    def one(who: str, x: dict[str, Any] | None) -> str:
        if not x:
            return f"{who} —"
        lv = x.get("niveaux") or {}
        if x.get("controle") != "ok":
            return f"{who} : {x.get('avis')} (niveaux écartés : {x.get('controle')})"
        size = f", {x.get('titres')} titre(s) calculés par Python" if x.get("titres") is not None else ""
        return f"{who} : {x.get('avis')}, entrée {lv.get('entree'):g}, stop {lv.get('stop'):g}{size}"

    txt = "Avis IA (observation, jamais appliqué) — " + " ; ".join(
        (one("analyste", r.analyst), one("contradicteur", r.contradictor))
    )
    if r.arbiter:
        txt += f" ; arbitre : retient {r.arbiter.get('choix')}"
    return txt


@dataclass(frozen=True)
class AuthorMeasure:
    author: str
    n: int  # valid sets of levels whose window is over
    filled: int  # entries that would have filled
    mean_r: float | None  # gross R of the filled ones
    hit_rate: float | None


def measure_authors(s: Session, today: date, entry_sessions: int = 3, max_hold: int = 20,
                    slippage_pct: float = 0.002) -> list[AuthorMeasure]:  # fmt: skip
    """What each author's levels would have given (limit entry within ``entry_sessions``, first target, stop, or
    ``max_hold`` sessions), from the closes stored after the review — the measure that designates the default."""
    from app.domain.backtest import simulate_trade
    from app.domain.indicators import Bar

    per: dict[str, list[float]] = {a: [] for a in (*AUTHORS, "arbitre")}
    counts: dict[str, int] = dict.fromkeys(per, 0)
    for r in s.scalars(select(AiIdeaReview).where(AiIdeaReview.status == "ok", AiIdeaReview.day < today)):
        rows = s.execute(
            select(PriceEod.open, PriceEod.high, PriceEod.low, PriceEod.close, PriceEod.volume)
            .where(PriceEod.isin == r.isin, PriceEod.day > r.day, PriceEod.day <= r.day + timedelta(days=60))
            .order_by(PriceEod.day)
        ).all()
        bars = [Bar(o, h, lo, c, v) for o, h, lo, c, v in rows]
        if len(bars) < entry_sessions + max_hold:
            continue  # the window is not over: never counted
        sets = {"python": r.python, "analyste": r.analyst, "contradicteur": r.contradictor}
        if r.chosen in AUTHORS:
            sets["arbitre"] = sets[r.chosen]
        for who, x in sets.items():
            if not x or x.get("controle") != "ok":
                continue
            lv = x["niveaux"]
            counts[who] += 1
            t = simulate_trade(bars, float(lv["entree"]), float(lv["stop"]), float(lv["objectifs"][0]), slippage_pct,
                               entry_sessions, max_hold)  # fmt: skip
            if t is not None:
                per[who].append(t.gross_r)
    out = []
    for who, rs in per.items():
        out.append(AuthorMeasure(who, counts[who], len(rs), mean(rs) if rs else None,
                                 sum(1 for x in rs if x > 0) / len(rs) if rs else None))  # fmt: skip
    return out
