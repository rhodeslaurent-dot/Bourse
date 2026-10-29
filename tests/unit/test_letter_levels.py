"""29/10/2026: the reader's free level names (« entrée », « objectif 1 », « invalidation ») were never those the
engine reads (achat, objectif, stop): one closed list, unknown names dropped and said."""

from app.domain.letter_levels import canonical_levels, recalculable


def test_free_names_go_back_to_the_closed_list():
    got, dropped = canonical_levels(
        {"Entrée": 170.0, "objectif 1": 195.7, "Objectif_2": 209.6, "invalidation": 154.9, "humeur": 3.0}
    )
    assert got == {"achat": 170.0, "objectif": 195.7, "objectif_2": 209.6, "stop": 154.9}
    assert dropped == ["humeur"] and recalculable(got)


def test_an_explicit_stop_wins_over_an_invalidation_and_a_range_exit_is_a_trigger():
    got, _ = canonical_levels({"invalidation": 80.0, "stop": 82.6, "déclencheur": 100.2, "objectif": 109.1})
    assert got["stop"] == 82.6 and got["declencheur"] == 100.2 and recalculable(got)
    assert not recalculable({"objectif": 109.1, "stop": 82.6})  # no entry of any kind
