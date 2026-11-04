"""04/11/2026 (analysis of the cards by the user's Claude project) — DKK/GBX prices labelled « € », « stop_atr 2,5
SEK », « sortie_au_temps_seances 20 SEK », two accounts at the same amount: the units are the listing's currency for
a price, « × ATR » for a multiple, sessions for a duration; one broker balance is said once."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.explain import entry_zone_text, explain_proposal


def test_the_entry_zone_gives_each_number_its_own_unit():
    ez = {"type": "ordre à plage de déclenchement", "seuil": 101.5, "limite": 102.0, "validite_seances": 3,
          "stop_atr": 2.5, "sortie_au_temps_seances": 20, "politique": True}  # fmt: skip
    txt = entry_zone_text(ez, "SEK")
    want = "seuil 101.5 SEK · limite 102 SEK · stop 2.5 × ATR sous l'exécution · sortie au temps après 20 séances"
    assert txt == want
    assert "€" in entry_zone_text({"limite": 9.5}, None) and entry_zone_text({}, "DKK") == "—"


def test_price_lines_use_the_listing_currency_and_a_shared_balance_is_said():
    p = SimpleNamespace(gates={}, entry_zone={"limite": 640.0}, stop=600.0, targets=[700.0], shares=None,
                        risk_eur=None, action="WATCH", details={}, to_verify=False, account_recommended=None,
                        size_eur=None, cost_estimate=None, horizon=None)  # fmt: skip
    sig = SimpleNamespace(evidence={"p_open": 641.0, "levels": {"support": 620.0}}, detector="d7", source="x")
    snap = SimpleNamespace(
        quotes={}, features={"day": "2026-11-03", "atr14": 12.0},
        regime={"day": "2026-11-03", "regime": "orange", "marche": "EU"},
        portfolio_state=[
            {"account_id": i, "type": t, "broker": "saxo", "cash": 5000.0, "synced_at": "t", "declarative": False}
            for i, t in ((2, "cto_cash"), (3, "cto_srd"))
        ],
    )  # fmt: skip
    ex = explain_proposal(p, sig, snap, currency="DKK")
    assert "limite 640.00 DKK" in ex.headline and "stop 600.00 DKK" in ex.headline and "€" not in ex.headline
    assert any("Ouverture du jour 641.00 DKK" in b for b in ex.bullets)
    assert any("cité par la source : 620.00 DKK" in b for b in ex.bullets)
    assert any("amplitude 14 j 12.00 DKK" in d for d in ex.data_used)
    assert any("(régime européen : la place n'a pas le sien)" in d for d in ex.data_used)
    srd = next(d for d in ex.data_used if d.startswith("CTO SRD"))
    assert "(même solde que CTO comptant, compté une fois)" in srd
