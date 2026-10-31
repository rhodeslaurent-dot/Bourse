"""Graduated verdict (docs/37 lot L0, ADR-016 proposed): the ``verdict`` parameters, the measured edge a speculative
card shows, the level of a stored proposal and the read-only preview ``verdict-preview``.

Built OFF by default: without the ``verdict`` group, or with ``verdict.enabled: false``, the engine behaves exactly as
before (tests/integration/test_engine_verdict.py compares both). The preview never writes anything: it recomputes,
from the stored gates and details of each proposal of a day, the level it would get with the scale on.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import DecisionSnapshot, Proposal, Signal
from app.domain.admission import Setup as SetupKind
from app.domain.entry_policies import PolicyParams
from app.domain.timeutil import PARIS
from app.domain.verdict import (
    LEVEL_ORDER,
    Edge,
    Level,
    Verdict,
    VerdictFacts,
    VerdictParams,
    classify_reason,
    grade,
    has_appreciation,
    last_target_admits,
    r4_first_target_only,
    size_label,
    speculative_plage,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "params.example.yaml"
D7 = "d7_trend_quality"  # D15 applies to this detector's cards only


def group(config: LoadedConfig) -> dict[str, Any] | None:
    g = getattr(config.params, "verdict", None)
    return g if isinstance(g, dict) else None


def params_of(g: dict[str, Any]) -> VerdictParams:
    return VerdictParams(
        enabled=bool(g["enabled"]),
        speculative_detectors=frozenset(str(x) for x in g["speculative_detectors"] or []),
        speculative_size_multiplier=float(g["speculative_size_multiplier"]),
        speculative_last_target=bool(g["speculative_last_target"]),
        speculative_d7_entry=str(g["speculative_d7_entry"]),
        speculative_stop_atr=float(g["speculative_stop_atr"]),
        plage_cap_atr=float(g["speculative_plage_cap_atr"]),
        plage_validity_sessions=int(g["speculative_plage_validity_sessions"]),
        time_exit_sessions=int(g["speculative_time_exit_sessions"]),
        notify_speculative=bool(g["notify_speculative"]),
        shown_max=int(g["speculative_shown_max"]) if g.get("speculative_shown_max") is not None else None,
    )


def params(config: LoadedConfig, day: date | None = None) -> VerdictParams | None:
    """The scale when ``verdict.enabled`` and in effect on ``day``; None otherwise — the engine then runs as before."""
    g = group(config)
    if g is None or g.get("enabled") is not True:
        return None
    eff = g.get("effective_from")
    try:
        if day is not None and eff is not None and date.fromisoformat(str(eff)) > day:
            return None
        return params_of(g)
    except (KeyError, TypeError, ValueError):
        return None  # the schema validated the group at load: never a half-read scale


def preview_params(config: LoadedConfig) -> tuple[VerdictParams | None, str]:
    """The values the preview simulates: the user's group (even disabled), else the proposed values of the example."""
    g = group(config)
    if g is not None:
        try:
            state = "activée" if g.get("enabled") is True else "désactivée (verdict.enabled: false)"
            return params_of(g), f"config/params.yaml, échelle {state}"
        except (KeyError, TypeError, ValueError):
            pass
    try:
        ex = (yaml.safe_load(EXAMPLE.read_text(encoding="utf-8")) or {}).get("verdict")
        if isinstance(ex, dict):
            return params_of(
                ex
            ), "valeurs proposées de config/params.example.yaml (groupe verdict absent de params.yaml)"
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        pass
    return None, "groupe verdict introuvable"


def policy_params(config: LoadedConfig, vp: VerdictParams) -> PolicyParams:
    """The plan n° 4 policy parameters of D15, with the production values the policies read."""
    p = config.params
    return PolicyParams(
        float(p.risk.slippage_pct),
        float((getattr(p, "admission", None) or {}).get("min_reward_risk", 2.0)),
        int((getattr(p, "entry", None) or {}).get("expiry_sessions", 3)),
        confirm_sessions=vp.plage_validity_sessions,
        confirm_max_atr=vp.plage_cap_atr,
    )


# --- measured edge ----------------------------------------------------------------------------------------------


