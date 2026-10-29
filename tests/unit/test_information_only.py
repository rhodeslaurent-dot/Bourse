"""User decision of 29/10/2026: the cards that cannot become an order are folded « pour information »."""

from types import SimpleNamespace

from app.web.opportunities_routes import information_only


def _it(blocked, p_max=None, last=None):
    p = SimpleNamespace(gates={"blocked": blocked}, details={"p_max": p_max} if p_max else {})
    return {"p": p, "snapshot_price": {"last": last} if last else None}


def test_what_cannot_become_an_order_is_information_only():
    watch_only = (
        "WATCH : espérance historique négative au coût complet (backtest n° 2, docs/27) — surveillance seulement"
    )
    assert information_only(_it([watch_only]), 0.05)
    assert information_only(_it(["WATCH : pas d'objectif exploitable (aucune résistance)"]), 0.05)
    assert information_only(
        _it(["WATCH : objectif trop proche — le prix maximal (98.3) tombe au niveau du stop"]), 0.05
    )
    wait = "WATCH : refusé — P_max 100 dépassé — limite posée en attente d'un repli"
    assert information_only(_it([wait], p_max=100.0, last=107.0), 0.05)  # 7 % above its limit
    assert not information_only(_it([wait], p_max=100.0, last=103.0), 0.05)  # within reach
    assert not information_only(_it(["WATCH : ratio d'admission 0.70 < 0.75 (scoring)"]), 0.05)
