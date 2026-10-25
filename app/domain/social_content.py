"""docs/30 S4a — what retail chatter is about: aggregates of the classified forum messages of a value, and the
strength of the signal. Pure functions: the LLM has only labelled each message (closed lists); counting,
author diversity and every decision are made here. Author keys are ephemeral salted hashes, never stored."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

THEMES = (
    "resultats", "guidance", "contrat", "m_and_a", "augmentation_capital", "gouvernance", "reco_broker",
    "transaction_dirigeant", "franchissement_seuil", "rachat_actions", "reglementaire", "dividende",
    "technique", "sentiment", "autre",
)  # fmt: skip
NON_FACTUAL = frozenset({"technique", "sentiment", "autre"})
NATURES = (
    "fait_source", "relais_presse", "rumeur", "opinion", "question", "promotion", "pretendue_info_privilegiee",
)  # fmt: skip
DIRECTIONS = ("positif", "negatif", "neutre", "ambigu")
UNCLASSIFIED = "non_classe"
INSIDER = "pretendue_info_privilegiee"
PROMOTION = "promotion"
ABOUT_FACTS = frozenset({"fait_source", "relais_presse", "rumeur", "opinion"})  # a question or a pitch is not one

STRONG, WEAK, NOISE, UNKNOWN = "fort", "faible", "bruit", "indisponible"
PUMP, REINFORCED = "emballement", "renforce"


@dataclass(frozen=True)
class Message:
    author: str  # ephemeral key (salted hash, changed at each read) — never a pseudonym, never stored
    ts: float | None  # POSIX time when the page gives it
    theme: str = UNCLASSIFIED
    nature: str = UNCLASSIFIED
    direction: str = UNCLASSIFIED
    precise: bool = False
    summary: str = ""

    @property
    def classified(self) -> bool:
        return self.theme != UNCLASSIFIED and self.nature != UNCLASSIFIED


@dataclass(frozen=True)
class ContentParams:
    min_distinct_authors: int = 3
    author_concentration_max: float = 0.4
    promotion_share_max: float = 0.3
    pump_min_messages: int = 5
    summaries_max: int = 3


@dataclass(frozen=True)
class Aggregate:
    messages: int
    recent_messages: int  # within the short window (2 h)
    classified: int
    distinct_authors: int
    concentration: float | None  # share of the most active author
    promotion_share: float | None
    insider_claims: int  # never used: excluded from every count below (MAR art. 8 and 14)
    by_theme: dict[str, int]
    by_nature: dict[str, int]
    dominant_theme: str | None
    theme_authors: int  # distinct authors on the dominant theme
    direction: str | None  # majority direction on the dominant theme
    precise_share: float | None
    strength: str
    reasons: list[str] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "messages": self.messages,
            "messages_recents": self.recent_messages,
            "classes": self.classified,
            "auteurs_distincts": self.distinct_authors,
            "concentration": None if self.concentration is None else round(self.concentration, 2),
            "part_promotion": None if self.promotion_share is None else round(self.promotion_share, 2),
            "pretendues_infos_privilegiees": self.insider_claims,
            "par_theme": self.by_theme,
            "par_nature": self.by_nature,
            "theme_dominant": self.dominant_theme,
            "auteurs_sur_theme": self.theme_authors,
            "direction": self.direction,
            "part_precise": None if self.precise_share is None else round(self.precise_share, 2),
            "solidite": self.strength,
            "motifs": self.reasons,
        }


def aggregate(msgs: list[Message], p: ContentParams, now: float | None = None, recent_h: float = 2.0) -> Aggregate:
    """Counts, author diversity and strength of the chatter about a value. A message claiming inside
    information is counted apart and never contributes; without any classified message the strength is
    « indisponible » (the counts and the concentration still hold: they need no classification)."""
    n = len(msgs)
    recent = sum(1 for m in msgs if now is not None and m.ts is not None and m.ts >= now - recent_h * 3600)
    known = [m for m in msgs if m.author]  # an author the page did not show is unknown, not « one author »
    by_author = Counter(m.author for m in known)
    concentration = max(by_author.values()) / len(known) if known else None
    insider = [m for m in msgs if m.nature == INSIDER]
    usable = [m for m in msgs if m.classified and m.nature != INSIDER]
    promo = sum(1 for m in usable if m.nature == PROMOTION)
    promo_share = promo / len(usable) if usable else None
    by_theme = dict(Counter(m.theme for m in usable).most_common())
    by_nature = dict(Counter(m.nature for m in usable).most_common())
    factual = [m for m in usable if m.theme not in NON_FACTUAL and m.nature in ABOUT_FACTS]
    reasons: list[str] = []
    on_theme: list[Message] = []
    dominant: str | None = None
    if insider:
        reasons.append(f"{len(insider)} message(s) se réclamant d'une information privilégiée : non exploité(s)")
    if not usable:
        strength = UNKNOWN
        reasons.append("aucun message classé")
    elif not factual:
        strength = NOISE
        reasons.append("aucun fait évoqué (opinions, technique, promotion)")
    else:
        counts = Counter(m.theme for m in factual)
        dominant = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        on_theme = [m for m in factual if m.theme == dominant]
        authors = len({m.author for m in on_theme if m.author})
        ok_authors = authors >= p.min_distinct_authors
        ok_conc = concentration is not None and concentration <= p.author_concentration_max
        ok_promo = promo_share is not None and promo_share < p.promotion_share_max
        strength = STRONG if ok_authors and ok_conc and ok_promo else WEAK
        if not known:
            reasons.append("auteurs non lus sur la page : diversité inconnue")
        elif not ok_authors:
            reasons.append(f"{authors} auteur(s) sur le thème (< {p.min_distinct_authors})")
        if not ok_conc and concentration is not None:
            reasons.append(f"un auteur porte {concentration:.0%} des messages")
        if not ok_promo and promo_share is not None:
            reasons.append(f"messages promotionnels : {promo_share:.0%}")
    dirs = Counter(m.direction for m in on_theme if m.direction in DIRECTIONS)
    direction = None
    if dirs:
        top, k = dirs.most_common(1)[0]
        direction = top if k > sum(dirs.values()) / 2 else "ambigu"
    precise_share = sum(1 for m in on_theme if m.precise) / len(on_theme) if on_theme else None
    return Aggregate(
        n, recent, sum(1 for m in msgs if m.classified), len(by_author), concentration, promo_share, len(insider),
        by_theme, by_nature, dominant, len({m.author for m in on_theme if m.author}), direction, precise_share,
        strength, reasons, pick_summaries(on_theme, p.summaries_max),
    )  # fmt: skip


def pick_summaries(msgs: list[Message], n: int) -> list[str]:
    """Up to ``n`` neutral summaries on the dominant theme: precise first, sourced facts and rumours before
    opinions, one per author, newest first."""
    rank = {"fait_source": 0, "relais_presse": 1, "rumeur": 2, "opinion": 3}
    out, seen = [], set()
    for m in sorted(msgs, key=lambda m: (not m.precise, rank.get(m.nature, 9), -(m.ts or 0.0))):
        if (m.author and m.author in seen) or not m.summary.strip():
            continue
        seen.add(m.author)
        out.append(m.summary.strip())
        if len(out) >= n:
            break
    return out


def effect(agg: Aggregate, small_cap: bool, p: ContentParams) -> str | None:
    """``emballement`` (entry blocked: a small cap pushed by few accounts or by pitches), ``renforce`` (strong,
    factual, diverse chatter: a reason to look first — never a reason to buy) or None."""
    if agg.messages >= p.pump_min_messages and small_cap:
        concentrated = agg.concentration is not None and agg.concentration > p.author_concentration_max
        pitched = agg.promotion_share is not None and agg.promotion_share >= p.promotion_share_max
        if concentrated or pitched:
            return PUMP
    return REINFORCED if agg.strength == STRONG else None
