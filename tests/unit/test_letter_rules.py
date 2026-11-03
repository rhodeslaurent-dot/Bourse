"""Lot R3 (docs/38 §38.6) — the levels a letter writes in plain words, read by rules (no LLM): a cue gives its kind to
the numbers after it in the same sentence, the nearest cue wins, a list of targets is numbered, percentages, counts,
dates and the digits of an ISIN or of « MM50 » are never prices. Synthetic sentences (same wordings as the fiches of
02/11/2026, other values and numbers); the letters themselves are never committed."""

from __future__ import annotations

import pytest

from app.data.providers.imap_gmail import extract_values
from app.domain.letter_levels import within_band
from app.domain.letter_rules import merge_levels, read_levels


@pytest.mark.parametrize(
    ("text", "levels"),
    [
        # a target in a sentence, the cue far from the number
        ("La tendance reste solide. On visera dans un premier temps le plus haut annuel à 52,40 euros.",
         {"objectif": 52.4}),
        # a list of targets, then the invalidation written « clôture sous »
        ("Objectifs : 52,40 puis extensions de Fibonacci à 53,10, 55,75, 58,20. Invalidation publiée : clôture "
         "sous 47,85.", {"objectif": 52.4, "objectif_2": 53.1, "objectif_3": 55.75, "stop": 47.85}),
        # a breakout trigger, a target zone, a stop « d'invalidation sous »
        ("Le scénario suppose une clôture au-dessus de 3,42 €, premier signal, avec pour cible la zone 3,75 – 3,90 € "
         "et un stop d'invalidation sous 3,21 €.",
         {"declencheur": 3.42, "objectif": 3.75, "objectif_2": 3.9, "stop": 3.21}),
        # the nearest cue wins: « achat sur repli, objectif de 60 » is a target
        ("Achat sur repli, objectif de 60 € ; stop à 52,50 €.", {"objectif": 60.0, "stop": 52.5}),
        ("Entrée à 640 €, objectif 700 €, invalidation 600 €.", {"achat": 640.0, "objectif": 700.0, "stop": 600.0}),
        ("Renforcer au-dessus de 205, stop 195.", {"declencheur": 205.0, "stop": 195.0}),
        # « cassure de la résistance » names the trigger
        ("La cassure de la résistance des 12,80 € validerait le scénario.", {"declencheur": 12.8}),
        ("Rebond jeudi dernier sur le support de 41,15, résistance 44,30.", {"support": 41.15, "resistance": 44.3}),
        ("CAC 40 : support 7 400, résistance 7 650.", {"support": 7400.0, "resistance": 7650.0}),
        # descriptive numbers without a cue, dates, percentages, counts, ISIN and moving-average digits
        ("Plus haut historique à 105,20 le 16 octobre, MM50 à 98,10, le titre (FR0000000001) a perdu 32 % depuis le "
         "1er janvier ; 4 079 titres.", {}),
        ("Objectif atteint en 3 séances, potentiel de 15 %.", {}),
        # 03/11/2026: the invalidation written after the number; a support far from its word is the past, not a level
        ("Une clôture sous 3,21 € invaliderait ce scénario.", {"stop": 3.21}),
        ("Objectif 3,75, une clôture en dessous de 3,21 € invaliderait le scénario.", {"objectif": 3.75, "stop": 3.21}),
        ("La hausse s'appuie sur le support oblique tracé depuis le plus bas de mars à 41,58 €.", {}),
    ],
)  # fmt: skip
def test_levels_written_in_plain_words(text, levels):
    assert read_levels(text) == levels


def test_a_negative_fiche_keeps_its_support_not_a_target():
    lv = read_levels("L'auteur passe négatif et vise un retour sur le support majeur de 190 $, soit −7 %.")
    assert lv == {"support": 190.0}


def test_merge_keeps_the_first_level_and_numbers_the_targets_again():
    assert merge_levels({"stop": 10.0, "objectif": 12.0}, {"stop": 9.0, "objectif": 13.0, "objectif_2": 14.0}) == {
        "stop": 10.0, "objectif": 12.0, "objectif_2": 13.0, "objectif_3": 14.0}  # fmt: skip


def test_the_price_band_drops_levels_far_from_the_value():
    kept, dropped = within_band({"support": 7400.0, "objectif": 7650.0, "objectif_2": 2.1, "stop": 1.76}, 1.887,
                                (0.5, 2.0))  # fmt: skip
    assert kept == {"stop": 1.76, "objectif": 2.1} and dropped == ["support 7400", "objectif 7650"]
    assert within_band({"stop": 1.0}, None, (0.5, 2.0)) == ({"stop": 1.0}, [])  # no close: nothing dropped


ALIASES = {"alpha industries": "FR0000000AA1", "beta services": "FR0000000BB2"}
LETTER = """Les fiches du jour

Alpha Industries : le plus haut annuel en ligne de mire

Alpha Industries — achat : rebond sur le support de 41,15. On visera dans un premier temps le plus haut annuel à \
52,40 euros.

Objectifs : 52,40 puis 53,10. Invalidation publiée : clôture sous 39,90.

CAC 40 : support 7 400, résistance 7 650.

Beta Services : une clôture au-dessus de 3,42 € serait un premier signal, avec pour cible la zone 3,75 – 3,90 € et un \
stop d'invalidation sous 3,21 €. À surveiller.

Alpha Industries et Beta Services sont éligibles au PEA, objectif 3.
"""


def test_a_fiche_spread_over_paragraphs_is_read_for_its_value():
    by = {v.isin: v for v in extract_values(LETTER, ALIASES)}
    a, b = by["FR0000000AA1"], by["FR0000000BB2"]
    # the title first (neutral), then the paragraph that recommends; the next paragraph still belongs to the fiche
    assert a.direction == "buy" and a.levels == {"support": 41.15, "stop": 39.9, "objectif": 52.4,
                                                 "objectif_2": 53.1}  # fmt: skip
    # the market paragraph ends the fiche: its levels are nobody's; a sentence naming two values gives none
    assert b.direction == "watch" and b.levels == {"declencheur": 3.42, "stop": 3.21, "objectif": 3.75,
                                                   "objectif_2": 3.9}  # fmt: skip


def test_a_paragraph_naming_two_values_shares_its_levels_by_sentence():
    text = "Alpha Industries : objectif 52,40. Stop 39,90. Beta Services : cible 3,75, stop 3,21."
    by = {v.isin: v.levels for v in extract_values(text, ALIASES)}
    assert by == {"FR0000000AA1": {"objectif": 52.4, "stop": 39.9}, "FR0000000BB2": {"objectif": 3.75, "stop": 3.21}}
