"""Price levels written in a letter, read by rules (lot R3, docs/38 §38.6). Pure, no LLM.

On 02/11/2026 the tool read no level in the Momentum fiches although they were written in plain words:
« On visera dans un premier temps le sommet historique à 105,20 euros », « clôture au-dessus de 1,87 € »,
« pour cible la zone 2,05 – 2,15 € », « stop d'invalidation sous 1,76 € », « Invalidation publiée : clôture sous
93,14 ». The former rule only took a number written right after the word (« objectif 60 », « stop à 52,50 »).

Here a **cue** (a word that names a level: objectif, vise, clôture au-dessus de, invalidation…) gives its kind to
the numbers that follow it in the same sentence:

- the number must come within ``CUE_WINDOW`` characters of the cue, with no other cue in between (the nearest cue
  wins: « achat sur repli, objectif de 60 » is a target, not a buy level);
- after a target cue, the next numbers of the list (« 105,20 puis … 106,45, 110,05 », « zone 2,05 – 2,15 ») are the
  following targets, each within ``CHAIN_GAP`` characters of the previous one; for any other kind the first number
  is kept;
- a percentage, a count (« titres », « séances »), a day of a date (« le 16 octobre »), an hour and the digits of an
  ISIN or of « MM50 » are never prices.

Every number is copied as written: nothing is computed or guessed (rule 3 holds for the rules as for the LLM).
"""

from __future__ import annotations

import re

CUE_WINDOW = 60  # characters between the end of a cue and its first number
CHAIN_GAP = 40  # characters between two numbers of one list of targets
TARGET_KEYS = ("objectif", "objectif_2", "objectif_3")

_CUES = re.compile(
    r"(?P<declencheur>d[ée]clencheur"
    r"|cl[ôo]tur(?:e|er|ant)\s+(?:(?:nette|franche)\s+)?au[- ]dessus\s+d"
    r"|(?:cassure|franchissement|d[ée]passement)\s+d"
    r"|au[- ]del[àa]\s+d"
    r"|passage\s+au[- ]dessus\s+d"
    r"|sortie\s+(?:par\s+le\s+haut|au[- ]dessus)"
    r"|(?:signal\s+d'achat|achat|acheter|renforcer|entrer)\s+au[- ]dessus\s+d)"
    r"|(?P<stop>stop(?:[- ]loss)?|invalidation|invalid(?:é|e)e?s?\b|invalider(?:ait|a)?)"
    r"|(?P<objectif>objectifs?|cibles?|\bvis(?:e|er|era|erons|ons|ent)\b|en\s+ligne\s+de\s+mire"
    r"|potentiel\s+(?:de\s+hausse\s+)?(?:jusqu'à|vers))"
    r"|(?P<support>supports?)"
    r"|(?P<resistance>r[ée]sistances?)"
    r"|(?P<achat>zone\s+d'achat|point\s+d'entr[ée]e|entr[ée]e|achat|acheter|achetez|accumuler)",
    re.I,
)
_MONTHS = "janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre"
_NOT_A_PRICE = (
    r"\s*(?:%|pb\b|pts?\b|points?\b|ans?\b|mois\b|jours?\b|s[ée]ances?\b|semaines?\b|titres\b|actions\b"
    r"|millions?\b|milliards?\b|m€|mds?\b|md€|fois\b|x\b|h\d|h\b|e\b|er\b|[èe]me\b|" + _MONTHS + ")"
)
_NUM = re.compile(
    r"(?<![\w/.,])(?P<int>\d{1,3}(?:[   ]\d{3})+(?![\d])|\d+)(?:[.,](?P<dec>\d{1,4}))?(?![\d/])"
    r"(?!" + _NOT_A_PRICE + r")",
    re.I,
)
# a sentence ends at . ! ? ; followed by a space, or at a line break (a decimal point is followed by a digit)
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")


def _clean(text: str) -> str:
    return (text or "").replace("’", "'").replace(" ", " ").replace(" ", " ")


def sentences(text: str) -> list[str]:
    return [x.strip() for x in _SENTENCE.split(_clean(text)) if x and x.strip()]


def _value(m: re.Match[str]) -> float | None:
    whole = re.sub(r"\s", "", m.group("int"))
    try:
        return float(f"{whole}.{m.group('dec')}" if m.group("dec") else whole)
    except ValueError:
        return None


def read_sentence(sentence: str) -> list[tuple[str, float]]:
    """(kind, number) pairs of one sentence, in the order written (kinds: the cue groups; targets unnumbered)."""
    s = _clean(sentence)
    cues = [(m.start(), m.end(), m.lastgroup or "") for m in _CUES.finditer(s)]
    out: list[tuple[str, float]] = []
    last: tuple[str, int] | None = None  # (kind, end of the previous number of the chain)
    for m in _NUM.finditer(s):
        val = _value(m)
        if val is None or val <= 0:
            continue
        before = [c for c in cues if c[1] <= m.start()]
        cue = before[-1] if before else None
        if cue is not None and m.start() - cue[1] <= CUE_WINDOW and (last is None or cue[1] > last[1]):
            kind = cue[2]
            prev = before[-2] if len(before) > 1 else None
            if kind in ("resistance", "support") and prev and prev[2] == "declencheur" and cue[0] - prev[1] <= 20:
                kind = "declencheur"  # « cassure de la résistance des 1,87 »: the breakout level is the trigger
            out.append((kind, val))  # a cue's first number
            last = (kind, m.end())
        elif last is not None and last[0] == "objectif" and m.start() - last[1] <= CHAIN_GAP:
            if cue is None or cue[1] <= last[1]:  # no new cue since the previous target
                out.append(("objectif", val))
                last = ("objectif", m.end())
            else:
                last = None
        else:
            last = None
    return out


def read_levels(text: str) -> dict[str, float]:
    """The levels of a passage under the closed list of names (``app.domain.letter_levels.KEYS``): the first number
    of each kind wins; targets are numbered in the order written (objectif, objectif_2, objectif_3)."""
    out: dict[str, float] = {}
    targets: list[float] = []
    for sent in sentences(text):
        for kind, val in read_sentence(sent):
            if kind == "objectif":
                if val not in targets:
                    targets.append(val)
            else:
                out.setdefault(kind, val)
    for key, val in zip(TARGET_KEYS, targets, strict=False):
        out[key] = val
    return out


def merge_levels(base: dict[str, float], extra: dict[str, float]) -> dict[str, float]:
    """``base`` completed by ``extra`` (a later passage of the same fiche): a level already read is kept; the targets
    of both are put in one list, in the order read, then numbered again."""
    out = {k: v for k, v in base.items() if k not in TARGET_KEYS}
    for k, v in extra.items():
        if k not in TARGET_KEYS:
            out.setdefault(k, v)
    targets: list[float] = []
    for src in (base, extra):
        for k in TARGET_KEYS:
            if k in src and src[k] not in targets:
                targets.append(src[k])
    for key, val in zip(TARGET_KEYS, targets, strict=False):
        out[key] = val
    return out


__all__ = ["CHAIN_GAP", "CUE_WINDOW", "merge_levels", "read_levels", "read_sentence", "sentences"]
