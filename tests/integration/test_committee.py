"""docs/24 v2 — committee in observation mode: pseudonymised dossier with base rates, two calls, guards,
quorum, bounded effect never applied, budget pre-reservation, idempotence, measure."""

from __future__ import annotations

from pathlib import Path

from app.db.models import CommitteeRun, LlmCall, Proposal, SignalOutcome
from app.db.session import db_session
from app.llm.gateway import LlmUsage
from app.llm.schemas import ChairOut, PreMortemOut
from app.services.committee import bounded_effect, deliberate, measure, settings
from tests.integration.test_engine import NOW
from tests.integration.test_proposals_ui import _letter_proposal


class Gw:
    enabled = True

    class settings:  # noqa: N801
        write_model = "claude-sonnet-4-6"
        prompts_dir = Path("app/llm/prompts")
        prices = {"claude-sonnet-4-6": {"input": 3.0, "output": 15.0}}

    def __init__(self, premortem: dict, chair: dict) -> None:
        self.out = {"committee_premortem": PreMortemOut.model_validate(premortem),
                    "committee_chair": ChairOut.model_validate(chair)}  # fmt: skip
        self.users: list[str] = []

    def parse(self, s, purpose, prompt, user, schema, model=None, max_tokens=0):
        self.users.append(user)
        s.add(LlmCall(model=model, prompt_version="1", prompt_hash=purpose, purpose=purpose, cost_usd=0.02, ts=NOW))
        return self.out[purpose], LlmUsage(10, 10, 0.02, False)


PM = {"causes": [
    {"texte": "Le stop à 93 est proche d'un support fragile.", "faits": ["F4"], "vraisemblance": "moyenne",
     "gravite": "moyenne"},
    {"texte": "Repli de 37 % inventé.", "faits": ["F1"], "vraisemblance": "forte", "gravite": "forte"},
]}  # fmt: skip
CHAIR = {"action_comite": "reduire_un_cran", "confiance_comite": "moyenne",
         "motifs": [{"texte": "Le stop à 93 laisse peu de marge.", "faits": ["F4"]}],
         "dissidence_retenue_ou_ecartee": "Cause 1 retenue."}  # fmt: skip


def test_deliberation_is_masked_guarded_bounded_and_never_applied(db_engine, config):
    with db_session() as s:
        s.info["config"] = config
        p = _letter_proposal(s)
        before = (p.action, p.shares, dict(p.details or {}))
        gw = Gw(PM, CHAIR)
        run = deliberate(s, config, gw, p, NOW)
        assert run.status == "ok" and run.action_comite == "reduire_un_cran" and run.mode == "observation"
        assert "TotalEnergies" not in gw.users[0] and "FR0000120271" not in gw.users[0]  # issuer masked
        assert "Taux de base" in gw.users[0] and "<premortem>" in gw.users[1]
        assert [c["texte"][:6] for c in run.premortem["causes"]] == ["Le sto"]  # invented 37 % rejected
        assert any("37" in r["element"] for r in run.rejected)
        assert run.effect["applique"] is False and run.effect["titres"] <= int(p.shares or 0)
        assert (p.action, p.shares, p.details) == before  # observation: the proposal is untouched
        assert deliberate(s, config, gw, p, NOW).id == run.id and len(gw.users) == 2  # idempotent


def test_no_valid_cause_means_no_quorum(db_engine, config):
    with db_session() as s:
        s.info["config"] = config
        p = _letter_proposal(s)
        pm = {"causes": [{"texte": "Sans fait.", "faits": ["F999"], "vraisemblance": "forte", "gravite": "forte"}]}
        gw = Gw(pm, CHAIR)
        run = deliberate(s, config, gw, p, NOW)
        assert run.status == "quorum" and run.action_comite is None and len(gw.users) == 1


def test_budget_is_reserved_before_any_call(db_engine, config, monkeypatch):
    with db_session() as s:
        s.info["config"] = config
        p = _letter_proposal(s)
        s.add(LlmCall(model="m", prompt_version="1", prompt_hash="x", purpose="committee_chair", cost_usd=0.49, ts=NOW))
        s.flush()
        gw = Gw(PM, CHAIR)
        run = deliberate(s, config, gw, p, NOW)
        assert run.status == "budget" and gw.users == []


def test_effects_are_never_less_cautious_than_the_engine(config):
    st = settings(config)
    p = Proposal(action="BUY", shares=10, isin="X", signal_id=1)
    assert bounded_effect(p, "maintenir", st)["titres"] == 10
    assert bounded_effect(p, "reduire_un_cran", st)["titres"] == 7
    assert bounded_effect(p, "reduire_deux_crans", st)["titres"] == 5
    assert bounded_effect(p, "attendre_repli", st)["action"] == "WATCH"
    assert bounded_effect(p, "rejeter", st) == {"action": "NO_TRADE", "titres": 0}
    w = Proposal(action="WATCH", shares=10, isin="X", signal_id=1)
    assert bounded_effect(w, "attendre_evenement", st)["action"] == "WATCH"
    assert st.mode == "observation"


def test_measure_groups_final_outcomes_by_verdict(db_engine, config):
    from datetime import UTC, datetime

    with db_session() as s:
        s.info["config"] = config
        p = _letter_proposal(s)
        run = deliberate(s, config, Gw(PM, CHAIR), p, NOW)
        s.add(SignalOutcome(proposal_id=p.id, isin=p.isin, computed_at=datetime.now(UTC), status="stopped",
                            final=True, decision_group="sans_decision", r_net=-1.0))  # fmt: skip
        s.flush()
        m = measure(s)
        assert m["reduire_un_cran"]["n"] == 1 and m["reduire_un_cran:moyenne"]["mean_r"] == -1.0
        assert s.query(CommitteeRun).count() == 1 and run.status == "ok"


def test_the_card_shows_the_observation_verdict(db_engine, config, calendars):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with db_session() as s:
        s.info["config"] = config
        p = _letter_proposal(s)
        deliberate(s, config, Gw(PM, CHAIR), p, NOW)
        day = p.created_at.date().isoformat()
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        html = c.get(f"/opportunites?day={day}").text + c.get("/opportunites?day=2026-10-22").text
    assert "Comité d&#39;investissement — en observation" in html or "Comité d'investissement — en observation" in html
    assert "Réduire d&#39;un cran" in html or "Réduire d'un cran" in html
    assert "jamais appliqué" in html


def test_home_lists_the_day_ideas_with_the_committee_verdict(db_engine, config, calendars):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with db_session() as s:
        s.info["config"] = config
        p = _letter_proposal(s)
        p.action, p.rank, p.gates = "BUY", 1, {"passed": [], "blocked": []}  # an admitted BUY of the day
        deliberate(s, config, Gw(PM, CHAIR), p, NOW)
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        html = c.get("/").text
    i = html.find("Idées du jour")
    block = html[i : i + 2500]
    assert i > 0 and "#1 TotalEnergies" in block and "reduire un cran" in block
