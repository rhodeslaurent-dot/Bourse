"""Lot R1 (docs/38) — one decision per idea, its main reason in plain words with its figures, what to do, the gross
table per target and the verdict of the day; the gate texts are the engine's own (copied from the cards of
02/11/2026)."""

from __future__ import annotations

import pytest

from app.domain.review import DECISIONS, Idea, day_verdict, idea_verdict, main_reason, target_rows

SCORE = "WATCH : ratio d'admission 0.56 < 0.80 (scoring)"
PULLBACK = "WATCH : refusé — P_max 98.08 dépassé de 2.01% — limite posée en attente d'un repli"
WIDE = "WATCH : stop trop large (9.4% sous l'entrée)"
NO_TARGET = "WATCH : pas d'objectif exploitable (aucune résistance ni niveau publié au-dessus)"


def test_main_reason_is_plain_ordered_and_keeps_its_figures():
    assert main_reason([SCORE, PULLBACK]) == "le dossier est trop faible au score (56% des points, 80% requis)"
    assert main_reason([WIDE, SCORE]) == "le stop serait trop loin sous l'entrée (9.4% sous l'entrée)"
    assert main_reason([PULLBACK]) == "le cours est au-dessus du prix maximal d'achat"
    assert main_reason([NO_TARGET]).startswith("aucun objectif chiffrable")
    # an absolute block wins and its own words are kept
    r = main_reason([SCORE, "BLOQUÉ : plafond de risque ouvert dépassé (risk.max_open_risk_pct)"])
    assert r.startswith("un plafond de risque") and "risk.max_open_risk_pct" in r
    assert main_reason(["WATCH : une raison nouvelle"]) == "une raison nouvelle"  # never dropped
    assert main_reason([]) is None


@pytest.mark.parametrize(
    ("idea", "decision", "todo"),
    [
        (Idea("BUY", (), price=100.0, p_max=99.5, stop=96.0, shares=40, expiry="05/11/2026"), "acheter",
         "Poser l'ordre limite à 99.5 (40 titres, stop 96), valable jusqu'au 05/11/2026"),
        (Idea("BUY", (), p_max=52.6, stop=49.16, plage_threshold=51.0), "acheter",
         "Poser l'ordre à plage de déclenchement : seuil 51, limite 52.6 (stop 49.16)"),
        (Idea("WATCH", (PULLBACK,), price=100.05, p_max=98.08, stop=97.66), "attendre_repli",
         "Rien aujourd'hui ; une limite à 98.08 n'achèterait qu'en cas de repli"),
        (Idea("WATCH", (SCORE, PULLBACK), price=100.05, p_max=98.08), "surveiller", "Rien aujourd'hui"),
        (Idea("WATCH", (NO_TARGET,), price=85.69), "surveiller", "Rien aujourd'hui"),
        (Idea("BUY", ("BLOQUÉ : coupe-circuit déclenché",), p_max=10.0), "refus", "Rien à faire"),
        (Idea("NO_TRADE", (WIDE,)), "refus", "Rien à faire"),
        (Idea("BUY", (), to_verify=True, p_max=10.0), "a_verifier",
         "Vérifier le cours en direct chez le courtier avant toute décision"),
    ],
)  # fmt: skip
def test_one_decision_per_idea_and_what_to_do(idea, decision, todo):
    v = idea_verdict(idea)
    assert v.decision == decision and v.decision in DECISIONS and v.todo == todo
    assert v.reason


def test_a_trigger_above_the_maximum_price_is_inexploitable_by_construction():
    # Elior, 02/11/2026: trigger 1.87 published by the letter, the tool's maximum entry price 1.762
    v = idea_verdict(Idea("WATCH", (WIDE, PULLBACK), price=1.887, p_max=1.762, letter_trigger=1.87))
    assert v.decision == "inexploitable" and v.label == "INEXPLOITABLE"
    assert "1.762" in v.reason and "1.87" in v.reason and "repli" in v.todo
    assert idea_verdict(Idea("WATCH", (PULLBACK,), p_max=1.9, letter_trigger=1.87)).decision == "attendre_repli"


def test_the_pullback_reason_says_how_far_above_the_limit():
    v = idea_verdict(Idea("WATCH", (PULLBACK,), price=100.05, p_max=98.08))
    assert v.reason == "le cours (100.05) est 2.0% au-dessus du prix maximal d'achat (98.08)"


