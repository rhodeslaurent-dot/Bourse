"""The verdict of a letter proposal in one sentence (docs/20 §20.10) — shared by the opportunities card, the
« Préconisations » of ``/lettres``, ``letters-trace`` and the Telegram alert of the letters review.

Written by rules from ``details.verdict``, ``details.verdict_motif`` (a gate line, verbatim) and
``details.verdict_chiffres`` (numbers computed by the engine in Python): never by the LLM, never with a number that
is not in the proposal's details."""

from __future__ import annotations

from typing import Any

LABELS = {
    "entrer_maintenant": "Entrer maintenant",
    "fiche_fixing": "Fiche au fixing de clôture",
    "alerte_declencheur": "Alerte au déclencheur",
    "attendre_repli": "Attendre un repli",
    "bloque": "Bloqué",
    "rejet": "Rejeté",
    "non_analysable": "Non analysable",
}
TONES = {
    "entrer_maintenant": "green",
    "fiche_fixing": "green",
    "alerte_declencheur": "blue",
    "attendre_repli": "blue",
    "bloque": "red",
    "rejet": "slate",
    "non_analysable": "amber",
}
MODES = {
    "achat": "prix d'achat de la lettre",
    "support": "repli sur le support de la lettre",
    "declencheur": "cassure du déclencheur de la lettre",
    "marche": "entrée au cours (la lettre ne donne pas de prix d'entrée)",
    "outil": "niveaux de l'outil (la lettre n'en donne aucun)",
}
LEVEL_LABELS = {
    "achat": "achat",
    "declencheur": "déclencheur",
    "objectif": "objectif 1",
    "objectif_2": "objectif 2",
    "objectif_3": "objectif 3",
    "stop": "invalidation",
    "support": "support",
    "resistance": "résistance",
}


def _details(p: Any) -> dict[str, Any]:
    if isinstance(p, dict):
        return p
    return getattr(p, "details", None) or {}


def _n(x: Any) -> str:
    return f"{float(x):.4g}".replace(".", ",")


def _why(line: Any) -> str:
    text = str(line or "").strip()
    for prefix in ("WATCH : ", "BLOQUÉ : "):
        text = text.removeprefix(prefix)
    return text[:240].rstrip(" .;")


def label(p: Any) -> str | None:
    v = _details(p).get("verdict")
    return LABELS.get(v) if v else None


def tone(p: Any) -> str:
    return TONES.get(str(_details(p).get("verdict")), "slate")


def failed_checks(p: Any) -> list[dict[str, Any]]:
    return [c for c in _details(p).get("controle") or [] if isinstance(c, dict) and not c.get("ok")]


def sentence(p: Any) -> str | None:
    """The verdict in one sentence, or None for a proposal without a letter verdict."""
    d = _details(p)
    v = d.get("verdict")
    if not v:
        return None
    c = d.get("verdict_chiffres") or {}
    why = _why(d.get("verdict_motif"))
    stop = f"stop {_n(c['stop'])}" if c.get("stop") is not None else "stop indisponible"
    if c.get("stop") is not None and c.get("stop_retenu") == "structure" and c.get("stop_lettre") is not None:
        stop += f" (structure ; invalidation de la lettre {_n(c['stop_lettre'])})"
    tgt = f", objectif {_n(c['objectif'])}" if c.get("objectif") is not None else ""
    pmax = _n(c["p_max"]) if c.get("p_max") is not None else "—"
    if v == "entrer_maintenant":
        size = f" ({c['titres']} titres, compte {c.get('compte')})" if c.get("titres") else ""
        on = f" sur la clôture du {c['jour_cours']}" if c.get("jour_cours") else ""
        return f"Entrée possible : ordre limite à {pmax}{size}{on} ; {stop}{tgt}."
    if v == "fiche_fixing":
        return (
            f"Entrée possible au fixing de clôture si le cours reste ≤ {pmax} : cours {c.get('statut_cours')}, "
            f"pas d'ordre en séance (règle 2), fiche à 17:15 ; {stop}{tgt}."
        )
    if v == "alerte_declencheur":
        if "au déclencheur" in why:
            return f"Alerte au déclencheur seulement, aucun ordre : {why}."
        trig = _n(c["declencheur"]) if c.get("declencheur") is not None else _n(c.get("seuil") or 0)
        dist = (
            f", à {_n(c['distance_declencheur_pct'])} %{' (proche)' if c.get('declencheur_proche') else ''}"
            if c.get("distance_declencheur_pct") is not None
            else ""
        )
        return (
            f"Alerte au franchissement de {trig} (cours {_n(c['cours'])}{dist}) : ordre à plage de déclenchement "
            f"(seuil {_n(c['seuil'])}, limite {pmax}) après une clôture au-dessus ; {stop}{tgt}."
        )
    if v == "attendre_repli":
        gap = f", cours {_n(c['cours'])} à {_n(c['ecart_limite_pct'])} % au-dessus" if c.get("ecart_limite_pct") else ""
        return f"Attendre un repli : limite à {pmax}{gap} ; {stop}{tgt}."
    if v == "bloque":
        return f"Pas d'entrée : {why}."
    if v == "rejet":
        return f"Rejeté : {why}."
    return f"Non analysable : {why}."
