"""18/11/2026 — user's remark: « the descriptions beside the watchlist values are incomprehensible; I want something
simple, clear, explicit and short ». One or two plain sentences per value, from the facts the row holds."""

from __future__ import annotations

from datetime import datetime
from typing import Any

SOURCES = {"momentum_capital": "Momentum Capital", "zonebourse": "Zonebourse", "rs_rank": "le classement de l'outil",
           "manual": "toi", "resultats": "ses résultats", "radar": "le radar d'actualité"}  # fmt: skip
LEVELS = {"achat": "achat vers", "entree": "achat vers", "seuil": "achat au-dessus de", "stop": "stop",
          "invalidation": "invalidé sous", "objectif": "objectif", "objectif_1": "objectif",
          "objectif_2": "objectif suivant", "note": "note"}  # fmt: skip


def _num(x: Any) -> str:
    if isinstance(x, int | float):
        return f"{x:,.2f}".replace(",", " ").replace(".", ",").replace(",00", "")
    return str(x)


def _levels(levels: dict[str, Any] | None) -> str:
    parts = []
    for k, v in (levels or {}).items():
        if v in (None, "", []) or k in ("mm50", "mm200", "fait", "expire_le"):
            continue
        if isinstance(v, list):
            v = " puis ".join(_num(x) for x in v)
        parts.append(f"{LEVELS.get(k, k.replace('_', ' '))} {_num(v)}")
    return ", ".join(parts)


def why(reason: str, source: str | None, levels: dict[str, Any] | None, rs_rank: float | None,
        feat: Any = None, added_at: datetime | None = None) -> str:  # fmt: skip
    """Why the value is watched, in plain French: what happened, and what to look for."""
    if reason == "momentum":
        rank = (
            f"fait mieux que {rs_rank:.0f} % des valeurs suivies sur les derniers mois" if rs_rank else "forte hausse"
        )
        return (f"Valeur en forte tendance haussière : elle {rank}, et son cours est au-dessus de ses moyennes "
                "50 et 200 jours. À surveiller pour un achat sur cassure d'un plus haut ou sur repli.")  # fmt: skip
    if reason == "catalyseur":
        fait = (levels or {}).get("fait") or "un fait récent"
        end = (levels or {}).get("expire_le")
        ok = isinstance(end, str) and len(end) == 10
        until = f" Elle reste dans la liste jusqu'au {end[8:10]}/{end[5:7]}." if ok else ""
        return (f"Actualité récente : {fait}. À suivre de près — ce n'est pas un signal d'achat en soi, "
                f"la tendance des derniers mois peut rester baissière.{until}")  # fmt: skip
    if reason == "external":
        who = SOURCES.get(source or "", source or "une lettre")
        lv = _levels(levels)
        return f"Recommandée par {who}" + (f" : {lv}." if lv else ".") + " Les niveaux sont ceux de la lettre."
    if reason == "a_l_affut":
        lv = _levels(levels)
        return "En attente d'un repli pour acheter moins cher" + (f" : {lv}." if lv else ".")
    if reason == "manual":
        when = f" le {added_at:%d/%m}" if added_at else ""
        lv = _levels(levels)
        return f"Ajoutée par toi{when}" + (f" — {lv}." if lv else ".")
    lv = _levels(levels)
    return f"Surveillée ({reason})" + (f" : {lv}." if lv else ".")
