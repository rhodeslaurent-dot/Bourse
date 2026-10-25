"""docs/30 S4a — aggregates and strength of the forum chatter (pure), anonymised reading of a thread page,
closed lists of the classification."""

from __future__ import annotations

from datetime import datetime

from app.data.providers.social import forum_messages, repeated_classes, thread_links
from app.domain.social_content import ContentParams, Message, aggregate, effect
from app.domain.timeutil import PARIS

P = ContentParams()


def _m(author: str, theme: str = "contrat", nature: str = "rumeur", direction: str = "positif", **kw) -> Message:
    return Message(author, 1_000.0, theme, nature, direction, kw.get("precise", True), kw.get("summary", "résumé"))


def test_diverse_factual_chatter_is_strong_and_reinforces():
    agg = aggregate([_m(a) for a in "abcde"] + [_m("f", "sentiment", "opinion")], P, now=1_000.0)
    assert (agg.strength, agg.dominant_theme, agg.theme_authors, agg.direction) == ("fort", "contrat", 5, "positif")
    assert agg.concentration is not None and agg.concentration < 0.4 and agg.precise_share == 1.0
    assert effect(agg, small_cap=False, p=P) == "renforce" and len(agg.summaries) == 3


def test_one_account_pushing_a_small_cap_is_a_pump():
    msgs = [_m("x", nature="promotion") for _ in range(5)] + [_m("y", "sentiment", "opinion")]
    agg = aggregate(msgs, P)
    assert agg.strength == "bruit" and agg.concentration is not None and agg.concentration > 0.8  # pitches: no fact
    assert effect(agg, small_cap=True, p=P) == "emballement"
    assert effect(agg, small_cap=False, p=P) is None  # a large cap is not pumped by one account


def test_claimed_inside_information_never_counts():
    """A30.4 — five accounts claiming inside knowledge of a takeover: nothing usable, never « fort »."""
    agg = aggregate([_m(a, "m_and_a", "pretendue_info_privilegiee") for a in "abcde"], P)
    assert agg.insider_claims == 5 and agg.strength == "indisponible" and agg.dominant_theme is None
    assert effect(agg, small_cap=False, p=P) is None and not agg.summaries
    assert any("information privilégiée" in r for r in agg.reasons)


def test_unknown_authors_are_not_one_author():
    agg = aggregate([_m("") for _ in range(6)], P)
    assert agg.concentration is None and agg.distinct_authors == 0 and agg.strength == "faible"
    assert effect(agg, small_cap=True, p=P) is None
    assert "auteurs non lus sur la page : diversité inconnue" in agg.reasons


def test_no_classified_message_is_unknown_not_noise():
    agg = aggregate([Message("a", None), Message("b", None)], P)
    assert agg.strength == "indisponible" and agg.messages == 2 and agg.classified == 0


THREAD = """<html><body><div class="topic">
<div class="msg-row"><span class="pseudo">Jeannot75</span><span class="when">25/10/2026 14:10</span>
<div class="msg-body">Contrat signé avec Airbus selon @Pierrot, voir https://www.lesechos.fr/a?c=1 — jeannot75 dixit
</div></div>
<div class="msg-row"><span class="pseudo">Marie_Bourse</span><span class="when">25/10/2026 15:02</span>
<div class="msg-body">Je confirme, Jeannot75 a raison<br>pour le <b>contrat</b></div></div>
<div class="msg-row"><span class="pseudo"></span><span class="when">24/10/2026</span>
<div class="msg-body">Anonyme</div></div>
</div></body></html>"""
SEL = {"message": "msg-row", "author": "pseudo", "text": "msg-body", "date": "when"}


def test_thread_messages_are_anonymised_before_leaving_the_provider():
    """A30.1 — no pseudonym in the texts (theirs or quoted), mentions and links reduced, authors hashed."""
    now = datetime(2026, 10, 25, 17, 0, tzinfo=PARIS)
    msgs = forum_messages(THREAD, SEL, now, salt="s1")
    assert len(msgs) == 3
    blob = " ".join(m.text + m.author_key for m in msgs).lower()
    assert "jeannot" not in blob and "marie" not in blob and "pierrot" not in blob
    assert msgs[0].text.startswith("Contrat signé avec Airbus selon @[membre], voir [lien lesechos.fr]")
    assert msgs[1].text == "Je confirme, [membre] a raison pour le contrat"
    assert (msgs[0].ts.hour, msgs[0].ts.minute) == (14, 10) and msgs[2].ts is None and msgs[2].day.day == 24
    assert msgs[2].author_key == "" and msgs[0].author_key != msgs[1].author_key
    again = forum_messages(THREAD, SEL, now, salt="s2")
    assert again[0].author_key != msgs[0].author_key  # a new salt at each read: never recognisable
    assert forum_messages(THREAD, {"message": "msg-row"}, now, "s") == []  # no text class: nothing guessed


