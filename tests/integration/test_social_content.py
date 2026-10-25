"""docs/30 S4a — a read of a value's forum threads (mocked) labelled by a fake model: aggregates stored without
any pseudonym, daily cost cap, « structure » when the classes are not set, and the engine effects (a pump
blocks the entry; strong chatter alone never makes a proposal)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.config import validate_params
from app.db.models import Instrument, LlmCache, Proposal, SocialContent
from app.db.session import db_session
from app.llm.gateway import ClaudeGateway, LlmSettings
from app.services import engine
from app.services.social_content import analyse, facts
from tests.integration.test_engine import DAY, ISIN, NOW, _seed, breakout_bars

ROOT = Path(__file__).resolve().parents[2]
T = datetime(2026, 10, 25, 16, 0, tzinfo=UTC)  # 17:00 in Paris
PSEUDOS = ["Jeannot75", "Marie_Bourse", "Pierrot", "Lulu", "Zorro", "Spammeur"]
SETTINGS = LlmSettings("m", "w", 0, 50, 5, {"m": {"input": 0.1, "output": 0.5}}, ROOT / "app/llm/prompts", True)


def _cfg(raw: dict, **content):  # type: ignore[no-untyped-def]
    bp = raw["weak_signals"]["social"]["boursorama"]
    bp.update(thread_link_regex=r"/detail/\d+", message_class="msg-row", author_class="pseudo",
              text_class="msg-body", message_date_class="when", interval_s=0)  # fmt: skip
    raw["weak_signals"]["content"].update(content)
    return validate_params(raw, path=ROOT / "config" / "params.example.yaml", sha256="test")


def _page(rows: list[tuple[str, str]]) -> str:
    return "".join(
        f'<div class="msg-row"><span class="pseudo">{a}</span><span class="when">25/10/2026 15:{k:02d}</span>'
        f'<div class="msg-body">{t}</div></div>'
        for k, (a, t) in enumerate(rows)
    )


def _client(rows: list[tuple[str, str]]) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        if "/detail/" in req.url.path:
            return httpx.Response(200, text=_page(rows))
        return httpx.Response(200, text='<a href="/bourse/forum/1rPTTE/detail/77/">fil</a>')

    return httpx.Client(transport=httpx.MockTransport(handler))


SENT: list[str] = []


def _fake(model, system, user, schema, temperature, max_tokens, **kw):  # type: ignore[no-untyped-def]
    SENT.append(user)
    out = []
    for i, text in re.findall(r"\[(\d+)\] (.+)", user):
        if "exploser" in text:
            lab = ("sentiment", "promotion", "positif", "vague", "Un membre annonce une envolée")
        else:
            lab = ("contrat", "rumeur", "positif", "precise", "Un membre évoque un contrat de 900 millions")
        out.append({"i": int(i), "theme": lab[0], "nature": lab[1], "direction": lab[2], "precision": lab[3],
                    "source_citee": "aucune", "resume": lab[4]})  # fmt: skip
    return schema.model_validate({"messages": out}), 1000, 300


def _inst(s, small: bool = False) -> Instrument:  # type: ignore[no-untyped-def]
    inst = Instrument(isin="FR0000120271", name="TotalEnergies", mic="XPAR", currency="EUR", ticker_local="TTE",
                      active=True, small_cap=small)  # fmt: skip
    s.add(inst)
    s.flush()
    return inst


def test_strong_chatter_is_stored_without_any_pseudonym(db_engine, raw):
    """A30.1, A30.3 — five accounts on a precise contract: « fort », summaries without invented numbers."""
    cfg = _cfg(raw)
    rows = [(a, f"Contrat signé avec un client, merci {PSEUDOS[(k + 1) % 5]} et @{PSEUDOS[k]}") for k, a in
            enumerate(PSEUDOS[:5])]  # fmt: skip
    SENT.clear()
    with db_session() as s, _client(rows) as c:
        row = analyse(s, cfg, c, _inst(s), "demande", ClaudeGateway(SETTINGS, call=_fake), now=T)
        assert row is not None and row.status == "ok" and row.effect == "renforce"
        d = row.data
        assert (d["messages"], d["auteurs_distincts"], d["theme_dominant"], d["solidite"]) == (5, 5, "contrat", "fort")
        assert row.threads == ["https://www.boursorama.com/bourse/forum/1rPTTE/detail/77/"]
        assert row.summaries and all("900" not in x for x in row.summaries)  # not in any message: removed
        stored = json.dumps([row.data, row.summaries, row.threads, row.note], ensure_ascii=False)
        stored += json.dumps([c.response for c in s.query(LlmCache)], ensure_ascii=False)
        for p in PSEUDOS[:5]:
            assert p.lower() not in stored.lower() and all(p.lower() not in u.lower() for u in SENT)
        assert any("thème dominant : contrat, 5 auteur(s)" in f for f in facts(row))


def test_one_account_pumping_a_small_cap_and_the_cost_cap(db_engine, raw):
    """A30.5, A30.8 — counts and concentration need no model: beyond the daily cap they still block a pump."""
    cfg = _cfg(raw, max_usd_per_day=0.0)
    rows = [("Spammeur", f"ça va exploser lundi, message {k}") for k in range(6)]
    with db_session() as s, _client(rows) as c:
        row = analyse(s, cfg, c, _inst(s, small=True), "radar", ClaudeGateway(SETTINGS, call=_fake), now=T)
        assert row.status == "plafond" and "plafond" in row.note and row.cost_usd == 0
        assert row.data["messages"] == 6 and row.data["concentration"] == 1.0
        assert row.effect == "emballement"  # one account, small cap: blocked without any classification
    cfg = _cfg(raw, max_usd_per_day=0.30)
    with db_session() as s, _client(rows) as c:
        inst = s.get(Instrument, "FR0000120271")
        row = analyse(s, cfg, c, inst, "radar", ClaudeGateway(SETTINGS, call=_fake), now=T)
        assert row.status == "ok" and row.effect == "emballement" and row.data["solidite"] == "bruit"


def test_without_the_page_classes_nothing_is_guessed(db_engine, config, monkeypatch):
    monkeypatch.setitem(config.raw["weak_signals"]["social"]["boursorama"], "message_class", None)
    with db_session() as s, _client([]) as c:
        row = analyse(s, config, c, _inst(s), "demande", None, now=T)
        assert row.status == "structure" and "social-check --threads" in row.note
        assert facts(row) == [f"échanges du forum : indisponibles ({row.note})"]


def test_engine_blocks_a_content_pump_and_content_alone_never_proposes(db_engine, config):
    """A30.5, A30.9"""
    with db_session() as s:
        _seed(s, breakout_bars())
        s.add(Instrument(isin="FR0000000002", name="Calme", mic="XPAR", currency="EUR", in_universe=True, active=True))
        for isin, eff in ((ISIN, "emballement"), ("FR0000000002", "renforce")):
            s.add(SocialContent(isin=isin, computed_at=NOW, source="forum_boursorama", reason="radar", status="ok",
                                window_hours=24, data={"messages": 9, "solidite": "fort"}, effect=eff))  # fmt: skip
        s.flush()
        engine.scan(s, config, DAY, now=NOW)
        props = s.query(Proposal).all()
        assert props and {p.isin for p in props} == {ISIN}
        for p in props:
            assert any(b.startswith("BLOQUÉ : emballement — échanges") for b in (p.gates or {}).get("blocked", []))
            assert (p.details or {})["echanges_forum"]["effet"] == "emballement"
