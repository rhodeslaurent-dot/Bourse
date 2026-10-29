"""Names of the price levels read in a letter (report of 29/10/2026). Pure.

The reader (LLM) was asked for « entrée, objectif, stop, support, résistance » and answered free keys
(« entrée », « objectif_1 », « invalidation »…) while the engine looks for ``achat``, ``objectif``, ``stop``,
``support`` (R15): a letter with an entry, a target and an invalidation was never recalculated. Every key is put
back in one closed list; an unknown key is dropped (and said), never guessed.
"""

from __future__ import annotations

import re
import unicodedata

KEYS = ("achat", "declencheur", "objectif", "objectif_2", "objectif_3", "stop", "support", "resistance")

_ALIASES = {
    "achat": "achat",
    "entree": "achat",
    "entry": "achat",
    "prix d achat": "achat",
    "zone d achat": "achat",
    "buy": "achat",
    "declencheur": "declencheur",
    "trigger": "declencheur",
    "breakout": "declencheur",
    "cassure": "declencheur",
    "franchissement": "declencheur",
    "sortie": "declencheur",
    "stop": "stop",
    "stop loss": "stop",
    "invalidation": "stop",
    "seuil d invalidation": "stop",
    "niveau d invalidation": "stop",
    "seuil": "stop",
    "support": "support",
    "resistance": "resistance",
}
_TARGET = re.compile(r"^(?:objectif|cible|target|tp)\s*(?:n\s*)?(\d)?$")


def _plain(key: str) -> str:
    t = unicodedata.normalize("NFKD", key)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def canonical_key(key: str) -> str | None:
    k = _plain(key)
    if k in _ALIASES:
        return _ALIASES[k]
    m = _TARGET.match(k)
    if m:
        n = int(m.group(1) or 1)
        return "objectif" if n <= 1 else f"objectif_{n}" if n <= 3 else None
    return None


def canonical_levels(levels: dict[str, float]) -> tuple[dict[str, float], list[str]]:
    """(levels under the closed keys, keys dropped). The first value wins for a key; an explicit ``stop`` is kept
    over an invalidation read under another word."""
    out: dict[str, float] = {}
    dropped: list[str] = []
    for key, value in sorted(levels.items(), key=lambda kv: _plain(kv[0]) != "stop"):
        ck = canonical_key(key)
        if ck is None:
            dropped.append(key)
            continue
        out.setdefault(ck, float(value))
    return out, dropped


def recalculable(levels: dict[str, float] | None) -> bool:
    """R15 as the engine applies it: a stop, a target, and an entry (buy level, support or breakout trigger)."""
    lv = levels or {}
    entry = lv.get("achat") is not None or lv.get("support") is not None or lv.get("declencheur") is not None
    return lv.get("stop") is not None and lv.get("objectif") is not None and entry
