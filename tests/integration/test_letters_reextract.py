"""30/10/2026: the letters history was read with the extraction prompt v1 (no levels, names unresolved); the stored
letters are read again with the current prompt (fake reader here: no real LLM call), conservatively."""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import Instrument, LlmCall, NewsletterItem, NewsletterValue, PriceEod, Proposal
from app.db.session import db_session
from app.llm.gateway import ClaudeGateway, LlmSettings, LlmUnavailable
from app.llm.schemas import NewsletterExtraction
from app.services import letters_reextract as rx
from app.services.decisions import create_proposal, record_signal

T1 = datetime(2026, 3, 10, 7, 30, tzinfo=UTC)
T2 = datetime(2026, 3, 11, 7, 30, tzinfo=UTC)
T3 = datetime(2026, 10, 29, 7, 30, tzinfo=UTC)
BODY1 = (
    "Viridien : sortie par le haut au-dessus de 100,20, objectif 109,10.\n\n"
    "Sopra Steria : achat vers 170, stop 154,90, objectif 195,70.\n\n"
    "Atos à surveiller, support 1,20."
)
BODY2 = "BioMérieux : poursuite de la hausse, objectif 140."
READS = {
    "Viridien": {"name": "Viridien", "direction": "buy",
                 "levels": {"declencheur": 100.2, "objectif": 109.1, "stop": 77.0}},  # 77 is not in the letter
    "Sopra": {"name": "Sopra Steria", "direction": "buy",
              "levels": {"entrée": 170.0, "stop": 154.9, "objectif": 195.7, "zone": 170.0}},  # « zone »: unknown name
    "Atos": {"name": "Atos", "direction": "buy", "levels": {"support": 1.2}},
    "BioM": {"name": "BioMérieux", "direction": "buy", "levels": {"objectif": 140.0}},
}  # fmt: skip


