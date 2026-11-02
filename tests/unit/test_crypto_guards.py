"""Lot M2 (docs/39 §4) — a crypto ETP is never bought with leverage (no SRD) nor in the PEA, and the crypto pocket is
closed while ``allocation.crypto_max_weight`` is 0 (decision D5); a pocket opened above 0 stays blocked until its
weight cap is checked by the tool. Other instruments are untouched."""

from __future__ import annotations

from app.domain.allocation import crypto_gates


def test_a_stock_is_untouched():
    assert crypto_gates("stock", ["pea", "cto_cash", "cto_srd"], 0.0) == (["pea", "cto_cash", "cto_srd"], [])


def test_a_crypto_etp_has_no_leverage_and_no_pea_and_a_closed_pocket_by_default():
    left, blocked = crypto_gates("crypto_etp", ["pea", "cto_cash", "cto_srd"], 0.0)
    assert left == ["cto_cash"] and blocked[0].startswith("BLOQUÉ : poche crypto fermée")
    left, blocked = crypto_gates("crypto_etp", ["cto_cash", "cto_srd"], None)
    assert left == ["cto_cash"] and blocked


def test_an_opened_pocket_stays_blocked_until_its_cap_is_checked():
    (_, (b,)) = crypto_gates("crypto_etp", ["cto_cash"], 0.02)
    assert b.startswith("BLOQUÉ : plafond de poids crypto (2%)")
