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
    track: dict[str, Any] | None = None  # comparable history frozen in the snapshot (docs/18 §18.5)


def _fmt(x: Any, digits: int = 2, unit: str = "") -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):,.{digits}f}".replace(",", " ") + unit
    except (TypeError, ValueError):
        return str(x)


def explain_proposal(
    proposal: Any, signal: Any | None, snapshot: Any | None, capital: float | None = None, currency: str | None = None
) -> Explanation:
    """``proposal``/``signal``/``snapshot`` are ORM rows or any objects with the same attributes. Price levels are
    in the listing's ``currency`` (« € » when it is the euro or unknown); the risk is always in euros."""
    unit = "€" if currency in (None, "", "EUR") else currency
    ev = (getattr(signal, "evidence", None) or {}) if signal else {}
    gates = getattr(proposal, "gates", None) or {}
    blocked = [str(b) for b in (gates.get("blocked") or [])]
    detector = getattr(signal, "detector", None) if signal else None
    source = getattr(signal, "source", None) if signal else None
    entry = getattr(proposal, "entry_zone", None) or {}
    entry_px = (
        entry.get("achat")
        or entry.get("limite")
        or entry.get("low")
        or entry.get("entry_low")
        or getattr(signal, "entry_low", None)
    )
    stop = getattr(proposal, "stop", None)
    targets = getattr(proposal, "targets", None) or []
    shares = getattr(proposal, "shares", None)
    risk_eur = getattr(proposal, "risk_eur", None)

    parts: list[str] = []
    who = label("detector", detector) if detector else "signal"
    if detector == "external" and ev.get("lettre"):
        who = f"{ev['lettre']} (lettre, recalculée par l'outil)"
    elif detector == "external" and source:
        who = f"{source} (lettre / analyste)"
    parts.append(f"{label('action', getattr(proposal, 'action', ''))} · {who}")
    if ev.get("direction"):
        parts.append(
            {"buy": "avis achat", "sell": "avis vente", "watch": "à surveiller"}.get(ev["direction"], ev["direction"])
        )
    if entry_px:
        parts.append(f"{'limite' if entry.get('limite') else 'entrée'} {_fmt(entry_px)} {unit}")
    if stop and entry_px:
        try:
            parts.append(f"stop {_fmt(stop)} {unit} ({(1 - float(stop) / float(entry_px)):.1%} sous l'entrée)")
        except ZeroDivisionError:
            parts.append(f"stop {_fmt(stop)} {unit}")
    elif stop:
        parts.append(f"stop {_fmt(stop)} {unit}")
    if targets:
        parts.append("objectif " + " / ".join(_fmt(t) for t in targets[:2]) + f" {unit}")
    if shares:
        parts.append(f"{int(shares)} titres")
    if risk_eur is not None:
        pct = f" ({float(risk_eur) / capital:.1%} du capital)" if capital else ""
        parts.append(f"risque {_fmt(risk_eur, 0)} €{pct}")
    if ev.get("drift_since_open") is not None:
        parts.append(f"écart depuis l'ouverture {float(ev['drift_since_open']):+.1%}")
    feats_snap = (getattr(snapshot, "features", None) or {}) if snapshot is not None else {}
    track = feats_snap.get("track_record") if isinstance(feats_snap, dict) else None
    if track and track.get("verdict") in ("favorable", "defavorable"):
        parts.append(f"historique {label('track_verdict', track['verdict'])}")
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
        bullets.append(f"Compte conseillé : {label('account', proposal.account_recommended)}")
    if getattr(proposal, "size_eur", None):
        bullets.append(f"Taille {_fmt(proposal.size_eur, 0)} €")
    cost = getattr(proposal, "cost_estimate", None) or {}
    if cost and not any(isinstance(v, dict) for v in cost.values()):  # per-account costs: table of the fiche
        bullets.append("Coût estimé : " + ", ".join(f"{k} {_fmt(v)}" for k, v in cost.items()))
    for g in gates.get("passed") or []:
        bullets.append(f"Porte passée : {g}")
    if getattr(proposal, "horizon", None):
        bullets.append(f"Horizon : {proposal.horizon}")
    if track:
        if track.get("n"):
            bullets.append(
                f"Historique comparable ({track.get('scope')}) : {track['n']} cas, espérance "
                f"{float(track.get('mean_r') or 0):+.2f} R, rétrécie {float(track.get('shrunk_r') or 0):+.2f} R "
                f"→ {label('track_verdict', track.get('verdict'))}"
            )
        else:
            bullets.append("Historique comparable : aucun cas terminé à la date de la proposition")

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
        if feats.get("day"):
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
    return Explanation(headline, bullets, blocked, data_used, summary, track)


