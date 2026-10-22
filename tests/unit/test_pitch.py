"""The one-sentence summary of a proposal is written by rules from the stored evidence (rule 3)."""

from app.services.pitch import pitch


def test_each_detector_has_a_sentence_built_only_from_its_evidence():
    s = pitch("d3_range_expansion", {"hausse_du_jour": 0.0504, "rvol": 2.34, "rs_rank": 91})
    assert s == (
        "Séance d'accélération : +5,0 % sur un volume 2,3× la moyenne, clôture dans le haut de la séance, "
        "au sortir d'une base calme ; force relative 91/100."
    )
    assert "MM50 au-dessus de la MM200 en hausse de 1,2 %" in pitch("d7_trend_quality", {"pente_mm200": 0.012})
    assert "ses MM10 et MM20, qui tiennent" in pitch(
        "d4_pullback", {"repli_pct": 0.06, "moyennes_touchees": ["mm10", "mm20"]}
    )
    assert "(25,2)" in pitch("d2_breakout", {"pivot_60": 25.2})
    assert "de momentum_capital" in pitch("external", {}, "momentum_capital")
    assert pitch("d2_breakout", {}) is None and pitch(None, None) is None  # no evidence → no sentence
