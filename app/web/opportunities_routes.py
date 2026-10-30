"""``/opportunites`` (docs/17 §17.3): proposals of a day with a one-line explanation, an expandable
detail (fiche, data used, gates, decisions, note) and decision buttons → ``record_decision``."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.db.models import Decision, DecisionSnapshot, Instrument, Proposal, Signal
from app.db.session import db_session
from app.domain.learning import VERDICT_ORDER
from app.services.decisions import record_decision
from app.services.explain import explain_proposal
from app.services.instruments import name_of
from app.services.letters_verdict import failed_checks, sentence
from app.services.letters_verdict import label as verdict_label
from app.services.letters_verdict import tone as verdict_tone
from app.services.notes import notes_for
from app.services.pitch import pitch
from app.web.labels import VERDICT_GROUPS, VERDICT_TONES
from app.web.routes import templates

router = APIRouter()


@router.get("/opportunites", response_class=HTMLResponse)
def opportunities_page(
    request: Request, day: date | None = None, action: str | None = None, sort: str = "rang"
) -> HTMLResponse:
    cfg = request.app.state.config
    capital = float(cfg.params.capital.capital_pilote_eur) or None
    day = day or datetime.now(UTC).date()
    with db_session() as s:
        props = [
            p
            for p in s.scalars(
                select(Proposal).where(Proposal.deleted_at.is_(None)).order_by(Proposal.id.desc()).limit(300)
            )
            if (p.created_at.replace(tzinfo=UTC) if p.created_at.tzinfo is None else p.created_at).date() == day
            and (not action or p.action == action)
        ]
        sigs = (
            {x.id: x for x in s.scalars(select(Signal).where(Signal.id.in_([p.signal_id for p in props])))}
            if props
            else {}
        )
        snaps = (
            {
                x.proposal_id: x
                for x in s.scalars(
                    select(DecisionSnapshot).where(DecisionSnapshot.proposal_id.in_([p.id for p in props]))
                )
            }
            if props
            else {}
        )
        decisions: dict[int, list[Decision]] = {}
        for d in (
            s.scalars(select(Decision).where(Decision.proposal_id.in_([p.id for p in props])).order_by(Decision.id))
            if props
            else []
        ):
            decisions.setdefault(d.proposal_id, []).append(d)
        letters: dict[int, dict[str, int]] = {}
        if props:
            from app.db.models import LetterValidation

            for v in s.scalars(
                select(LetterValidation).where(
                    LetterValidation.target_type == "proposal",
                    LetterValidation.target_ref.in_([str(p.id) for p in props]),
                )
            ):
                d = letters.setdefault(int(v.target_ref), {"confirme": 0, "contredit": 0})
                if v.verdict in d:
                    d[v.verdict] += 1
        notes = {
            k: {"text": v.text, "updated_at": v.updated_at}
            for k, v in notes_for(s, "proposal", [str(p.id) for p in props]).items()
        }
        insts = (
            {i.isin: i for i in s.scalars(select(Instrument).where(Instrument.isin.in_({p.isin for p in props})))}
            if props
            else {}
        )
        venues = getattr(request.app.state.calendars, "venues", {}) if hasattr(request.app.state, "calendars") else {}
        from app.db.models import CommitteeRun

        committee: dict[int, CommitteeRun] = {}
        if props:
            for r in s.scalars(
                select(CommitteeRun)
                .where(CommitteeRun.proposal_id.in_([p.id for p in props]))
                .order_by(CommitteeRun.id)
            ):
                committee[r.proposal_id] = r  # the latest deliberation per proposal
        from app.services.reevaluations import latest_by_proposal, summary

        reevals = latest_by_proposal(s, [p.id for p in props])
        items = []
        for p in props:
            sig = sigs.get(p.signal_id)
            inst = insts.get(p.isin)
            items.append(
                {
                    "p": p,
                    "sig": sig,
                    "comite": committee.get(p.id),
                    "name": name_of(p.isin),
                    "explain": explain_proposal(p, sig, snaps.get(p.id), capital, inst.currency if inst else None),
                    "decisions": decisions.get(p.id, []),
                    "note": notes.get(str(p.id)),
                    "letters": letters.get(p.id, {}),
                    "snapshot_price": ((snaps.get(p.id).quotes or {}).get(p.isin) if snaps.get(p.id) else None),
                    "pitch": pitch(
                        sig.detector if sig else None, sig.evidence if sig else None, sig.source if sig else None
                    ),
                    "place": (venues[inst.mic].name if inst and inst.mic in venues else inst.mic if inst else None),
                    "secteur": " · ".join(x for x in (inst.sector, inst.industry) if x) if inst else None,
                    "devise": inst.currency if inst else None,
                    "reeval": summary(reevals[p.id]) if p.id in reevals else None,  # intraday_reeval (live quote)
                    # docs/20 §20.10: a letter's proposal — what the letter says, what the tool sees, the verdict
                    "verdict": (
                        {
                            "label": verdict_label(p),
                            "tone": verdict_tone(p),
                            "phrase": sentence(p),
                            "echecs": failed_checks(p),
                            "recue": _received(p),
                        }  # fmt: skip
                        if (p.details or {}).get("verdict")
                        else None
                    ),
                }
            )
        # docs/22 §22.4: the engine's ranking first (or net ratio / net gain on request); then the
        # proposals without rank (letters not testable, refusals), favourable history first (docs/18)
        action_rank = {"BUY": 0, "WATCH": 1, "NO_TRADE": 3}

        def acc_metric(it: dict, key: str) -> float:
            p = it["p"]
            row = ((p.details or {}).get("comptes") or {}).get(p.account_recommended or "", {})
            v = row.get(key)
            return -float(v) if isinstance(v, int | float) else 1e9

        def sort_key(it: dict) -> tuple:
            p = it["p"]
            primary = (
                acc_metric(it, "ratio_net")
                if sort == "ratio"
                else acc_metric(it, "gain_net_pct")
                if sort == "gain"
                else (p.rank if p.rank is not None else 1e9)
            )
            return (
                p.rank is None,
                primary,
                action_rank.get(p.action, 2),
                VERDICT_ORDER.get((it["explain"].track or {}).get("verdict", "insuffisant"), 1),
            )

        # user request of 26/10/2026: what can be acted on first (the order book, detailed), then what is
        # only watched, then what is set aside — the engine's rank kept inside each group
        book = order_book(items)
        orderable = {r["p"].id for r in book if not r["blocked"]}
        from app.services.explain import plain_reason

        far = float((getattr(cfg.params, "entry", None) or {}).get("far_limit_display_pct", 0.05))
        for it in items:
            p = it["p"]
            blocked = it["explain"].blocked or []
            it["groupe"] = (
                0
                if p.id in orderable or (p.action == "BUY" and not blocked and not p.to_verify)
                else 3
                if p.action == "NO_TRADE"
                else 2
                if information_only(it, far)
                else 1
            )
            it["pourquoi"] = [x for x in dict.fromkeys(plain_reason(b) for b in blocked) if x]
        level_groups = _verdict_groups(cfg, items)  # docs/37 L0: only when the day's proposals carry a level
        items.sort(key=lambda it: (it["groupe"], sort_key(it)))
        counts = {a: sum(1 for it in items if it["p"].action == a) for a in ("BUY", "WATCH", "NO_TRADE")}
        places: dict[str, int] = {}
        for it in items:
            places[it["place"] or "place inconnue"] = places.get(it["place"] or "place inconnue", 0) + 1
    return templates.TemplateResponse(
        request,
        "opportunites.html",
        {
            "request": request,
            "items": items,
            "day": day,
            "action": action,
            "counts": counts,
            "sort": sort,
            "book": book,
            "places": sorted(places.items(), key=lambda kv: -kv[1]),
            "today": datetime.now(UTC).date(),
            **(
                {"level_groups": level_groups, "fold_group": len(level_groups) - 1, "verdict_tones": VERDICT_TONES}
                if level_groups
                else {}
            ),
        },
    )


def _verdict_groups(cfg: Any, items: list[dict]) -> dict[int, tuple[str, str]] | None:
    """docs/37 §37.2 (lot L0): when the day's proposals were graded (``details.verdict_niveau``, written by the engine
    with ``verdict.enabled``), the page groups them by level — conviction forte, à considérer, spéculative, à
    surveiller, écartées (folded) — instead of actionable / watched / information / set aside. A proposal of the
    same day made before the scale was on is graded on its stored gates. None: the page as before."""
    if not any((it["p"].details or {}).get("verdict_niveau") for it in items):
        return None
    from app.domain.verdict import LEVEL_ORDER
    from app.services import verdict as verdict_svc

    vp, _ = verdict_svc.preview_params(cfg)
    for it in items:
        p = it["p"]
        lvl = (p.details or {}).get("verdict_niveau")
        if lvl not in LEVEL_ORDER and vp is not None:
            price = (it.get("snapshot_price") or {}).get("last")
            v, _ = verdict_svc.grade_proposal(p, it["sig"], vp, price=price, config=cfg)
            lvl = v.level.value
        it["niveau"] = lvl if lvl in LEVEL_ORDER else "a_surveiller"
        it["groupe"] = LEVEL_ORDER[it["niveau"]]
    return {LEVEL_ORDER[k]: v for k, v in VERDICT_GROUPS.items()}


@router.post("/opportunites/decision")
def decide(
    proposal_id: int = Form(...), decision: str = Form(...), reason: str = Form(""), day: str = Form("")
) -> RedirectResponse:
    if decision not in ("seen", "watch", "order_entered", "ignored", "snoozed"):
        raise HTTPException(400, "décision inconnue")
    with db_session() as s:
        try:
            record_decision(s, proposal_id, decision, "web", reason.strip() or None)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
    return RedirectResponse(f"/opportunites{'?day=' + day if day else ''}", status_code=303)


def _received(p: Proposal) -> str | None:
    """Reception time of the letter behind a letter proposal, Réunion then Paris (docs/09 §9.5)."""
    from app.domain.timeutil import format_dual, from_store

    raw = ((p.details or {}).get("lettre") or {}).get("recue_a")
    try:
        return format_dual(from_store(datetime.fromisoformat(str(raw)))) if raw else None
    except ValueError:
        return None


INFORMATION_ONLY = (
    "espérance historique négative au coût complet",  # a detector in watch only (ADR-013)
    "WATCH : pas d'objectif exploitable",
    "WATCH : objectif trop proche",
    "WATCH : stop trop large",
    "aucune limite en attente (R4)",  # a breakout whose maximum price falls under its trigger (29/10/2026)
)


def information_only(it: dict, far_pct: float) -> bool:
    """User decision of 29/10/2026: a card that cannot become an order is folded by default — a detector in watch
    only, no exploitable target, a target too close for the risk, a stop too wide, a breakout whose maximum price
    falls under its trigger, or a limit waiting for a pullback more than ``entry.far_limit_display_pct`` under the
    price of the analysis. A letter's proposal follows its verdict (docs/20 §20.10): « rejet » and « non analysable »
    are folded, an alert at the trigger is not."""
    p = it["p"]
    blocked = [str(b) for b in (p.gates or {}).get("blocked") or []]
    verdict = (p.details or {}).get("verdict")
    if verdict in ("rejet", "non_analysable"):
        return True
    if verdict is None and any(k in b for b in blocked for k in INFORMATION_ONLY):
        return True
    if verdict is not None and verdict != "attendre_repli":
        return False
    pmax = (p.details or {}).get("p_max")
    price = (it.get("snapshot_price") or {}).get("last")
    # a breakout above the limit of its plage (docs/06 §6.3) follows the same display rule as a waiting limit; an
    # armed breakout (BUY, its plage in the order book) is never folded
    waiting = any("en attente d'un repli" in b or "au-dessus de la limite de la plage" in b for b in blocked)
    return bool(waiting and pmax and price and price / pmax - 1 > far_pct)


def order_book(items: list[dict]) -> list[dict]:
    """R7 (docs/06 §6.7): open admitted proposals sorted by distance from the last price to the
    maximum entry price; the closest ``entry.standing_orders_max`` are the limit orders to post, with an expiry
    date after ``entry.expiry_sessions`` sessions of the value's venue (user decision of 27/10/2026). A breakout's
    row is its order à plage de déclenchement (docs/06 §6.3): seuil, limite, expiry after ``entry.confirm_sessions``
    sessions, and the distance from the price to the seuil."""
    rows = []
    seen: set[str] = set()
    for it in sorted(items, key=lambda x: (x["p"].rank is None, x["p"].rank or 0, x["p"].id)):
        p = it["p"]
        d = p.details or {}
        if p.status != "open" or p.action not in ("BUY", "WATCH") or d.get("admission") not in ("admis", "refuse"):
            continue
        if getattr(p, "to_verify", False):  # rule 2: a stale or delayed quote in session: no order (review 31/10)
            continue
        waits = [b for b in (p.gates or {}).get("blocked", []) if b.startswith("WATCH")]
        if any("en attente d'un repli" not in b for b in waits):
            continue  # WATCH for another reason (score, stop too wide…): no order to post
        if p.isin in seen:
            continue  # one order per value (best-ranked detector)
        seen.add(p.isin)
        pmax = d.get("p_max")
        acc = (d.get("comptes") or {}).get(p.account_recommended or "", {})
        price = (it.get("snapshot_price") or {}).get("last")
        if not pmax:
            continue
        ent = d.get("entree") or {}
        seuil = ent.get("seuil") if ent.get("type") == "ordre à plage de déclenchement" else None
        ref = float(seuil) if isinstance(seuil, int | float) and seuil > 0 else float(pmax)
        rows.append(
            {
                "p": p,
                "name": it["name"],
                "p_max": pmax,
                "seuil": seuil,
                "titres": acc.get("titres"),
                "stop": p.stop,
                "distance": (price / ref - 1) if price else None,  # to the seuil for a plage, else to the limit
                # the expiry date to type at the broker
                "expire": ent.get("jusqu_au") or (d.get("validite") or {}).get("jusqu_au"),
                "blocked": [b for b in (p.gates or {}).get("blocked", []) if b.startswith("BLOQUÉ")],
            }
        )
    rows.sort(key=lambda r: (r["blocked"] != [], abs(r["distance"]) if r["distance"] is not None else 9))
    return rows


@router.post("/opportunites/derogation")
def grant_override(proposal_id: int = Form(...), reason: str = Form(""), day: str = Form("")) -> RedirectResponse:
    """R21: a cap may be exceeded only with the user's explicit, motivated agreement, for this
    proposal only, when the engine marked it as eligible. Recorded as a decision; the snapshot of
    the proposal is never modified (rule 10)."""
    if len(reason.strip()) < 5:
        raise HTTPException(400, "motif obligatoire pour une dérogation")
    with db_session() as s:
        p = s.get(Proposal, proposal_id)
        if p is None:
            raise HTTPException(404, "proposition inconnue")
        if not ((p.details or {}).get("derogation") or {}).get("proposable"):
            raise HTTPException(400, "dérogation non proposable pour cette proposition (R21)")
        record_decision(s, proposal_id, "override", "web", reason.strip())
    return RedirectResponse(f"/opportunites{'?day=' + day if day else ''}", status_code=303)


@router.post("/opportunites/these")
def write_thesis_now(request: Request, proposal_id: int = Form(...), day: str = Form("")) -> RedirectResponse:
    """Write (or rewrite) the thesis of one proposal on demand (docs/22 §22.8)."""
    from app.services.thesis import write_thesis

    with db_session() as s:
        p = s.get(Proposal, proposal_id)
        if p is None:
            raise HTTPException(404, "proposition inconnue")
        write_thesis(s, getattr(request.app.state, "llm", None), p)
    return RedirectResponse(f"/opportunites{'?day=' + day if day else ''}", status_code=303)
