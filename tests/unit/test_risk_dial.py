"""docs/28 — the daily risk level: components, bounds, conviction, plain explanation."""

from app.domain.risk_dial import DialInput, DialParams, conviction, recommend

P = DialParams()


def _inp(**kw):
    base = {"regime": "green", "conjuncture": "modere", "open_lines": 5, "strong_setups": 0,
            "results_multiplier": 1.0, "results_reason": "", "edge_measured": False}  # fmt: skip
    return DialInput(**{**base, **kw})


def test_normal_day_is_one_and_explained():
    d = recommend(_inp(), P)
    assert d.level == 1.0 and [c.label for c in d.components] == ["Marché", "Résultats récents", "Portefeuille"]
    text = d.explanation(300)
    assert text[0].startswith("Niveau de risque conseillé : × 1.00") and "300 €" in text[0]


def test_good_context_goes_up_only_with_a_measured_edge():
    good = _inp(conjuncture="faible", open_lines=2, strong_setups=4)
    assert recommend(good, P).level == 1.0 and "pas encore mesurée" in recommend(good, P).capped
    d = recommend(_inp(conjuncture="faible", open_lines=2, strong_setups=4, edge_measured=True), P)
    assert d.level == round(1.1 * 1.1, 3)


def test_bad_context_goes_down_but_never_below_the_floor():
    assert recommend(_inp(regime="orange"), P).level == 0.85
    d = recommend(_inp(regime="red", results_multiplier=0.75, results_reason="série de pertes"), P)
    assert d.level == 0.5 and "jamais en dessous" in d.capped
    assert recommend(_inp(open_lines=8), P).level == 0.9


def test_conviction_rewards_a_measured_edge_only():
    assert conviction(0.2, P)[0] == 1.15
    assert conviction(None, P) == (1.0, "") and conviction(0.05, P)[0] == 1.0


def test_rule_keys_drop_the_numbers():
    from app.services.risk_level import _rule_key

    assert _rule_key("BLOQUÉ : coupe-circuit : −4.3 R sur la semaine") == "BLOQUÉ : coupe-circuit"
    assert _rule_key("WATCH : ratio d'admission 0.56 < 0.75 (scoring)") == "WATCH : ratio d'admission"