def test_thread_links_and_structure_diagnostic_hold_no_text():
    page = '<a href="/bourse/forum/1rPTTE/detail/123/#m4">a</a><a href="/bourse/forum/1rPTTE/detail/123/">b</a>'
    page += '<a href="/bourse/forum/1rPTTE/detail/456/">c</a><a href="/autre/">d</a>'
    base = "https://www.boursorama.com/bourse/forum/1rPTTE/"
    assert thread_links(page, base, r"/detail/\d+") == [
        "https://www.boursorama.com/bourse/forum/1rPTTE/detail/123/",
        "https://www.boursorama.com/bourse/forum/1rPTTE/detail/456/",
    ]
    assert thread_links(page, base, None) == []
    rows = repeated_classes(THREAD)
    assert ("div.msg-row", 3) in [(k, n) for k, n, _ in rows]
    assert not any("Jeannot" in k or "Marie" in k for k, _, _ in rows)


def test_closed_lists_turn_an_unknown_value_into_non_classe():
    """A30.2"""
    from app.llm.schemas import ForumBatchLabels

    lab = {"i": 0, "theme": "astrologie", "nature": "rumeur", "direction": "haussier", "precision": "precise",
           "source_citee": "presse", "resume": "x"}  # fmt: skip
    m = ForumBatchLabels.model_validate({"messages": [lab]}).messages[0]
    assert (m.theme, m.nature, m.direction) == ("non_classe", "rumeur", "non_classe")


def test_numbers_absent_from_the_message_are_removed_from_the_summary():
    """A30.3 (rule 3)"""
    from app.services.social_content import scrub_numbers

    out = scrub_numbers("Un membre évoque un contrat de 250 millions et un objectif à 48,5", "contrat à 48,5 €")
    assert "250" not in out and "48,5" in out


def test_short_field_shapes_show_formats_never_words():
    from app.data.providers.social import short_field_shapes

    page = "".join(
        f'<tr><td><button class="c-source__username">Jeannot{k}</button></td>'
        f'<td><span class="c-table__last-message">1{k}:3{k}</span></td></tr>'
        for k in range(6)
    )
    rows = {k: (n, shapes) for k, n, shapes in short_field_shapes(page)}
    assert rows["span.c-table__last-message"] == (6, ["99:99"])
    assert rows["button.c-source__username"] == (6, ["xxxxxxx9"])
    assert "Jeannot" not in repr(rows)


def test_set_scalar_keeps_a_regex_backslash_valid_yaml():
    import yaml

    from app.cli import set_scalar

    text = "a:\n  b:\n    rx: null   # commentaire\n"
    out, ok = set_scalar(text, "a.b.rx", r"/detail/\d+")
    assert ok and yaml.safe_load(out)["a"]["b"]["rx"] == r"/detail/\d+" and "# commentaire" in out


def test_boursorama_time_formats_without_year():
    from app.data.providers.social import forum_times_in, message_time

    now = datetime(2026, 10, 25, 17, 0, tzinfo=PARIS)
    assert message_time("24 oct. 14:32", now)[1] == datetime(2026, 10, 24, 14, 32, tzinfo=PARIS)
    assert message_time("28 déc. 09:05", now)[1].year == 2025  # never in the future
    assert message_time("14:32", now)[1] == datetime(2026, 10, 25, 14, 32, tzinfo=PARIS)
    assert message_time("Pseudo", now) == (None, None)
    page = "".join(f'<span class="c-table__last-message">{x}</span>' for x in ("16:05", "24 oct. 14:32", "abc"))
    got = forum_times_in(page, now, "c-table__last-message")
    assert [(ts.day, ts.hour) for _, ts in got] == [(25, 16), (24, 14)]
