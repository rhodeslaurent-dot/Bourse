"""Step 2, option A (17/11/2026, docs/42, ADR-020) — three authors of levels on the day's ideas, in observation:
masked dossier with Python's levels, analyst then contradictor, arbiter on the first ideas; Python's plausibility
check (a failing set is set aside, never corrected), quantity by Python (rule 7, the proposal's own inputs), an
arbiter's choice of a rejected set becomes « aucun », budget, idempotence, never applied; the measure per author."""

from __future__ import annotations

from datetime import UTC, date, timedelta
from pathlib import Path

from sqlalchemy import select

from app.db.models import AiIdeaReview, LlmCall, PriceEod
from app.db.session import db_session
from app.domain.risk import resize
from app.llm.gateway import LlmUsage
from app.llm.schemas import IdeaAnalystOut, IdeaArbiterOut, IdeaContradictorOut
from app.services import idea_ai
from tests.integration.test_engine import NOW
from tests.integration.test_proposals_ui import _letter_proposal


class Gw:
    enabled = True

    class settings:  # noqa: N801
        write_model = "claude-sonnet-5-5"
        prompts_dir = Path("app/llm/prompts")
        prices = {
            "claude-sonnet-5-5": {"input": 2.0, "output": 10.0},
            "claude-fable-5-2": {"input": 8.0, "output": 40.0},
        }

    def __init__(self, analyst: dict, contra: dict, arbiter: dict | None = None) -> None:
        self.out = {
            "idea_ai_analyst": IdeaAnalystOut.model_validate(analyst),
            "idea_ai_contradictor": IdeaContradictorOut.model_validate(contra),
            "idea_ai_arbiter": IdeaArbiterOut.model_validate(arbiter or {"choix": "python", "confiance": "faible"}),
        }
        self.users: list[str] = []
        self.models: list[str | None] = []

    def parse(self, s, purpose, prompt, user, schema, model=None, max_tokens=0):
        self.users.append(user)
        self.models.append(model)
        s.add(LlmCall(model=model, prompt_version="1", prompt_hash=purpose, purpose=purpose, cost_usd=0.01, ts=NOW))
        return self.out[purpose], LlmUsage(10, 10, 0.01, False)


def _idea(s):
    p = _letter_proposal(s)
    assert p.details.get("p_max") and p.action in ("BUY", "WATCH")
    return p


def _day(p) -> date:
    from app.domain.timeutil import PARIS

    return p.created_at.replace(tzinfo=p.created_at.tzinfo or UTC).astimezone(PARIS).date()


def test_levels_are_checked_sized_by_python_and_never_applied(db_engine, config):
    with db_session() as s:
        s.info["config"] = config
        p = _idea(s)
        day = _day(p)
        last = idea_ai.last_close(s, p.isin, day)
        assert last is not None
        good = {"entree": round(last, 2), "stop": round(last * 0.96, 2), "objectifs": [round(last * 1.1, 2)]}
        bad = {"entree": round(last, 2), "stop": round(last * 1.02, 2), "objectifs": [round(last * 1.1, 2)]}
        before = (p.action, p.shares, p.stop, dict(p.details or {}))
        gw = Gw(
            {"avis": "acheter", "niveaux": good, "motifs": [{"texte": "Le signal est récent.", "faits": ["F1"]}],
             "confiance": "moyenne"},
            {"objections": [{"texte": "La tendance reste à confirmer.", "faits": ["F1"]}], "avis_revu": "attendre",
             "niveaux_revus": bad, "confiance": "faible"},
            {"choix": "contradicteur", "raison": "Plus prudent.", "confiance": "faible"},
        )  # fmt: skip
        (row,) = idea_ai.run_day(s, config, gw, day, NOW)
        assert row.status == "ok" and row.rank == 1
        assert "TotalEnergies" not in gw.users[0] and p.isin not in gw.users[0]  # the company is masked
        assert "Niveaux de Python" in gw.users[0] and "<avis>" in gw.users[1] and "<propositions>" in gw.users[2]
        assert gw.models[2] == "claude-fable-5-2"  # ADR-020: the arbiter on the most capable model
        stored = p.details["comptes"][p.account_recommended]["entree_taille"]
        assert row.analyst["controle"] == "ok"
        assert row.analyst["titres"] == resize(stored, good["entree"], good["stop"]).shares  # Python sizes (rule 7)
        assert "au-dessus de l'entrée" in row.contradictor["controle"] and row.contradictor["titres"] is None
        assert row.contradictor["niveaux"] == bad  # set aside, never corrected
        assert row.chosen == "aucun" and any(r["element"] == "arbitre" for r in row.rejected)  # rejected choice
        assert row.python["titres"] == p.shares and row.python["avis"] == p.action
        assert (p.action, p.shares, p.stop, p.details) == before  # observation: nothing applied
        line = idea_ai.summary(row)
        assert "jamais appliqué" in line and "calculés par Python" in line and "retient aucun" in line
        again = idea_ai.run_day(s, config, gw, day, NOW)[0]
        assert again.id == row.id and len(gw.users) == 3  # idempotent: same dossier → no new call
        from app.services import daily_review

        rv = daily_review.build(s, config, day, NOW)
        cards = [i for k in ("fiches", "fiches_information") for i in rv[k] if i["proposal_id"] == p.id]
        assert cards and cards[0]["avis_ia"] == line  # shown on the card of the day, beside Python's levels


