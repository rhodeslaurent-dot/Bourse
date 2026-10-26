"""Lot 2.4 — ranked opportunities page with the full card, order book, thesis with guards,
override only when eligible, Telegram top-N with decision buttons."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.db.models import Decision, NewsletterItem, NewsletterValue, Proposal
from app.db.session import db_session
from app.llm.schemas import ThesisOut
from app.services import engine
from app.services.thesis import facts_for, write_thesis
from tests.integration.test_engine import DAY, ISIN, NOW, _seed, breakout_bars


def _letter_proposal(s) -> Proposal:
    _seed(s, breakout_bars()[:-1])
    it = NewsletterItem(source="momentum_capital", message_id="m1", dedup_hash="h1",
                        received_at=NOW - timedelta(hours=20), subject="Momentum", body_text="x")  # fmt: skip
    s.add(it)
    s.flush()
    s.add(NewsletterValue(item_id=it.id, isin=ISIN, name="TotalEnergies", direction="buy",
                          levels={"achat": 98.0, "stop": 93.0, "objectif": 110.0}))  # fmt: skip
    s.flush()
    engine.scan(s, s.info.get("config"), DAY, now=NOW) if s.info.get("config") else None
    return s.query(Proposal).filter(Proposal.isin == ISIN).one()


class FakeGateway:
    enabled = True

    class settings:  # noqa: N801
        write_model = "claude-sonnet-4-6"

    def __init__(self, out: ThesisOut) -> None:
        self.out = out

    def parse(self, s, purpose, prompt, user, schema, model=None, max_tokens=0):
        from app.llm.gateway import LlmUsage

        return self.out, LlmUsage(10, 10, 0.01, False)


def test_thesis_guards_and_page(db_engine, config, calendars):
    with db_session() as s:
        s.info["config"] = config
        p = _letter_proposal(s)
        facts = facts_for(s, p, NOW)
        ids = [f["id"] for f in facts]
        assert any("110" in f["texte"] for f in facts) and any("Compte conseillé" in f["texte"] for f in facts)
        out = ThesisOut.model_validate(
            {
                "arguments": [
                    {"texte": "Objectif publié 110 au-dessus de la limite.", "faits": [ids[3]], "nature": "fait"},
                    {"texte": "Potentiel de 37 % inventé.", "faits": [ids[0]], "nature": "analyse"},
                    {"texte": "Argument sans fait.", "faits": ["F999"], "nature": "opinion"},
                ],
                "risques": ["Annonce macro proche."],
                "pourquoi_maintenant": "Lettre reçue la veille.",
                "ce_qui_invaliderait": "Clôture sous 93.",
            }
        )
        th = write_thesis(s, FakeGateway(out), p, NOW)
        assert [a["texte"] for a in th["arguments"]] == ["Objectif publié 110 au-dessus de la limite."]
        motifs = " ".join(r["motif"] for r in th["ecartes"])
        assert "chiffres absents" in motifs and "sans fait" in motifs
    from app.main import create_app

    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        html = c.get(f"/opportunites?day={datetime.now(UTC).date().isoformat()}").text
        html_day = c.get("/opportunites?day=2026-10-22").text
    page = html + html_day
    assert "Thèse d'investissement" in page and "Gain et risque nets, par compte" in page
    assert "expire le " in page  # date shown, not only the time (22/10/2026)
    assert "refermer le détail" in page and "Objectif 1" in page and "brut" in page
    # the order book only lists BUY and limits waiting for a pullback (tests/unit/test_pea_listing_and_order_book.py)
    assert (
        "#1" in page or "rang brut 1" in page
    ) and "Objectif publié 110" in page  # rank kept, greyed if not actionable


def test_override_refused_unless_eligible_and_telegram_buttons(db_engine, config, calendars):
    from app.notify.telegram import ModeStore, handle_callback, proposal_buttons

    with db_session() as s:
        s.info["config"] = config
        p = _letter_proposal(s)
        pid = p.id
    from app.main import create_app

    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        r = c.post("/opportunites/derogation", data={"proposal_id": pid, "reason": "conviction forte"})
        assert r.status_code == 400  # not marked eligible by the engine (R21)
        with db_session() as s:
            q = s.get(Proposal, pid)
            q.details = {**(q.details or {}), "derogation": {"proposable": True, "manque": []}}
        r = c.post(
            "/opportunites/derogation", data={"proposal_id": pid, "reason": "conviction forte"}, follow_redirects=False
        )
        assert r.status_code == 303
    with db_session() as s:
        assert s.query(Decision).filter(Decision.proposal_id == pid, Decision.decision == "override").count() == 1
    seen: list = []
    store = ModeStore(lambda *a: None, lambda: "disponible", lambda *a: None, decide=lambda i, d: seen.append((i, d)))
    btn = proposal_buttons(pid)[2]
    assert handle_callback(f"proposal_buy:{btn.data}", 7, 7, store).text.startswith("Enregistré")
    assert seen == [(pid, "order_entered")]


def test_notify_top_only_unblocked_buy(db_engine, config):
    class Alerts:
        def __init__(self):
            self.events = []

        def emit(self, s, ev, mode, intraday=True):
            self.events.append(ev)
            return True

    with db_session() as s:
        s.info["config"] = config
        p = _letter_proposal(s)
        p.action, p.gates, p.rank = "BUY", {"passed": [], "blocked": []}, 1
        p.created_at = datetime.now(UTC)
        alerts = Alerts()
        assert engine.notify_top(s, config, alerts, "disponible", datetime.now(UTC).date()) == 1
        ev = alerts.events[0]
        assert ev.title.startswith("#1") and len(ev.lines) <= 11 and ev.buttons[0].data == f"p{p.id}:vu"
        assert engine.notify_top(s, config, alerts, "disponible", datetime.now(UTC).date()) == 0  # sent once
