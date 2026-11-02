"""Lot R3 (docs/38 §38.6) — a fiche written over several paragraphs is read at the reception without the LLM (cues,
nearest cue, list of targets); a level far from the value's last close (``letters.level_price_band``) is dropped and
said; ``letters-trace --rules`` re-reads the stored letters for free and ``--fill`` completes only the values left
without any level; the review says what each managers' source gave. Synthetic letter, fictitious values."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.data.providers.imap_gmail import NewsletterMail
from app.db.models import Instrument, NewsItem, NewsletterItem, NewsletterValue, PriceEod
from app.db.session import db_session
from app.domain.timeutil import PARIS
from app.services.letter_trace import rules_reread
from app.services.newsletters import ingest_mail, level_band

ALPHA, BETA = "FR0000000AA1", "FR0000000BB2"
RECV = datetime(2026, 11, 2, 10, 10, tzinfo=UTC)
LETTER = """Les fiches du jour

Valtera Industries : le plus haut annuel en ligne de mire

Valtera Industries — achat : rebond sur le support de 41,15. On visera dans un premier temps le plus haut annuel à \
52,40 euros.

Objectifs : 52,40 puis 53,10. Invalidation publiée : clôture sous 39,90. Prochaine résistance majeure à 410.

CAC 40 : support 7 400, résistance 7 650.