class Reader:
    """The gateway's SDK call replaced: six positional arguments, (parsed, input tokens, output tokens)."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls = 0
        self.fail_on = fail_on

    def __call__(self, model, system, user, schema, temperature, max_tokens):
        self.calls += 1
        assert schema is NewsletterExtraction and max_tokens == 2048 and "levels" in system
        if self.fail_on and self.fail_on in user:
            raise LlmUnavailable("réponse tronquée (max_tokens = 2048 atteint) : augmenter la limite dans params.yaml")
        if "Viridien" in user:
            values = [READS["Viridien"], READS["Sopra"], READS["Atos"]]
        else:
            values = [READS["BioM"]]
        return NewsletterExtraction(values=values), 3000, 400


def gateway(reader: Reader, daily: float = 5.0) -> ClaudeGateway:
    st = LlmSettings("m", "m", 0, 15, daily, {"m": {"input": 1.0, "output": 5.0}}, Path("app/llm/prompts"), True)
    return ClaudeGateway(st, call=reader)


@pytest.fixture
def letters(db_engine):
    with db_session() as s:
        s.add_all([
            Instrument(isin="FR001400PVN6", name="Viridien S.A.", mic="XPAR"),
            Instrument(isin="FR0000050809", name="Sopra Steria Group", mic="XPAR"),
            Instrument(isin="FR0000051732", name="Atos SE", mic="XPAR"),
            Instrument(isin="FR0013280286", name="BioMérieux", mic="XPAR"),
            PriceEod(isin="FR0000050809", day=date(2026, 3, 9), open=160, high=166, low=159, close=165.0, volume=1,
                     source="eodhd", received_at=T1),
            PriceEod(isin="FR0000050809", day=date(2026, 3, 10), open=166.0, high=171, low=165, close=170.0, volume=1,
                     source="eodhd", received_at=T1),
        ])  # fmt: skip
        a = NewsletterItem(source="momentum_capital", message_id="a", dedup_hash="ha", received_at=T1,
                           subject="Trois idées", body_text=BODY1, parsed={"values": [], "rejected_llm_levels": []},
                           parsed_by="llm", llm_prompt_version="1")  # fmt: skip
        b = NewsletterItem(source="momentum_capital", message_id="b", dedup_hash="hb", received_at=T2,
                           subject="BioMérieux", body_text=BODY2, parsed_by="rules")  # fmt: skip
        c = NewsletterItem(source="momentum_capital", message_id="c", dedup_hash="hc", received_at=T3,
                           subject="Déjà v2", body_text="Lue à la réception avec la consigne actuelle.",
                           parsed_by="llm", llm_prompt_version="2")  # fmt: skip
        z = NewsletterItem(source="zonebourse", message_id="z", dedup_hash="hz", received_at=T2,
                           subject="Autre source", body_text="Rien.")  # fmt: skip
        s.add_all([a, b, c, z])
        s.flush()
        atos = record_signal(s, "FR0000051732", "external", "momentum_capital", T1, "eod", "unavailable", "t",
                             evidence={"direction": "watch", "levels": {}})  # fmt: skip
        create_proposal(s, atos, "WATCH", None, {"blocked": []}, "t", to_verify=True, created_at=T1)
        biom = record_signal(s, "FR0013280286", "external", "momentum_capital", T2, "eod", "unavailable", "t",
                             evidence={"direction": "buy", "levels": {"objectif": 135.0}})  # fmt: skip
        create_proposal(s, biom, "WATCH", None, {"blocked": []}, "t", to_verify=True, created_at=T2)
        s.add_all([
            NewsletterValue(item_id=a.id, isin=None, name="Viridien", direction="neutral", levels=None),
            NewsletterValue(item_id=a.id, isin="FR0000051732", name="Atos", direction="watch", levels={},
                            p_open=1.1, signal_id=atos.id),
            NewsletterValue(item_id=b.id, isin="FR0013280286", name="BioMérieux", direction="buy",
                            levels={"objectif": 135.0}, signal_id=biom.id),
        ])  # fmt: skip
        ids = {"a": a.id, "b": b.id, "c": c.id, "z": z.id}
    return ids


def _items(sources=("momentum_capital",)):
    with db_session() as s:
        return [it.id for it in rx.select_items(s, date(2026, 3, 1), date(2026, 10, 30), list(sources))]


def test_the_dry_run_estimates_the_cost_and_calls_nothing(letters, monkeypatch, capsys):
    import app.llm.gateway as gw
    from app.cli import cmd_letters_reextract

    def no_call(*a, **k):
        raise AssertionError("aucun appel à l'IA en aperçu")

    monkeypatch.setattr(gw, "_sdk_parse", no_call)
    args = argparse.Namespace(params="config/params.example.yaml", calendars="config/market_calendars.yaml",
                              from_date="2026-03-01", to="2026-10-30", source=["momentum_capital"], apply=False,
                              max_cost_usd=15.0, retry_failed=False)  # fmt: skip
    assert cmd_letters_reextract(args) == 0
    out = capsys.readouterr().out
    assert "3 lettre(s) dans la période · à relire : 2" in out
    assert "lues avec la consigne v2 dès la réception : 1" in out
    assert "coût estimé : ≈ " in out and "llm.prices_usd_per_mtok" in out and "aucun appel à l'IA" in out
    assert "Viridien :" not in out  # the letter's text is never printed
    with db_session() as s:
        assert s.scalar(select(LlmCall.id)) is None
        assert all("reextract" not in (it.parsed or {}) for it in s.scalars(select(NewsletterItem)))


def test_the_plan_counts_and_prices_the_letters_to_read(letters):
    st = gateway(Reader()).settings
    with db_session() as s:
        p = rx.plan(s, rx.select_items(s, date(2026, 3, 1), date(2026, 10, 30)), st)
    assert p.letters == 4 and p.todo_ids == [letters["a"], letters["b"], letters["z"]]  # oldest first
    assert p.states == {"todo": 3, "current": 1}
    assert p.chars_read == len(BODY1) + len(BODY2) + len("Rien.") and p.version == "2"
    assert p.tokens_out_per_letter == 2048 and "aucune extraction payée" in p.out_basis
    assert p.cost is not None and p.cost_max is not None and 0 < p.cost <= p.cost_max


def test_apply_fills_levels_and_isins_and_never_rewrites_a_linked_row(letters):
    reader = Reader()
    rep = rx.run(db_session, _items(), gateway(reader), 15.0)
    assert reader.calls == 2 and rep.processed == 2 and rep.skipped == 1 and rep.stopped is None
    with db_session() as s:
        rows = {v.name: v for v in s.scalars(select(NewsletterValue))}
        vir = rows["Viridien"]  # unlinked: ISIN resolved, levels written in the letter, direction set
        assert vir.isin == "FR001400PVN6" and vir.direction == "buy"
        assert vir.levels == {"declencheur": 100.2, "objectif": 109.1}  # stop 77 is not in the letter: rejected
        sopra = rows["Sopra Steria"]  # inserted as the reception stores it
        assert sopra.isin == "FR0000050809" and sopra.signal_id is None and sopra.direction == "buy"
        assert sopra.levels == {"achat": 170.0, "stop": 154.9, "objectif": 195.7}
        assert sopra.p_open == 166.0 and sopra.close_prev == 165.0 and sopra.gap_open == pytest.approx(166 / 165 - 1)
        atos = rows["Atos"]  # linked to a proposal: empty levels filled, nothing else changed
        assert atos.levels == {"support": 1.2} and atos.direction == "watch" and atos.p_open == 1.1
        biom = rows["BioMérieux"]  # linked, with levels: left as it was
        assert biom.levels == {"objectif": 135.0} and biom.direction == "buy"
        a = s.get(NewsletterItem, letters["a"])
        assert a.parsed["rejected_llm_levels"] == ["Viridien:stop=77.0"]
        mark = a.parsed["reextract"]
        assert mark["version"] == "2" and mark["status"] == "ok" and mark["values"] == 3 and mark["with_levels"] == 3
        assert mark["unknown_level_names"] == ["Sopra Steria:zone"] and mark["inserted"] == 1
        assert a.parsed_by == "llm" and a.llm_prompt_version == "1"  # the reading at the reception stays recorded
        assert len(list(s.scalars(select(Proposal)))) == 2  # no proposal created for a past letter
    assert rep.values == 4 and rep.with_levels == 4 and rep.inserted == 1 and rep.isin_filled == 1
    assert rep.kept_linked == 2 and rep.updated == 2 and rep.rejected == ["Viridien:stop=77.0"]
    assert rep.cost == pytest.approx(2 * (3000 * 1.0 + 400 * 5.0) / 1e6)
    text = "\n".join(rep.lines())
    assert "ISIN nouvellement résolus : 2" in text and "Viridien → FR001400PVN6" in text and "ARRÊT" not in text


def test_a_second_run_skips_the_letters_already_read(letters):
    reader = Reader()
    rx.run(db_session, _items(), gateway(reader), 15.0)
    with db_session() as s:
        before = [(v.id, v.isin, v.direction, v.levels) for v in s.scalars(select(NewsletterValue))]
    again = rx.run(db_session, _items(), gateway(reader), 15.0)
    assert reader.calls == 2 and again.processed == 0 and again.skipped == 3 and again.cost == 0
    with db_session() as s:
        assert [(v.id, v.isin, v.direction, v.levels) for v in s.scalars(select(NewsletterValue))] == before
        p = rx.plan(s, rx.select_items(s, date(2026, 3, 1), date(2026, 10, 30), ["momentum_capital"]),
                    gateway(reader).settings)  # fmt: skip
    assert p.todo_ids == [] and p.states == {"done": 2, "current": 1}
    assert p.tokens_out_per_letter == 400 and "2 extraction(s) payée(s)" in p.out_basis


def test_the_run_stops_before_the_command_cap_and_says_where_to_resume(letters):
    reader = Reader()
    rep = rx.run(db_session, _items(), gateway(reader), 0.001)  # one letter's upper estimate is ≈ 0.011 $
    assert reader.calls == 0 and rep.processed == 0 and rep.remaining == 2
    assert rep.stopped and "--max-cost-usd 0.00" in rep.stopped
    text = "\n".join(rep.lines())
    assert "ARRÊT" in text and "2 lettre(s) restante(s) ; prochaine lettre reçue le 10/03/2026" in text
    with db_session() as s:
        assert all("reextract" not in (it.parsed or {}) for it in s.scalars(select(NewsletterItem)))


def test_the_run_stops_at_the_daily_llm_cap(letters):
    with db_session() as s:
        s.add(LlmCall(ts=datetime.now(UTC), model="m", prompt_version="1", prompt_hash="x", cost_usd=5.5,
                      cached=False, purpose="news_classify"))  # fmt: skip
    reader = Reader()
    rep = rx.run(db_session, _items(), gateway(reader, daily=5.0), 15.0)
    assert reader.calls == 0 and rep.processed == 0 and rep.remaining == 2
    assert rep.stopped and "plafond journalier LLM atteint" in rep.stopped


def test_a_letter_whose_answer_fails_is_marked_and_the_run_goes_on(letters):
    reader = Reader(fail_on="Viridien")
    rep = rx.run(db_session, _items(), gateway(reader), 15.0)
    assert rep.processed == 1 and len(rep.failed) == 1 and "tronquée" in rep.failed[0] and rep.stopped is None
    with db_session() as s:
        assert s.get(NewsletterItem, letters["a"]).parsed["reextract"]["status"] == "echec"
        assert s.scalar(select(NewsletterValue).where(NewsletterValue.name == "Viridien")).isin is None
    again = rx.run(db_session, _items(), gateway(reader), 15.0)
    assert again.processed == 0 and again.skipped == 3
    retried = rx.run(db_session, _items(), gateway(Reader()), 15.0, retry_failed=True)
    assert retried.processed == 1


def test_the_trace_says_a_letter_was_read_again(letters):
    from app.services.letter_trace import trace

    rx.run(db_session, _items(), gateway(Reader()), 15.0)
    with db_session() as s:
        text = "\n".join(trace(s, date(2026, 3, 10)))
    assert "avec la consigne v2 : 3 valeur(s), 3 avec niveaux" in text
    assert "Viridien (FR001400PVN6) · buy · declencheur 100.2, objectif 109.1" in text