# --- plain-language reading of the engine's WATCH / BLOQUÉ lines (user request of 26/10/2026) ----------------

_PLAIN: list[tuple[str, str]] = [
    (
        "espérance historique négative",
        "Ce détecteur perd en moyenne une fois les coûts payés (backtest sur 8 ans) : ses idées restent visibles "
        "pour information, sans ordre, en attendant la refonte issue de la recherche (décision du 26/10/2026).",
    ),
    (
        "ratio d'admission",
        "Le score de la valeur n'atteint pas le minimum : il compte les points obtenus (tendance, "
        "risque, marché, catalyseurs…) sur les points possibles avec les données disponibles. Il faut "
        "75 % des points (80 % quand le marché est orange) ; en dessous, le dossier est jugé trop faible "
        "pour un achat, la valeur reste à surveiller.",
    ),
    (
        "limite posée en attente d'un repli",
        "Le cours est déjà au-dessus du prix maximal d'entrée : acheter plus haut "
        "rendrait le gain attendu trop faible face au risque. Un ordre limite est préparé au prix maximal (carnet "
        "d'ordres) ; il ne sera exécuté que si le cours redescend.",
    ),
    (
        "objectif trop proche",
        "Le premier objectif est trop près pour le risque pris : même acheté juste au-dessus du stop, le gain "
        "ne vaudrait pas deux fois la perte possible. Aucun ordre limite n'est préparé.",
    ),
    (
        "pas d'objectif exploitable",
        "Aucune résistance ni objectif publié au-dessus du cours : le gain attendu ne peut "
        "pas être chiffré, donc pas d'achat (valeur souvent à son plus haut).",
    ),
    ("confiance", "Trop peu d'éléments concordants (détecteur, lettres, presse, historique) pour un achat."),
    (
        "risque élevé",
        "Le risque de la ligne est jugé élevé (stop lointain, liquidité faible, petite capitalisation "
        "ou devise) : à surveiller, pas d'achat.",
    ),
    (
        "portail restreint —",
        "Contexte de marché restreint : seuls les dossiers très solides (ratio net ≥ 2,5 et score ≥ 75) passent.",
    ),
    (
        "portail restreint +",
        "Contexte de marché défavorable sur la place de la valeur (régime rouge ou risque "
        "conjoncturel élevé) : aucune entrée, sauf dérogation motivée.",
    ),
    ("coupe-circuit", "Les pertes récentes ont déclenché le coupe-circuit : pas de nouvelle entrée pour l'instant."),
    (
        "emballement",
        "Agitation anormale sans information publiée, ou portée par peu de comptes, sur une petite "
        "valeur : risque de manipulation, entrée bloquée.",
    ),
    ("aucune taille possible", "Avec le budget de risque et les frais, aucune quantité raisonnable n'est possible."),
    (
        "hausse médiatique",
        "La valeur a déjà monté depuis sa citation dans les médias : ces hausses se retournent souvent, à surveiller.",
    ),
    (
        "grappe corrélée",
        "Vous détenez déjà des valeurs qui évoluent de la même façon : le risque cumulé serait trop élevé.",
    ),
    (
        "publication de résultats",
        "Des résultats sont publiés pendant la durée prévue du trade : un saut de cours pourrait passer sous le stop.",
    ),
    ("dans les 30 h", "Une annonce macroéconomique majeure arrive : entrée gelée jusqu'à sa publication."),
    ("taux de change", "Le taux de change n'est pas disponible : la taille ne peut pas être calculée."),
]


def plain_reason(line: str) -> str | None:
    """The plain French reading of one WATCH / BLOQUÉ line of a proposal, or None."""
    low = line.lower()
    return next((text for key, text in _PLAIN if key.lower() in low), None)
