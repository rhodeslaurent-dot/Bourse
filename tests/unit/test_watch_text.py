"""18/11/2026 — « incomprehensible descriptions »: one or two plain sentences per watched value."""

from __future__ import annotations

from datetime import datetime

from app.services.watch_text import why


def test_each_reason_in_plain_french():
    m = why("momentum", "rs_rank", {"mm50": 10.0, "mm200": 9.0}, 87.0)
    assert m.startswith("Valeur en forte tendance haussière : elle fait mieux que 87 % des valeurs suivies")
    assert "{" not in m and "mm50" not in m  # never a raw dictionary
    letter = why("external", "momentum_capital", {"achat": 98.0, "stop": 93.0, "objectif": 110.0}, None)
    assert (
        letter == "Recommandée par Momentum Capital : achat vers 98, stop 93, objectif 110. "
        "Les niveaux sont ceux de la lettre."
    )
    c = why(
        "catalyseur",
        "resultats",
        {"fait": "résultats publiés le 17/11, salués par le marché", "expire_le": "2026-12-02"},
        None,
    )
    assert c.startswith("Actualité récente : résultats publiés le 17/11") and "jusqu'au 02/12" in c
    assert why("manual", "manual", {"note": "GTA VI"}, None, None, datetime(2026, 11, 18)) == (
        "Ajoutée par toi le 18/11 — note GTA VI."
    )