def test_the_plausibility_check_sets_aside_far_or_inverted_levels(config):
    st = idea_ai.settings(config)
    ok = {"entree": 100.0, "stop": 95.0, "objectifs": [110.0]}
    assert idea_ai.check_levels(ok, 101.0, st) is None
    assert "du dernier cours" in idea_ai.check_levels({**ok, "entree": 130.0, "objectifs": [140.0]}, 100.0, st)
    assert "sous l'entrée" in idea_ai.check_levels({**ok, "stop": 60.0}, 100.0, st)  # stop more than 25 % away
    assert "au-dessus de l'entrée" in idea_ai.check_levels({**ok, "stop": 101.0}, 100.0, st)
    assert idea_ai.check_levels({**ok, "objectifs": [99.0]}, 100.0, st) == "objectif sous l'entrée"
    assert idea_ai.check_levels({**ok, "objectifs": []}, 100.0, st) == "aucun objectif"
    assert "invérifiables" in idea_ai.check_levels(ok, None, st)
    assert idea_ai.check_levels({"entree": None, "stop": 95.0}, 100.0, st) == "entrée ou stop absent"


def test_the_review_respects_its_daily_budget_and_its_switch(db_engine, config):
    config.raw["idea_ai"]["daily_budget_usd"] = 0.001
    with db_session() as s:
        s.info["config"] = config
        p = _idea(s)
        gw = Gw({"avis": "attendre", "confiance": "faible"}, {"avis_revu": "attendre", "confiance": "faible"})
        (row,) = idea_ai.run_day(s, config, gw, _day(p), NOW)
        assert row.status == "budget" and gw.users == [] and not s.scalars(select(LlmCall)).first()
        assert "non rendu" in idea_ai.summary(row)


def test_the_measure_counts_only_finished_windows_per_author(db_engine, config):
    with db_session() as s:
        s.info["config"] = config
        p = _idea(s)
        day = _day(p)
        lv = {"entree": 100.0, "stop": 95.0, "objectifs": [104.0]}
        r = AiIdeaReview(proposal_id=p.id, day=day, isin=p.isin, rank=1, status="ok", created_at=NOW, dossier_hash="h",
                         python={"niveaux": lv, "controle": "ok"},
                         analyst={"niveaux": {**lv, "stop": 99.0}, "controle": "ok"},
                         contradictor={"niveaux": lv, "controle": "stop au-dessus"}, chosen="analyste")  # fmt: skip
        s.add(r)
        s.flush()
        assert all(m.n == 0 for m in idea_ai.measure_authors(s, day + timedelta(days=1)))  # window not over
        d, k = day + timedelta(days=1), 0
        while k < 30:
            if d.weekday() < 5:
                o = 100.0 if k == 0 else 101.5
                s.merge(PriceEod(isin=p.isin, day=d, open=o, high=o * 1.03 if k == 2 else o * 1.005, low=o * 0.997,
                                 close=o, adj_close=o, volume=1000, source="eodhd", received_at=NOW))  # fmt: skip
                k += 1
            d += timedelta(days=1)
        s.flush()
        by = {m.author: m for m in idea_ai.measure_authors(s, d + timedelta(days=1))}
        assert by["python"].n == 1 and by["python"].filled == 1 and by["python"].mean_r > 0  # first target reached
        assert by["contradicteur"].n == 0  # a set aside is never measured
        assert by["arbitre"].n == 1 and by["arbitre"].mean_r == by["analyste"].mean_r  # the arbiter's pick
