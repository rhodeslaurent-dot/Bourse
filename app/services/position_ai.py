"""Step 2, option B (user's decision of 17/11/2026, docs/42) — the evening review of each held position by an AI
analyst and an AI contradictor, in **observation**.

Python first reviews the position (``portfolio_review``: verdict, stop, signals, the manager-sale caution). The
analyst receives the numbered facts (company masked) and says what it would do — keep, raise the stop, lighten or
exit; the contradictor attacks that view and gives the one it keeps. Python then checks the contradictor's final
view: a stop never lower than the current one nor at or above the last close, within ``stop_band`` of it; a part in
the closed list; motives tied to facts and with no number absent from them. A view that fails is set aside
(``ecartee``, with why), never corrected. The quantity of a lightening is Python's (rule 7 spirit: the AI never sizes).
Nothing is applied: the review is stored next to Python's, and measured against « do nothing » from the close.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import AiPositionReview, Instrument, LlmCall
from app.domain.timeutil import PARIS

ACTIONS = ("conserver", "remonter_stop", "alleger", "sortir")


@dataclass
class PositionAiSettings:
    enabled: bool = False
    daily_budget_usd: float = 0.3
    analyst_model: str | None = None  # default: llm.write_model
    contradictor_model: str | None = None
    max_tokens: int = 900
    est_input_tokens: int = 4000
    est_output_tokens: int = 700
    stop_band: float = 0.25  # a proposed stop more than 25 % under the last close is not a stop


def settings(config: LoadedConfig) -> PositionAiSettings:
    raw = (config.raw or {}).get("position_ai") or {}
    st = PositionAiSettings()
    for k in st.__dataclass_fields__:
        if k in raw and raw[k] is not None:
            setattr(st, k, type(getattr(st, k))(raw[k]) if getattr(st, k) is not None else raw[k])
    return st


def _fmt(x: Any) -> str:
    return "—" if x is None else (f"{x:g}" if isinstance(x, float) else str(x))


def dossier(line: dict[str, Any], inst: Instrument | None, regime: str | None) -> list[dict[str, str]]:
    """Numbered facts from Python's review of the position — the only material of the agents."""
    from app.services.committee import pseudonymise

    texts = [
        f"Position : {_fmt(line.get('quantite'))} titres au prix de revient {_fmt(line.get('pru'))}, stop actuel "
        f"{_fmt(line.get('stop'))}, protection {line.get('protection') or '—'}",
        f"Dernier cours {_fmt(line.get('cours'))} ; gain {_fmt(round((line.get('gain_pct') or 0) * 100, 2))} % ; "
        f"{_fmt(line.get('r'))} R ; détenue depuis {_fmt(line.get('seances'))} séance(s)",
        f"Revue Python : verdict {line.get('verdict') or '—'} ; stop proposé {_fmt(line.get('stop_propose'))} "
        f"({line.get('stop_raison') or 'sans changement'}) ; part à alléger {_fmt(line.get('alleger_pct'))}",
    ]
    if line.get("objectifs"):
        texts.append("Objectifs : " + ", ".join(_fmt(x) for x in line["objectifs"]))
    texts += [f"Signal : {x}" for x in (line.get("signaux") or [])]
    texts += [f"Note : {x}" for x in (line.get("notes") or [])]
    if inst is not None and inst.earnings_next_date:
        texts.append(f"Prochains résultats : {inst.earnings_next_date:%d/%m/%Y}")
    if regime:
        texts.append(f"Régime du marché européen : {regime}")
    facts = [{"id": f"F{k}", "texte": t} for k, t in enumerate(texts, 1)]
    return pseudonymise(facts, inst, str(line.get("isin") or ""))