def edge_from_track(track: Any) -> Edge | None:
    """The live comparable history (docs/18 §18.5) when it qualifies (not « insuffisant »)."""
    if track is None:
        return None
    d: dict[str, Any] = dict(track) if isinstance(track, dict) else track.as_dict()
    verdict, shrunk = d.get("verdict"), d.get("shrunk_r")
    if verdict in (None, "insuffisant") or not isinstance(shrunk, int | float):
        return None
    return Edge(float(shrunk), int(d.get("n") or 0) or None, "historique comparable, espérance rétrécie",
                positive=verdict == "favorable")  # fmt: skip


def measured_edge(s: Session, detector: str, track: Any, cache: dict[str, Any] | None = None) -> Edge | None:
    """What the card's source measured, as the engine loads it: the live comparable history first, else the latest
    backtest at full cost (production entry and exits). None when nothing is measured — « non mesurée »."""
    live = edge_from_track(track)
    if live is not None:
        return live
    from app.services.backtest import production_segment

    key = f"verdict_edge:{detector}"
    if cache is not None and key in cache:
        return cache[key]  # type: ignore[no-any-return]
    seg = production_segment(s, detector)
    out = None
    if seg is not None:
        lo, hi = seg.get("ci_low"), seg.get("ci_high")
        out = Edge(float(seg["mean_r"]), int(seg["n"]), "backtest, entrée et sorties de production",
                   float(lo) if lo is not None else None, float(hi) if hi is not None else None,
                   positive=lo is not None and float(lo) > 0 and float(seg["mean_r"]) > 0)  # fmt: skip
    if cache is not None:
        cache[key] = out
    return out


def edge_dict(edge: Edge | None) -> dict[str, Any] | None:
    if edge is None:
        return None
    return {"r": edge.r, "n": edge.n, "source": edge.source, "ic_bas": edge.ci_low, "ic_haut": edge.ci_high,
            "positif": edge.positive}  # fmt: skip


# --- level of a stored proposal ---------------------------------------------------------------------------------


def _d12_ok(p: Proposal, sig: Signal | None, price: float | None, k: float) -> bool:
    """D12 from the stored values: R4 on the last exploitable target reaches the price (or the seuil)."""
    d = p.details or {}
    tg = [float(t) for t in (p.targets or []) if isinstance(t, int | float)]
    if len(tg) < 2 or p.stop is None or price is None:
        return False
    conf = d.get("configuration") or {}
    try:
        kind = SetupKind(str(conf.get("type")))
    except ValueError:
        kind = SetupKind.PULLBACK
    entry = conf.get("declencheur") or (sig.entry_low if sig is not None else None) or price
    comptes = d.get("comptes") or {}
    acc = comptes.get(p.account_recommended or "") or next(
        (v for v in comptes.values() if isinstance(v, dict) and v.get("frais_aller_retour_pct") is not None), {}
    )
    pmt = conf.get("p_max_technique")
    return last_target_admits(float(price), float(entry), kind, tg[-1], float(p.stop),
                              float(acc.get("frais_aller_retour_pct") or 0.0), k,
                              float(pmt) if isinstance(pmt, int | float) else None)  # fmt: skip


