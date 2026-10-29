"""docs/20 §20.10 (user complaint of 29/10/2026: « les valeurs remontées par la lettre Momentum apparaissent en orange
sans analyse supplémentaire ») — from a value stored at reception to one proposal of the day built by the engine,
with what the letter says, what the tool sees, a verdict, a control list; the same sentence on the card and on the
letters page; the funnel of the letter."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import LetterOpinion, LetterReview, NewsletterItem, NewsletterValue, Proposal, Signal
from app.db.session import db_session
from app.domain.timeutil import PARIS
from app.services.letter_analysis import analyse_items, todays_letter_proposals
from app.services.letters_verdict import sentence
from tests.integration.test_engine import ISIN, _seed, breakout_bars

NOW = datetime.now(UTC)


def _letter(s, levels: dict, mid: str = "m1", direction: str = "buy") -> NewsletterItem:
    it = NewsletterItem(source="momentum_capital", message_id=mid, dedup_hash=f"h-{mid}", subject="Momentum",
                        received_at=NOW - timedelta(hours=1), body_text="(privé)")  # fmt: skip
    s.add(it)
    s.flush()
    s.add(NewsletterValue(item_id=it.id, isin=ISIN, name="TotalEnergies", direction=direction, levels=levels))
    s.flush()
    return it


def _letter_proposals(s) -> list[Proposal]:
    return [p for p in s.scalars(select(Proposal)) if s.get(Signal, p.signal_id).source == "moteur_lettre"]


def test_a_range_breakout_under_its_trigger_is_rejected_never_a_waiting_limit(db_engine, config):
    """Viridien-style: « sortie du range au-dessus de 100,20 », targets close, invalidation far under the range."""
    with db_session() as s:
        _seed(s, breakout_bars()[:-1])  # the range under 100, not crossed
        it = _letter(s, {"declencheur": 100.2, "objectif": 102.5, "objectif_2": 103.0, "stop": 82.6})
        rep, funnels = analyse_items(s, config, [it])
        props = _letter_proposals(s)
        assert len(props) == 1, rep.note()
        p = props[0]
        d = p.details
        assert d["verdict"] == "rejet" and "au déclencheur" in d["verdict_motif"]
        assert not any("en attente d'un repli" in b for b in p.gates["blocked"])
        r = d["ratio_au_declencheur"]
        assert r["ratio"] < 2 and r["dernier_objectif"] == 103.0 and r["dernier_objectif_suffit"] is False
        assert "au dernier objectif 103" in d["verdict_motif"]  # both ratios shown
        a = d["analyse_outil"]
        assert a["mode"] == "declencheur" and a["stop_lettre"] == 82.6 and a["stop_retenu"] == "structure"
        assert a["stop_structure"] > 82.6 and d["lettre"]["niveaux"]["declencheur"] == 100.2
        assert {c["cle"] for c in d["controle"]} >= {"valeur", "historique", "cours", "structure", "ratio", "stop"}
        assert funnels[0]["analysed"] == 1 and funnels[0]["verdicts"] == {"rejet": 1}
        from app.web.opportunities_routes import information_only

        assert information_only({"p": p, "snapshot_price": None}, 0.05)  # folded « pour information »


def test_a_breakout_that_would_pass_on_its_last_target_is_an_alert(db_engine, config):
    with db_session() as s:
        _seed(s, breakout_bars()[:-1])
        it = _letter(s, {"declencheur": 100.2, "objectif": 102.5, "objectif_2": 125.0, "stop": 82.6})
        analyse_items(s, config, [it])
        (p,) = _letter_proposals(s)
        assert p.details["verdict"] == "alerte_declencheur" and p.details["ratio_au_declencheur"]["ratio"] < 2
        from app.web.opportunities_routes import information_only

        assert not information_only({"p": p, "snapshot_price": None}, 0.05)


def test_a_breakout_not_crossed_waits_for_its_trigger(db_engine, config):
    import copy

    from app.scheduler.jobs.intraday_reeval import candidates

    config = copy.deepcopy(config)  # the score and the restricted gate are not the subject here
    config.params.scoring.update({"buy_min_ratio": 0.0, "buy_min_confidence": 0})
    config.params.market_gate.update({"restricted_min_net_rr": 0.0, "restricted_min_score": 0})

    with db_session() as s:
        _seed(s, breakout_bars()[:-1])
        it = _letter(s, {"declencheur": 100.2, "objectif": 125.0, "stop": 96.0})
        analyse_items(s, config, [it])
        (p,) = _letter_proposals(s)
        d = p.details
        assert d["verdict"] == "alerte_declencheur", d["verdict_motif"]
        assert any("déclencheur non franchi" in b for b in p.gates["blocked"])
        assert d["configuration"]["franchi"] is False and d["entree"]["type"].startswith("ordre à plage")
        assert d["p_max"] >= d["analyse_outil"]["entree"]  # admitted at the trigger, not at the price under it
        if not [b for b in p.gates["blocked"] if b.startswith(("BLOQUÉ", "WATCH")) and "non franchi" not in b]:
            assert p in candidates(s, datetime.now(UTC))  # the intraday follow-up watches the crossing


def test_a_letter_without_entry_level_is_analysed_and_both_pages_say_the_same(db_engine, config, calendars):
    """Sopra-style: trendline broken, targets and an invalidation, no entry level → entry at the price."""
    from app.main import create_app

    with db_session() as s:
        _seed(s, breakout_bars())
        it = _letter(s, {"objectif": 110.0, "objectif_2": 115.0, "stop": 97.0})
        day = NOW.astimezone(PARIS).date()
        rev = LetterReview(ts=NOW, day=day, trigger="reception", status="ok", item_ids=[it.id], rejected=[])
        s.add(rev)
        s.flush()
        s.add(LetterOpinion(review_id=rev.id, day=day, isin=ISIN, name="TotalEnergies", source="momentum_capital",
                            item_ids=[it.id], stance="positive", explicit=True, action="achat",
                            summary="Tendance cassée.", quote="ligne de tendance baissière cassée"))  # fmt: skip
        s.flush()
        analyse_items(s, config, [it])
        (p,) = _letter_proposals(s)
        d = p.details
        assert d["analyse_outil"]["mode"] == "marche" and d["verdict"] in (
            "entrer_maintenant", "fiche_fixing", "attendre_repli", "bloque", "rejet", "alerte_declencheur")  # fmt: skip
        assert d["lettre"]["citation"] == "ligne de tendance baissière cassée"
        phrase = sentence(p)
        assert phrase and len(d["controle"]) >= 10
        # every number of the sentence is in the proposal's details (computed by the engine or quoted)
        flat: set[str] = set()

        def walk(x):
            if isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
            elif isinstance(x, bool):
                return
            elif isinstance(x, int | float):
                flat.update({f"{x:.4g}", f"{x:g}", f"{x:.1f}", f"{x:.2f}"})
            elif isinstance(x, str):
                flat.update(n.replace(",", ".") for n in re.findall(r"\d+(?:[.,]\d+)?", x))

        walk(d)
        nums = [n.replace(",", ".") for n in re.findall(r"\d+(?:,\d+)?", phrase)]
        assert all(n in flat for n in nums), (phrase, [n for n in nums if n not in flat])
        pid = p.id
        assert todays_letter_proposals(s, day)[ISIN].id == pid
        trace = "\n".join(__import__("app.services.letter_trace", fromlist=["trace"]).trace(s, day))
        assert "entonnoir : Lettres : 1 valeur(s) lue(s)" in trace and phrase in trace
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        card = c.get("/opportunites").text
        letters = c.get("/lettres").text
    assert "Ce que dit la lettre" in card and "Ce que voit l'outil" in card and "data-letter-checklist" in card
    from markupsafe import escape

    assert str(escape(phrase)) in card  # the same sentence on the card…
    assert str(escape(phrase)) in letters  # …and in the « Préconisations » of the letters page
    assert "sorties non validées par la recherche" in card.lower()


def test_an_inactive_value_gets_a_non_analysable_proposal(db_engine, config):
    from app.db.models import Instrument

    with db_session() as s:
        _seed(s, breakout_bars())
        s.get(Instrument, ISIN).active = False
        it = _letter(s, {"achat": 98.0, "stop": 93.0, "objectif": 110.0})
        _, funnels = analyse_items(s, config, [it])
        (p,) = _letter_proposals(s)
        assert p.details["verdict"] == "non_analysable" and p.details["non_analysable"] == "inactive"
        assert p.to_verify and p.action == "WATCH" and p.stop == 93.0  # the letter's levels, quoted
        assert funnels[0]["non_analysable"] == {"inactive": 1} and funnels[0]["without_proposal"] == []
        assert sentence(p).startswith("Non analysable : valeur inactive")
        again = analyse_items(s, config, [it])[1]
        assert len(_letter_proposals(s)) == 1 and again[0]["proposals"] == 1  # one proposal per value and day
