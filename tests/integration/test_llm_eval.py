"""ADR-011 — the evaluation replays stored news with the new model (cache bypassed), reports the agreement
per field and stops at the cost cap."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.db.models import LlmCache, NewsItem
from app.db.session import db_session
from app.llm.gateway import ClaudeGateway, LlmSettings
from app.llm.schemas import NewsClassification
from app.services.llm_eval import run_eval

SETTINGS = LlmSettings("claude-haiku-4-5", "claude-sonnet-4-6", 0, 50, 5,
                       {"claude-haiku-5-5": {"input": 0.1, "output": 0.5},
                        "claude-sonnet-5-5": {"input": 2, "output": 10}},
                       Path("app/llm/prompts"), True,
                       {"claude-haiku-5-5": {"sampling": False, "thinking": {"type": "disabled"}}})  # fmt: skip


def test_eval_compares_fields_bypasses_the_cache_and_caps_the_cost(db_engine):
    seen = []

    def call(model, system, user, schema, temperature, max_tokens, options=None):
        seen.append((model, options))
        direction = "negatif" if "baisse" in user else "positif"
        out = NewsClassification(type="resultats", direction=direction, magnitude="moyenne", ampleur_annoncee=0.5,
                                 resume="r", confiance=0.8, portee="valeur")  # fmt: skip
        return out, 20_000, 1_000  # 0.0025 $ per call

    with db_session() as s:
        for k, title in enumerate(["Résultats en hausse", "Résultats en baisse", "Contrat signé"]):
            s.add(NewsItem(source="x", guid=str(k), dedup_hash=str(k), fetched_at=datetime.now(UTC), title=title,
                           classified_by="llm", classification={"type": "resultats", "direction": "positif",
                                                                "magnitude": "moyenne", "portee": "valeur"}))  # fmt: skip
        s.flush()
        rep = run_eval(s, ClaudeGateway(SETTINGS, call=call), "claude-haiku-5-5", "claude-sonnet-5-5", 10, 0, 1.0)
        assert rep.news == 3 and rep.agree["type"] == 3 and rep.agree["direction"] == 2
        assert rep.disagreements[0]["direction"] == ("positif", "negatif")
        assert all(o == {"sampling": False, "thinking": {"type": "disabled"}} for _, o in seen)
        assert s.query(LlmCache).count() == 0  # never stored as a cached answer
        gw = ClaudeGateway(SETTINGS, call=call)
        capped = run_eval(s, gw, "claude-haiku-5-5", "claude-sonnet-5-5", 10, 0, 0.004)
        assert capped.news == 2 and "plafond" in capped.stopped