def grade_proposal(
    p: Proposal,
    sig: Signal | None,
    vp: VerdictParams,
    *,
    price: float | None = None,
    price_status: str = "eod",
    edge_positive: bool = False,
    simulate: bool = False,
    config: LoadedConfig | None = None,
    geometry: tuple[float, float, float | None] | None = None,
) -> tuple[Verdict, list[str]]:
    """The level of a stored proposal. ``simulate`` (the preview of a proposal made with the scale off) also applies
    what the engine would change: ADR-013 lifted for a speculative detector (D10), no R4 under the d7 plage (D15,
    from ``geometry`` = close, high and ATR14 of the signal day), the last target when R4 fails on the first one only
    (D12) — sizes and levels are then the engine's to recompute."""
    d = p.details or {}
    gates = p.gates or {}
    blocked = tuple(str(b) for b in gates.get("blocked") or [])
    caps = tuple(str(b) for b in d.get("blocages_plafonds") or [])
    appr = tuple(str(b) for b in gates.get("appreciation") or [])
    detector = sig.detector if sig is not None else ""
    spec = "speculative" in d
    admitted = d.get("admission") == "admis" and bool(p.shares)
    lifted: list[str] = []
    notes: list[str] = []
    k = float((getattr(config.params, "admission", None) or {}).get("min_reward_risk", 2.0)) if config else 2.0
    if simulate and not spec:
        r4 = sorted({j.code for b in blocked if (j := classify_reason(b, caps)).rule.r4_first_target})
        if detector in vp.speculative_detectors:
            spec = True
            lifted.append("surveillance_seule")
            notes.append(f"ADR-013 levé (D10) : carte spéculative, taille {size_label(vp.speculative_size_multiplier)}")
            if detector == D7 and vp.speculative_d7_entry == "plage":
                plage = None
                trig = sig.entry_low if sig is not None else None
                if config is not None and isinstance(trig, int | float) and geometry is not None and p.stop is not None:
                    close, high, atr14 = geometry
                    plage = speculative_plage(close, high, atr14, float(trig), float(p.stop),
                                              tuple(float(t) for t in p.targets or []), policy_params(config, vp),
                                              vp.speculative_stop_atr, vp.time_exit_sessions)  # fmt: skip
                if isinstance(plage, str):
                    notes.append(f"entrée à plage P2 (D15) impossible : {plage}")
                    admitted = False
                else:
                    lifted += r4
                    admitted = admitted or (d.get("admission") in ("admis", "refuse") and bool(p.targets))
                    lv = (
                        f" (estimée : seuil {plage.seuil:.4g}, limite {plage.limite:.4g}, stop {plage.stop:.4g})"
                        if plage is not None
                        else ""
                    )
                    notes.append(f"entrée à plage P2 (D15), R4 non appliqué{lv} : niveaux et taille recalculés par le "
                                 "moteur")  # fmt: skip
            elif r4 and vp.speculative_last_target and _d12_ok(p, sig, price, k):
                lifted += r4
                admitted = True
                notes.append("objectif final retenu (D12) : limite recalculée par le moteur")
        elif vp.speculative_last_target and r4_first_target_only(blocked, caps) and _d12_ok(p, sig, price, k):
            spec = True
            lifted += r4
            admitted = True
            notes.append(
                f"objectif final retenu (D12) : carte spéculative, taille {size_label(vp.speculative_size_multiplier)}"
            )
        elif has_appreciation(blocked, caps):
            notes.append(f"éléments d'appréciation seulement : re-évaluée à la taille "
                         f"{size_label(vp.speculative_size_multiplier)} par le moteur")  # fmt: skip
    facts = VerdictFacts(
        action=p.action,
        blocked=blocked,
        caps=caps,
        appreciation=appr,
        to_verify=bool(p.to_verify),
        price_status=price_status,
        admitted=admitted,
        speculative=spec,
        edge_positive=edge_positive,
        gate=(d.get("contexte_marche") or {}).get("portail") or d.get("portail"),
        lifted=tuple(lifted),
    )
    return grade(facts, vp), notes


# --- read-only preview -------------------------------------------------------------------------------------------


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=PARIS).astimezone(UTC)
    return start, start + timedelta(days=1)


def _signal_geometry(s: Session, p: Proposal) -> tuple[float, float, float | None] | None:
    """Close, high and ATR14 of the proposal's signal day from the stored bars, as the engine computes them (D15)."""
    from app.domain.indicators import compute_daily_features
    from app.services.market_data import load_bars

    raw = (p.details or {}).get("horodatage_cours")
    try:
        end = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None
    bars = load_bars(s, p.isin, end=end, limit=260)
    if len(bars) < 20:
        return None
    return bars[-1].close, bars[-1].high, compute_daily_features(bars).atr14