def check_final(action: str, stop: float | None, part: float | None, line: dict[str, Any],
                st: PositionAiSettings) -> str | None:  # fmt: skip
    """Why the final view is set aside (None when it holds) — Python's guard on the AI's numbers."""
    last, current = line.get("cours"), line.get("stop")
    if action not in ACTIONS:
        return f"action hors liste : {action}"
    if action == "remonter_stop":
        if stop is None or not math.isfinite(stop) or stop <= 0:
            return "stop absent ou invalide"
        if last is None:
            return "dernier cours inconnu : stop invérifiable"
        if stop >= last:
            return f"stop {stop:g} au-dessus du dernier cours {last:g}"
        if current is not None and stop < current:
            return f"stop {stop:g} sous le stop actuel {current:g} (un stop n'est jamais abaissé)"
        if stop < last * (1 - st.stop_band):
            return f"stop {stop:g} à plus de {st.stop_band:.0%} sous le dernier cours"
    if action == "alleger" and part not in (0.25, 0.5):
        return f"part à alléger hors liste : {part}"
    return None


def shares_for(action: str, part: float | None, qty: int) -> int | None:
    """Python's quantity for the view (the AI never sizes)."""
    if action == "sortir":
        return qty
    if action == "alleger" and part:
        return max(1, math.floor(qty * part)) if qty > 0 else 0
    return None


def spent_today(s: Session, now: datetime) -> float:
    day0 = now.astimezone(PARIS).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    q = select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0)).where(
        LlmCall.ts >= day0, LlmCall.purpose.like("position_ai%")
    )
    return float(s.scalar(q) or 0.0)


def review_one(s: Session, config: LoadedConfig, gateway: Any, line: dict[str, Any], day: date,
               regime: str | None, now: datetime) -> AiPositionReview:  # fmt: skip
    from app.llm.gateway import LlmUnavailable, load_prompt
    from app.llm.schemas import PositionAnalystOut, PositionContradictorOut
    from app.services.committee import _check, _clean

    st = settings(config)
    inst = s.get(Instrument, line["isin"])
    facts = dossier(line, inst, regime)
    pdir = gateway.settings.prompts_dir if gateway is not None else None
    versions = (
        f"analyst_v{load_prompt(pdir, 'position_analyst')[0]}|contra_v{load_prompt(pdir, 'position_contradictor')[0]}"
        if pdir
        else "regles"
    )
    h = hashlib.sha256((json.dumps(facts, ensure_ascii=False, sort_keys=True) + versions).encode()).hexdigest()
    row = s.scalar(
        select(AiPositionReview).where(AiPositionReview.position_id == line["position_id"], AiPositionReview.day == day)
    )
    if row is not None and row.status in ("ok", "ecartee") and row.dossier_hash == h:
        return row  # idempotent: same position, same day, same dossier
    row = row or AiPositionReview(position_id=line["position_id"], day=day)
    row.isin, row.dossier_hash, row.created_at = line["isin"], h, now
    row.ref_close, row.status, row.cost_usd = line.get("cours"), "indisponible", 0.0
    row.dossier = {"faits": facts, "versions": versions}
    row.python = {k: line.get(k) for k in ("verdict", "stop", "stop_propose", "stop_raison", "alleger_pct")}
    s.add(row)
    if gateway is None or not getattr(gateway, "enabled", False):
        row.analyst = {"motif": "IA non configurée : revue non faite"}
        s.flush()
        return row
    m1 = st.analyst_model or gateway.settings.write_model
    m2 = st.contradictor_model or gateway.settings.write_model
    est = 0.0
    for m in (m1, m2):
        price = gateway.settings.prices.get(m)
        if not price:
            row.status, row.analyst = "budget", {"motif": f"tarif inconnu pour {m} : revue non faite"}
            s.flush()
            return row
        est += st.est_input_tokens * price["input"] / 1e6 + st.est_output_tokens * price["output"] / 1e6
    if spent_today(s, now) + est > st.daily_budget_usd:
        row.status, row.analyst = "budget", {"motif": f"plafond de la revue ({st.daily_budget_usd} $ / jour)"}
        s.flush()
        return row
    rejected: list[dict[str, str]] = []
    user = "<faits>\n" + json.dumps(facts, ensure_ascii=False, indent=1) + "\n</faits>"
    cost = 0.0
    try:
        a, u1 = gateway.parse(s, "position_ai_analyst", "position_analyst", user, PositionAnalystOut, model=m1,
                              max_tokens=st.max_tokens)  # fmt: skip
        cost += u1.cost_usd
        row.analyst = {"action": a.action, "stop": a.stop, "part": a.part, "confiance": a.confiance,
                       "motifs": _check(a.motifs, facts, "motif", rejected)}  # fmt: skip
        user2 = user + "\n<avis>\n" + json.dumps(row.analyst, ensure_ascii=False, indent=1) + "\n</avis>"
        c, u2 = gateway.parse(s, "position_ai_contradictor", "position_contradictor", user2, PositionContradictorOut,
                              model=m2, max_tokens=st.max_tokens)  # fmt: skip
        cost += u2.cost_usd
    except LlmUnavailable as exc:
        row.status, row.cost_usd = "indisponible", cost
        row.analyst = {**(row.analyst or {}), "motif": f"IA indisponible : {exc}"}
        s.flush()
        return row
    row.contradictor = {
        "action_finale": c.action_finale, "stop_final": c.stop_final, "part_finale": c.part_finale,
        "confiance": c.confiance, "objections": _check(c.objections, facts, "objection", rejected),
        "raison": _clean(c.raison, facts, "raison", rejected),
    }  # fmt: skip
    why = check_final(c.action_finale, c.stop_final, c.part_finale, line, st)
    if why is not None or not (row.analyst.get("motifs") or row.contradictor.get("objections")):
        rejected.append({"element": "avis final", "motif": why or "aucun motif ni objection valide"})
        row.status = "ecartee"
        row.action = row.stop_ai = row.part_ai = row.shares_ai = None
    else:
        row.status, row.action = "ok", c.action_finale
        row.stop_ai = c.stop_final if c.action_finale == "remonter_stop" else None
        row.part_ai = c.part_finale if c.action_finale == "alleger" else None
        row.shares_ai = shares_for(c.action_finale, row.part_ai, int(line.get("quantite") or 0))
    row.rejected, row.cost_usd, row.models = rejected, cost, [m1, m2]
    s.flush()
    return row


