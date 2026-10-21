"""One-line, deterministic explanations of proposals (docs/17 §17.3). Pure formatting of stored
facts: nothing is computed here beyond percentages between stored numbers, and no LLM text."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.web.labels import label


@dataclass
class Explanation:
    headline: str  # one line, ≤ ~160 chars
    bullets: list[str] = field(default_factory=list)  # details (fiche)
    blocked: list[str] = field(default_factory=list)  # "BLOQUÉ : …" lines, verbatim from gates
    data_used: list[str] = field(default_factory=list)  # snapshot facts with status / timestamps
    summary: str = ""  # headline + first blocking gate + "à vérifier" (single-line contexts)


def _fmt(x: Any, digits: int = 2, unit: str = "") -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):,.{digits}f}".replace(",", " ") + unit
    except (TypeError, ValueError):
        return str(x)


def explain_proposal(
    proposal: Any, signal: Any | None, snapshot: Any | None, capital: float | None = None
) -> Explanation:
    """``proposal``/``signal``/``snapshot`` are ORM rows or any objects with the same attributes."""
    ev = (getattr(signal, "evidence", None) or {}) if signal else {}
    gates = getattr(proposal, "gates", None) or {}
    blocked = [str(b) for b in (gates.get("blocked") or [])]
    detector = getattr(signal, "detector", None) if signal else None
    source = getattr(signal, "source", None) if signal else None
    entry = getattr(proposal, "entry_zone", None) or {}
    entry_px = entry.get("achat") or entry.get("low") or entry.get("entry_low") or getattr(signal, "entry_low", None)
    stop = getattr(proposal, "stop", None)
    targets = getattr(proposal, "targets", None) or []
    shares = getattr(proposal, "shares", None)
    risk_eur = getattr(proposal, "risk_eur", None)

    parts: list[str] = []
    who = label("detector", detector) if detector else "signal"
    if detector == "external" and source:
        who = f"{source} (lettre / analyste)"
    parts.append(f"{label('action', getattr(proposal, 'action', ''))} · {who}")
    if ev.get("direction"):
        parts.append(
            {"buy": "avis achat", "sell": "avis vente", "watch": "à surveiller"}.get(ev["direction"], ev["direction"])
        )
    if entry_px:
        parts.append(f"entrée {_fmt(entry_px)} €")
    if stop and entry_px:
        try:
            parts.append(f"stop {_fmt(stop)} € ({(1 - float(stop) / float(entry_px)):.1%} sous l'entrée)")
        except ZeroDivisionError:
            parts.append(f"stop {_fmt(stop)} €")
    elif stop:
        parts.append(f"stop {_fmt(stop)} €")
    if targets:
        parts.append("objectif " + " / ".join(_fmt(t) for t in targets[:2]) + " €")
    if shares:
        parts.append(f"{int(shares)} titres")
    if risk_eur is not None:
        pct = f" ({float(risk_eur) / capital:.1%} du capital)" if capital else ""
        parts.append(f"risque {_fmt(risk_eur, 0)} €{pct}")
    if ev.get("drift_since_open") is not None:
        parts.append(f"écart depuis l'ouverture {float(ev['drift_since_open']):+.1%}")
    headline = " · ".join(parts)
    summary = headline
    if blocked:
        summary += " · " + blocked[0].split("(")[0].strip()
    if getattr(proposal, "to_verify", False):
        summary += " · à vérifier"

    bullets: list[str] = []
    if ev.get("p_open") is not None:
        bullets.append(
            f"Ouverture du jour {_fmt(ev['p_open'])} €"
            + (f", gap {float(ev['gap_open']):+.1%}" if ev.get("gap_open") is not None else "")
        )
    if ev.get("p_recv") is not None:
        bullets.append(f"Cours à réception {_fmt(ev['p_recv'])} €")
    for k, v in (ev.get("levels") or {}).items():
        bullets.append(f"Niveau « {k} » cité par la source : {_fmt(v)} €")
    if getattr(proposal, "account_recommended", None):
        bullets.append(f"Compte recommandé : {label('account', proposal.account_recommended)}")
    if getattr(proposal, "size_eur", None):
        bullets.append(f"Taille {_fmt(proposal.size_eur, 0)} €")
    cost = getattr(proposal, "cost_estimate", None) or {}
    if cost:
        bullets.append("Coût estimé : " + ", ".join(f"{k} {_fmt(v)}" for k, v in cost.items()))
    for g in gates.get("passed") or []:
        bullets.append(f"Porte passée : {g}")
    if getattr(proposal, "horizon", None):
        bullets.append(f"Horizon : {proposal.horizon}")

    data_used: list[str] = []
    if snapshot is not None:
        for isin, q in (getattr(snapshot, "quotes", None) or {}).items():
            if isinstance(q, dict):
                data_used.append(
                    f"Cours {isin} : {_fmt(q.get('last'))} € [{label('data_status', q.get('data_status'))}"
                    + (f", marché {q.get('market_timestamp')}" if q.get("market_timestamp") else "")
                    + f"] source {q.get('source', '?')}"
                )
        feats = getattr(snapshot, "features", None) or {}
        if feats:
            data_used.append(
                "Indicateurs du "
                + str(feats.get("day", "?"))
                + " : force relative "
                + _fmt(feats.get("rs_rank"), 0)
                + "/100, moyenne 50 j "
                + _fmt(feats.get("mm50"))
                + ", moyenne 200 j "
                + _fmt(feats.get("mm200"))
                + ", plus haut 60 j "
                + _fmt(feats.get("pivot_60"))
                + ", amplitude 14 j "
                + _fmt(feats.get("atr14"))
                + " €"
                + (" (trou de données)" if feats.get("gap_in_data") else "")
            )
        reg = getattr(snapshot, "regime", None) or {}
        if reg:
            data_used.append(f"Régime au {reg.get('day', '?')} : {label('regime', reg.get('regime'))}")
        for st in getattr(snapshot, "portfolio_state", None) or []:
            if isinstance(st, dict):
                data_used.append(
                    f"Compte {st.get('account_id')} : cash {_fmt(st.get('cash'), 0)} €, "
                    + ("déclaratif" if st.get("declarative") else "synchronisé")
                    + (f", synchronisé {st.get('synced_at')}" if st.get("synced_at") else ", jamais synchronisé")
                )
    return Explanation(headline, bullets, blocked, data_used, summary)