def preview(s: Session, config: LoadedConfig, day: date) -> dict[str, Any]:
    """The level each stored proposal of ``day`` (Paris) would get with the scale on. Nothing is written."""
    from app.services.instruments import name_of

    vp, source = preview_params(config)
    start, end = _day_bounds(day)
    props = list(
        s.scalars(
            select(Proposal)
            .where(Proposal.created_at >= start, Proposal.created_at < end, Proposal.deleted_at.is_(None))
            .order_by(Proposal.rank.is_(None), Proposal.rank, Proposal.id)
        )
    )
    out: dict[str, Any] = {"jour": day.isoformat(), "parametres": source, "actif": bool(vp and vp.enabled),
                           "comptes": {lv.value: 0 for lv in Level}, "lignes": []}  # fmt: skip
    if vp is None or not props:
        return out
    sigs = {x.id: x for x in s.scalars(select(Signal).where(Signal.id.in_([p.signal_id for p in props])))}
    snaps = {
        x.proposal_id: x
        for x in s.scalars(select(DecisionSnapshot).where(DecisionSnapshot.proposal_id.in_([p.id for p in props])))
    }
    cache: dict[str, Any] = {}
    counts: Counter[str] = Counter()
    for p in props:
        sig = sigs.get(p.signal_id)
        snap = snaps.get(p.id)
        q = ((snap.quotes or {}).get(p.isin) or {}) if snap is not None else {}
        feats = (snap.features or {}) if snap is not None else {}
        d = p.details or {}
        stored = d.get("verdict_niveau")
        price = q.get("last") if isinstance(q.get("last"), int | float) else None
        if stored is not None:
            edge_pos = bool((d.get("avantage_mesure") or {}).get("positif"))
        else:
            edge = measured_edge(s, sig.detector, feats.get("track_record"), cache) if sig is not None else None
            edge_pos = bool(edge and edge.positive)
        geometry = (
            _signal_geometry(s, p)
            if stored is None and sig is not None and sig.detector == D7 and vp.speculative_d7_entry == "plage"
            else None
        )
        v, notes = grade_proposal(
            p, sig, vp, price=price, price_status=str(q.get("data_status") or "eod"), edge_positive=edge_pos,
            simulate=stored is None, config=config, geometry=geometry,
        )  # fmt: skip
        counts[v.level.value] += 1
        out["lignes"].append(
            {
                "id": p.id,
                "nom": name_of(p.isin),
                "isin": p.isin,
                "detecteur": sig.detector if sig is not None else None,
                "action": p.action,
                "niveau": v.level.value,
                "niveau_hors_verification": v.base.value,
                "taille": v.size_multiplier,
                "raison": v.main,
                "notes": notes,
                "niveau_enregistre": stored,
            }
        )
    out["comptes"] = {lv.value: counts.get(lv.value, 0) for lv in Level}
    out["lignes"].sort(key=lambda r: (LEVEL_ORDER[r["niveau"]], r["id"]))
    return out


def preview_text(rep: dict[str, Any]) -> str:
    from app.web.labels import label

    c = rep["comptes"]
    lines = [
        f"Aperçu du verdict gradué (docs/37, lot L0) — propositions du {rep['jour']} — lecture seule, rien n'est "
        "modifié",
        f"Paramètres : {rep['parametres']}",
        f"{len(rep['lignes'])} proposition(s) : "
        + " · ".join(f"{label('verdict_niveau', k)} {c.get(k, 0)}" for k in LEVEL_ORDER),
    ]
    if not rep["lignes"]:
        lines.append("Aucune proposition ce jour (ou groupe verdict introuvable).")
        return "\n".join(lines)
    lines.append("")
    for r in rep["lignes"]:
        size = f" (taille {size_label(r['taille'])})" if r["niveau_hors_verification"] == Level.SPECULATIVE else ""
        lines.append(
            f"#{r['id']} {r['nom']} · {r['detecteur'] or '?'} · {r['action']} → {label('verdict_niveau', r['niveau'])}"
            f"{size} · {r['raison']}"
        )
        for n in r["notes"]:
            lines.append(f"    {n}")
    lines += [
        "",
        "Niveaux calculés sur les portes et les détails enregistrés. Avec verdict.enabled: true, le moteur recalcule "
        "les tailles (× verdict.speculative_size_multiplier), la plage P2 de d7 (D15) et l'objectif final (D12) : "
        "une carte peut alors changer de niveau (taille nulle, plafond).",
    ]
    return "\n".join(lines)