def run_day(s: Session, config: LoadedConfig, gateway: Any, day: date,
            now: datetime | None = None) -> list[AiPositionReview]:  # fmt: skip
    """Every held position reviewed by Python (``portfolio_review``) and with a last close, then by the agents."""
    from app.services.macro import latest_regime
    from app.services.portfolio_review import review_lines

    now = now or datetime.now(UTC)
    reg = latest_regime(s, day, "EU", 10)
    out = []
    for line in review_lines(s, config, day, now):
        if line.get("position_id") is None or line.get("cours") is None:
            continue  # no stored close: Python's review itself says « à vérifier »
        out.append(review_one(s, config, gateway, line, day, reg.regime if reg else None, now))
    return out


def latest_for(s: Session, position_id: int) -> AiPositionReview | None:
    return s.scalar(
        select(AiPositionReview)
        .where(AiPositionReview.position_id == position_id)
        .order_by(AiPositionReview.day.desc(), AiPositionReview.id.desc())
    )


def summary(r: AiPositionReview | None) -> str | None:
    """One line for the review page (observation)."""
    if r is None:
        return None
    if r.status != "ok":
        motif = ((r.analyst or {}).get("motif") or (r.rejected or [{}])[-1].get("motif") or r.status) if r else ""
        return f"Avis IA ({r.day:%d/%m}, observation) : non rendu — {motif}"
    what = {"conserver": "conserver", "remonter_stop": f"remonter le stop à {r.stop_ai:g}" if r.stop_ai else "",
            "alleger": f"alléger {r.shares_ai} titre(s)", "sortir": f"sortir ({r.shares_ai} titres)"}.get(
        r.action or "", r.action or "")  # fmt: skip
    first = ((r.contradictor or {}).get("objections") or (r.analyst or {}).get("motifs") or [{}])[0].get("texte", "")
    return f"Avis IA ({r.day:%d/%m}, observation, jamais appliqué) : {what}" + (f" — {first}" if first else "")
