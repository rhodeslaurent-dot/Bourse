"""docs/25 L1 and L6: Claude classifies only the news whose classification is used, and /sante shows
the cost of the calls per purpose."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.data.providers.rss import NewsItem as RssItem
from app.db.models import Instrument, LlmCall
from app.db.session import db_session
from app.llm.gateway import ClaudeGateway, LlmSettings
from app.llm.schemas import NewsClassification
from app.services.llm_costs import cost_board
from app.services.news import ingest_rss_items

SETTINGS = LlmSettings(
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    0,
    15,
    5,
    {"claude-haiku-4-5": {"input": 1.0, "output": 5.0}},
    Path("app/llm/prompts"),
    False,
)


def _item(source: str, title: str) -> RssItem:
    now = datetime.now(UTC)
    return RssItem(
        source=source,
        guid=title,
        title=title,
        url=f"https://example.org/{abs(hash(title))}",
        body="corps de l'article",
        published_at=now,
        fetched_at=now,
        dedup_hash=f"{source}-{title}"[:64],
    )


def _fake(calls: list[str]):
    def call(model, system, user, schema, temperature, max_tokens):
        calls.append(user)
        return (
            NewsClassification(
                type="resultats",
                direction="positif",
                magnitude="moyenne",
                ampleur_annoncee=0.5,
                resume="r",
                confiance=0.7,
            ),
            1000,
            100,
        )

    return call


def test_only_attached_items_and_the_press_review_go_to_claude(db_engine):
    calls: list[str] = []
    with db_session() as s:
        s.add(Instrument(isin="FR0000120073", name="Air Liquide", currency="EUR", mic="XPAR", active=True))
        s.flush()
        items = [
            _item("lesechos", "Air Liquide relève ses objectifs"),
            _item("lesechos", "Le pétrole recule à Londres"),
            _item("presse", "La BCE prête à baisser ses taux"),
        ]
        rep = ingest_rss_items(s, items, ClaudeGateway(SETTINGS, call=_fake(calls)))
        assert rep.new == 3 and rep.classified_llm == 2 and rep.classified_rules == 1
        assert not any("pétrole" in c for c in calls)
        # « none »: rules only (media feeds are read once by media_extract)
        rep = ingest_rss_items(
            s, [_item("cva", "Air Liquide cité")], ClaudeGateway(SETTINGS, call=_fake(calls)), scope="none"
        )
        assert rep.classified_llm == 0 and len(calls) == 2


def test_cost_board_per_purpose(db_engine):
    now = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
    with db_session() as s:
        for ts, purpose, cost, cached in [
            (now, "news_classify", 0.01, False),
            (now, "news_classify", 0.0, True),
            (now - timedelta(days=3), "letters_review", 0.20, False),
            (now - timedelta(days=30), "thesis", 5.0, False),  # last month: excluded
        ]:
            s.add(
                LlmCall(
                    ts=ts,
                    model="claude-haiku-4-5",
                    prompt_version="1",
                    prompt_hash="h",
                    purpose=purpose,
                    cost_usd=cost,
                    cached=cached,
                    input_tokens=100,
                    output_tokens=10,
                )
            )
        s.flush()
        b = cost_board(s, now)
        assert [r.purpose for r in b.rows] == ["letters_review", "news_classify"]
        news = b.rows[1]
        assert news.calls_month == 2 and news.cache_share == 0.5 and abs(news.per_call - 0.01) < 1e-9
        assert abs(b.today - 0.01) < 1e-9 and abs(b.month - 0.21) < 1e-9 and abs(b.last_7d - 0.21) < 1e-9
        assert b.projected_month is not None and abs(b.projected_month - 0.21 / 7 * 31) < 1e-9
