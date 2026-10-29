"""Company names in free text (user report of 29/10/2026). Pure.

Finding the values a letter, a news item or a forum message talks about used every name *and every ticker* of
the referential as a lowercase word. With New York, Warsaw and London in the referential, tickers became French
words: « sur » → Surteco (SUR), « aux » → Auxilia (AUX), « pas » → Passus (PAS), « mon » → Monnari (MON), « lien »
→ Chicago Atlantic (LIEN)… — five false recommendations and watchlist lines from one letter, while
« Sartorius Stedim Biotech », « TotalEnergies » or « Atos Group » were not recognised (their referential names
carry « S.A. », « SE », no « Group »).

- :func:`norm` puts a text and a name in the same form (no accents, lowercase, punctuation as spaces);
- :func:`core_name` drops the legal form and the group words (« SA », « SE », « plc », « Group », « Holding »…);
- :func:`mention_safe` says whether a name may be searched as a word in free text: several words, or one word of
  at least 4 letters that is not a common French or English word. Tickers are never searched in free text.
"""

from __future__ import annotations

import re
import unicodedata

LEGAL_WORDS = frozenset(
    """
    sa se nv ag plc inc incorporated corp corporation ltd limited spa sau sca scs kgaa gmbh bv ab asa oyj oy as
    publ co company companies cie group groupe grupo gruppe holding holdings the class ord ordinary shares adr ads
    reit trust sicav sppicav et
    """.split()
)

# common words that are also (parts of) company names: never searched alone in free text
COMMON_WORDS = frozenset(
    """
    aussi autre avant avec bien bourse cette chef chez comme dans depuis donc elle encore entre etre fait faire
    haute jamais jour leur leurs mais marche mois moins moment notre nous pays plus pour prix rien sans selon sens
    sera sont sous tous tout toute toutes tres vers votre cours hausse baisse niveau support resistance objectif
    achat vente signal tendance seance cloture ouverture resultats chiffre affaires semaine annee lien corps camp
    code page line site true test main gain life auto data next plus total orange rouge vert bleu noir blanc
    action actions valeur valeurs france europe capital energy energie global world international technology
    technologies solutions systems health bank banque first united american general national royal best home
    focus alpha beta delta omega nova atlas apex zone point link open free star solar smart digital media music
    games food water gold silver power steel metal mining premier prime core edge peak summit vision value growth
    income fund index market markets trading invest investment group holding services service industries
    industrie partners resources energy fed bce ecb
    """.split()
)


def norm(text: str) -> str:
    """No accents, lowercase, every character other than a letter, a digit or « & » as a space."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9&]+", " ", t)
    return " ".join(t.split())


def core_name(name: str) -> str:
    """« Sartorius Stedim Biotech S.A. » → « sartorius stedim biotech »; « Atos Group » → « atos »; « TotalEnergies
    SE » → « totalenergies ». Legal forms and group words are dropped at the end (and « the » at the start)."""
    words = norm(name).split()
    while words and (words[-1] in LEGAL_WORDS or len(words[-1]) == 1):
        words.pop()
    while words and words[0] == "the":
        words.pop(0)
    return " ".join(words)


def mention_safe(key: str) -> bool:
    """A normalized name that may be searched as a word in free text."""
    words = key.split()
    if not words:
        return False
    if len(words) >= 2:
        return len(key) >= 6 and not all(w in COMMON_WORDS or w in LEGAL_WORDS for w in words)
    w = words[0]
    return len(w) >= 4 and not w.isdigit() and w not in COMMON_WORDS


def name_keys(name: str) -> set[str]:
    """The normalized full name and its core: the keys under which a referential name is known."""
    return {k for k in (norm(name), core_name(name)) if k}


def find_mentions(text: str, index: dict[str, str]) -> list[tuple[str, str]]:
    """(ISIN, key) for each key of ``index`` (normalized names) written as whole words in ``text``, first
    occurrence order; one entry per ISIN."""
    t = f" {norm(text)} "
    hits: list[tuple[int, str, str]] = []
    for key, isin in index.items():
        pos = t.find(f" {key} ")
        if pos >= 0:
            hits.append((pos, isin, key))
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for _, isin, key in sorted(hits):
        if isin not in seen:
            seen.add(isin)
            out.append((isin, key))
    return out


def resolve(name: str, lookup: dict[str, str]) -> str | None:
    """ISIN of a company name given by a reader (LLM): the full normalized name first, then its core."""
    for k in (norm(name), core_name(name)):
        if k and k in lookup:
            return lookup[k]
    return None