def test_prices_keep_their_useful_decimals():
    from app.domain.review import fmt_price

    assert [fmt_price(x) for x in (100.05, 98.0812, 52.6, 1.762, 1.87, 0.0452, 8052.12, None)] == [
        "100.05", "98.08", "52.6", "1.762", "1.87", "0.0452", "8052.12", "—"]  # fmt: skip


def test_target_rows_are_gross_arithmetic_or_empty():
    rows = target_rows(100.0, 95.0, [105.0, 110.0])
    assert rows[0]["gain_brut"] == pytest.approx(0.05) and rows[0]["rr_brut"] == pytest.approx(1.0)
    assert rows[1]["rr_brut"] == pytest.approx(2.0)
    assert target_rows(None, 95.0, [105.0]) == [{"objectif": 105.0, "gain_brut": None, "rr_brut": None}]
    assert target_rows(100.0, 101.0, [105.0])[0]["rr_brut"] is None  # stop above the price: no ratio


def test_day_verdict_headline_and_reasons():
    head, why = day_verdict(["attendre_repli"] * 4 + ["surveiller"])
    assert head == "Aucun achat aujourd'hui"
    assert why[0] == "4 idée(s) au-dessus de leur prix maximal (limites prêtes pour un repli)"
    assert day_verdict(["acheter", "refus"])[0] == "1 achat(s) possible(s) aujourd'hui"
    head, why = day_verdict(["acheter"], circuit_tripped=True)
    assert head == "Aucun achat aujourd'hui" and why[0].startswith("coupe-circuit")
    assert day_verdict([])[0] == "Aucune idée aujourd'hui"
    assert day_verdict(["surveiller"], gate="restreint")[1][0] == "portail de marché : restreint"


def test_a_speculative_card_is_a_bet_never_all_rules_satisfied():
    """03/11/2026 (Elior): the graduated verdict made a « spéculative » BUY — score 0.42 < 0.80 and high risk moved to
    elements of appreciation, R4 computed on the final target (D12). The review says it is a bet, never « toutes les
    règles sont satisfaites », and the day's verdict does not count it as a buy."""
    v = idea_verdict(Idea("BUY", (), price=1.887, p_max=1.899, stop=1.846, shares=1191, speculative=True,
                          appreciation=("WATCH : ratio d'admission 0.42 < 0.80 (scoring)",
                                        "WATCH : risque élevé (distance du stop, liquidité)"),
                          bet_target=2.156))  # fmt: skip
    assert v.decision == "pari" and v.label == "PARI SPÉCULATIF" and v.tone == "warn"
    assert v.reason.startswith("pari sur l'objectif final 2.156 ; le dossier est trop faible au score (42% des points")
    assert "aucun avantage validé par la mesure" in v.reason
    assert v.todo.startswith("Seulement si vous acceptez ce pari : poser l'ordre limite à 1.899 (1191 titres")
    head, why = day_verdict(["pari"])
    assert head == "Aucun achat aujourd'hui" and why[0].startswith("1 idée(s) en pari spéculatif")


def test_a_measured_negative_edge_is_an_observation_without_any_order():
    """Decision of 04/11/2026 (docs/39): the engine's observation gate → « OBSERVATION », nothing to type at the
    broker, even for a card that would have been a buy or a limit waiting for a pullback."""
    obs = ("WATCH : observation (espérance mesurée -0.25 R, backtest, entrée et sorties de production, n 4407) — "
           "aucun ordre tant que la mesure n'est pas positive (docs/39)")  # fmt: skip
    for idea in (Idea("WATCH", (obs,), price=100.0, p_max=99.5, stop=96.0, shares=40),
                 Idea("WATCH", (obs, PULLBACK), price=100.05, p_max=98.08, stop=97.66)):  # fmt: skip
        v = idea_verdict(idea)
        assert v.decision == "observation" and v.label == "OBSERVATION"
        assert v.reason.startswith(
            "aucun avantage mesuré : c'est une observation, sans ordre (espérance mesurée -0.25 R"
        )
        assert v.todo.startswith("Rien à saisir") and "ordre" not in v.todo.lower().replace("rien à saisir", "")
    head, why = day_verdict(["observation", "observation"])
    assert head == "Aucun achat aujourd'hui" and why[0].startswith("2 idée(s) en observation")