Norvane Services : une clôture au-dessus de 3,42 € serait un premier signal, avec pour cible la zone \
3,75 – 3,90 € et un stop d'invalidation sous 3,21 €. À surveiller.
"""


def _seed(s) -> None:
    for isin, name, close in ((ALPHA, "Valtera Industries SA", 45.0), (BETA, "Norvane Services SE", 3.40)):
        s.add(Instrument(isin=isin, name=name, mic="XPAR", currency="EUR", in_universe=True, pea_eligible=True))
        s.add(PriceEod(isin=isin, day=date(2026, 10, 30), open=close, high=close, low=close, close=close, volume=1000,
                       source="fixture", received_at=RECV))  # fmt: skip
    s.flush()


def _mail(mid: str = "r3-1") -> NewsletterMail:
    return NewsletterMail("momentum_capital", mid, "Momentum — 2 novembre", "lettre@capital.fr", RECV, RECV, LETTER,
                          f"hash-{mid}")  # fmt: skip


def test_band_parameter_is_read(config):
    assert level_band(config) == (0.5, 2.0)


def test_the_reception_reads_the_fiche_without_the_llm_and_drops_a_far_level(config, db_engine):
    with db_session() as s:
        _seed(s)
        rep = ingest_mail(s, _mail(), config, None)
        assert rep.parsed_by_llm == 0
        rows = {v.isin: v for v in s.scalars(select(NewsletterValue)) if v.isin}
        assert rows[ALPHA].direction == "buy" and rows[ALPHA].levels == {
            "support": 41.15, "stop": 39.9, "objectif": 52.4, "objectif_2": 53.1}  # fmt: skip
        assert rows[BETA].direction == "watch" and rows[BETA].levels == {
            "declencheur": 3.42, "stop": 3.21, "objectif": 3.75, "objectif_2": 3.9}  # fmt: skip
        item = s.scalar(select(NewsletterItem))
        assert item.parsed["levels_out_of_band"] == ["valtera industries:resistance 410"]  # 410 > 2 × 45: dropped


def test_letters_trace_rules_rereads_for_free_and_fills_only_empty_values(config, db_engine):
    with db_session() as s:
        _seed(s)
        it = NewsletterItem(source="momentum_capital", message_id="old", dedup_hash="old", sent_at=RECV,
                            received_at=RECV, subject="Momentum — 2 novembre", body_text=LETTER)  # fmt: skip
        s.add(it)
        s.flush()
        empty = NewsletterValue(item_id=it.id, isin=BETA, name="norvane services", direction="watch", levels=None)
        kept = NewsletterValue(item_id=it.id, isin=ALPHA, name="valtera industries", direction="buy",
                               levels={"objectif": 52.4})  # fmt: skip
        s.add_all([empty, kept])
        s.flush()
        day = RECV.astimezone(PARIS).date()
        lines = rules_reread(s, day, level_band(config))
        txt = "\n".join(lines)
        assert ("norvane services (FR0000000BB2) · enregistrés : aucun niveau · règles actuelles : déclencheur 3.42, "
                "objectif 1 3.75, objectif 2 3.9, invalidation 3.21") in txt  # fmt: skip
        assert "écartés (trop loin du cours) : resistance 410" in txt and "Aperçu" in lines[-1]
        assert empty.levels is None  # a preview stores nothing
        lines = rules_reread(s, day, level_band(config), apply=True)
        assert empty.levels == {"declencheur": 3.42, "stop": 3.21, "objectif": 3.75, "objectif_2": 3.9}
        assert kept.levels == {"objectif": 52.4}  # a value that has levels keeps them
        assert it.parsed["rules_fill"]["values"] == 1 and lines[-1].startswith("1 valeur(s)")


def test_the_review_says_what_each_managers_source_gave(config, db_engine):
    from app.services import daily_review

    day = datetime.now(PARIS).date()
    now = datetime.now(UTC)
    with db_session() as s:
        s.add(NewsItem(source="cest_votre_argent", guid="ep-30", dedup_hash="ep-30", title="Les gérants du jour",
                       published_at=now - timedelta(days=2), fetched_at=now - timedelta(days=2)))  # fmt: skip
        s.add(NewsItem(source="cva_portefeuille_gerants", guid="w44", dedup_hash="w44",
                       title=f"{(day - timedelta(days=3)):%d/%m/%Y} – C'est Votre Argent",
                       published_at=now - timedelta(days=1), fetched_at=now - timedelta(days=1),
                       classification={"media": [{"name": "Alpha", "stance": "achat", "speaker": "A"},
                                                 {"name": "Beta", "stance": "vente", "speaker": "B"}]}))  # fmt: skip
        s.flush()
        r = daily_review.build(s, config, day)
    state = r["etat_experts"]
    assert any(x.startswith("C'est votre argent (émission BFM) : dernier épisode") and "pas encore lu" in x
               for x in state)  # fmt: skip
    assert any("relevé hebdomadaire" in x and f"émission du {(day - timedelta(days=3)):%d/%m}" in x
               and "1 achat(s), 1 vente(s)" in x for x in state)  # fmt: skip
    assert "état : C'est votre argent" in daily_review.as_text(r)


def test_from_the_letters_words_to_inexploitable_by_construction(config, db_engine):
    """The Elior case of 02/11/2026, end to end on synthetic data: the letter writes its trigger, targets and
    invalidation in words; the rules read them; the engine's maximum entry price falls under the published trigger;
    the review says INEXPLOITABLE, with no order."""
    from app.domain.review import idea_verdict
    from app.services.daily_review import idea_from
    from app.services.letter_analysis import analyse_items
    from tests.integration.test_engine import ISIN, breakout_bars
    from tests.integration.test_engine import _seed as seed_engine
    from tests.integration.test_letters_flow import _letter_proposals

    body = ("TotalEnergies : le scénario suppose une sortie du range par une clôture au-dessus de 100,20 €, avec pour "
            "cible 102,50 puis 103. Stop d'invalidation sous 82,60 €. À surveiller.")  # fmt: skip
    now = datetime.now(UTC) - timedelta(hours=1)
    with db_session() as s:
        seed_engine(s, breakout_bars()[:-1])
        ingest_mail(s, NewsletterMail("momentum_capital", "r3-e2e", "Momentum", "lettre@capital.fr", now, now, body,
                                      "hash-r3-e2e"), config, None)  # fmt: skip
        v = s.scalar(select(NewsletterValue).where(NewsletterValue.isin == ISIN))
        assert v.direction == "watch" and v.levels == {"declencheur": 100.2, "stop": 82.6, "objectif": 102.5,
                                                       "objectif_2": 103.0}  # fmt: skip
        analyse_items(s, config, [s.get(NewsletterItem, v.item_id)])
        (p,) = _letter_proposals(s)
        verdict = idea_verdict(idea_from(p, None))
        assert p.details["p_max"] < 100.2 and verdict.decision == "inexploitable"
        assert "100.2" in verdict.reason and verdict.todo.startswith("Rien à faire")


def test_when_the_reader_read_the_letter_a_value_found_by_the_rules_alone_is_a_mention(config, db_engine):
    """02/11/2026: the rules gave « achat » to every value of a paragraph with a buy word, and took « momentum » (the
    letter's own name) for a company. The reader names the recommended values; the others stay mentions, with the
    levels written."""
    from pathlib import Path

    from app.llm.schemas import NewsletterExtraction
    from app.llm.schemas import NewsletterValue as Read

    class Settings:
        prompts_dir = Path(__file__).resolve().parents[2] / "app" / "llm" / "prompts"

    class Reader:
        settings = Settings()

        def parse(self, s, purpose, prompt, text, schema, max_tokens=0):
            return NewsletterExtraction(values=[Read(name="Valtera Industries", direction="buy")]), None

    with db_session() as s:
        _seed(s)
        ingest_mail(s, _mail("r3-reader"), config, Reader())
        rows = {v.isin: v for v in s.scalars(select(NewsletterValue)) if v.isin}
        assert rows[ALPHA].direction == "buy" and rows[ALPHA].levels["objectif"] == 52.4  # the rules' levels kept
        assert rows[BETA].direction == "neutral" and rows[BETA].levels["declencheur"] == 3.42  # a mention
        item = s.scalar(select(NewsletterItem))
        assert item.parsed["rules_only_mentions"] == ["norvane services:watch"] and item.parsed_by == "llm"
