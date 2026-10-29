"""29/10/2026: « la lettre préconise Viridien, elle n'est ni dans la watchlist ni dans les opportunités ? » — the
trace says, for each value read, whether the engine could recalculate it and the proposal it led to, or why none."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.db.models import NewsletterItem, NewsletterValue, WatchlistRow
from app.db.session import db_session
from app.services.decisions import create_proposal, record_signal
from app.services.letter_trace import recalculable, trace

T = datetime(2026, 10, 29, 10, 52, tzinfo=UTC)


def test_the_trace_says_what_became_of_each_value(db_engine):
    with db_session() as s:
        it = NewsletterItem(source="momentum_capital", message_id="m1", dedup_hash="h1", received_at=T,
                            subject="Viridien : sortie par le haut ?", body_text="(privé)")  # fmt: skip
        s.add(it)
        s.flush()
        breakout = {"objectif": 109.1, "stop": 82.6}  # a range exit: no buy nor support level
        sig = record_signal(s, "FR001400PVN6", "external", "momentum_capital", T, "eod", "delayed", "t",
                            evidence={"direction": "buy", "levels": breakout})  # fmt: skip
        s.add_all([
            NewsletterValue(item_id=it.id, isin="FR001400PVN6", name="Viridien", direction="buy", levels=breakout,
                            signal_id=sig.id),
            NewsletterValue(item_id=it.id, isin=None, name="Sopra Steria", direction="buy",
                            levels={"objectif": 195.7, "stop": 154.9, "achat": 170.0}),
            NewsletterValue(item_id=it.id, isin="FR0000051732", name="Atos", direction="mention", levels={}),
            WatchlistRow(isin="FR001400PVN6", reason="external", source="momentum_capital", active=True),
        ])  # fmt: skip
        create_proposal(s, sig, "WATCH", None, {"blocked": []}, "t", to_verify=True, created_at=T)
        lines = trace(s, date(2026, 10, 29))
    text = "\n".join(lines)
    assert "Viridien (FR001400PVN6) · buy" in text and "niveaux manquants : achat, support ou déclencheur" in text
    assert "proposition n° " in text and ": WATCH · à vérifier" in text and "watchlist : oui" in text
    assert "Sopra Steria (ISIN non reconnu)" in text and "valeur non reconnue" in text
    assert "simple mention" in text
    assert "(privé)" not in text  # the letter's text is never printed
    assert recalculable({"objectif": 1, "stop": 0.5, "support": 0.8}) and not recalculable({"objectif": 1, "stop": 0.5})


def test_the_trace_shows_the_readers_stored_answer(db_engine, tmp_path):
    import hashlib

    from app.db.models import LlmCache

    (tmp_path / "newsletter_extract_v1.md").write_text("consigne", encoding="utf-8")
    with db_session() as s:
        it = NewsletterItem(source="momentum_capital", message_id="m2", dedup_hash="h2", received_at=T,
                            subject="BioMérieux : poursuite", body_text="corps privé")  # fmt: skip
        s.add(it)
        h = hashlib.sha256(b"m|newsletter_extract|1|consigne|corps priv\xc3\xa9").hexdigest()
        s.add(LlmCache(prompt_hash=h, response={"values": [
            {"name": "BioMérieux", "direction": "buy", "levels": {"objectif": 140.0}},
            {"name": "Veolia", "direction": "buy", "levels": {}},
        ]}))  # fmt: skip
        s.flush()
        text = "\n".join(trace(s, date(2026, 10, 29), ("m", tmp_path)))
    assert "réponse de l'IA (consigne v1) : 2 valeur(s), 1 avec niveaux" in text
    assert "IA : BioMérieux · buy · objectif 140.0" in text and "corps privé" not in text
