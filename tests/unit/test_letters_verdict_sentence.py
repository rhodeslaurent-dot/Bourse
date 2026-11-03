"""03/11/2026 — a letter card whose R4 was computed on the final target (D12) says « objectif final … pari », never
only the first target (Elior: « objectif 1,957 » was shown for a bet on 2,156)."""

from __future__ import annotations

from app.services.letters_verdict import sentence


def test_the_bet_target_is_said():
    nums = {"p_max": 1.899, "stop": 1.846, "objectif": 2.156, "objectif_1": 1.957, "titres": 1191,
            "compte": "cto_cash"}  # fmt: skip
    d = {"verdict": "entrer_maintenant", "verdict_chiffres": nums}
    s = sentence(d)
    assert "objectif final 2,156" in s and "objectif 1 1,957" in s and "pari sur l'objectif final" in s
    d["verdict_chiffres"].pop("objectif_1")
    assert sentence(d).endswith("objectif 2,156.")
