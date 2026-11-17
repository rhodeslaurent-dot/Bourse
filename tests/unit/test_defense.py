"""Step 2, option B (docs/42) — a defence of a held position against « do nothing », per share, gross."""

from __future__ import annotations

from app.domain.defense import Defense, defense_value, gap_vs_nothing, hold_value, python_defense
from app.domain.indicators import Bar


def _bars(*ohlc: tuple[float, float, float, float]) -> list[Bar]:
    return [Bar(o, h, lo, c, 1000) for o, h, lo, c in ohlc]


FALL = _bars((100, 101, 97, 98), (98, 99, 93, 94), (94, 95, 89, 90))  # a slow fall through 95 then 90
GAP = _bars((100, 101, 99, 100), (88, 90, 87, 89), (89, 90, 88, 89))  # a gap through every stop


def test_doing_nothing_keeps_the_current_stop():
    assert hold_value(FALL, 90.0, 3, 0.0) == 90.0  # hit on the third session
    assert hold_value(FALL, None, 3, 0.0) == 90  # no stop: the close of the horizon
    assert hold_value(FALL, 95.0, 3, 0.01) == 95.0 * 0.99  # slipped stop
    assert hold_value(GAP, 95.0, 3, 0.0) == 88  # gap through the stop: the open
    assert hold_value(FALL, 90.0, 4, 0.0) is None  # the window is not over


def test_each_defence_against_doing_nothing():
    assert gap_vs_nothing(FALL, Defense("remonter_stop", stop=95.0), 90.0, 100.0, 3, 0.0) == 0.05
    assert gap_vs_nothing(FALL, Defense("sortir"), 90.0, 100.0, 3, 0.0) == 0.10  # out at the next open (100)
    assert gap_vs_nothing(FALL, Defense("alleger", part=0.5), 90.0, 100.0, 3, 0.0) == 0.05
    assert gap_vs_nothing(FALL, Defense("conserver"), 90.0, 100.0, 3, 0.0) == 0.0
    assert defense_value(FALL, Defense("remonter_stop", stop=85.0), 90.0, 3, 0.0) == 90.0  # never lowered
    rise = _bars((100, 104, 99, 103), (103, 108, 102, 107), (107, 111, 106, 110))
    assert gap_vs_nothing(rise, Defense("sortir"), 90.0, 100.0, 3, 0.0) == -0.10  # a defence can cost
    assert gap_vs_nothing(FALL, Defense("sortir"), 90.0, 0.0, 3, 0.0) is None


def test_pythons_review_line_read_as_a_defence():
    assert python_defense({"verdict": "VENDRE"}) == Defense("sortir")
    assert python_defense({"verdict": "ALLÉGER", "alleger_pct": 0.5}) == Defense("alleger", part=0.5)
    assert python_defense({"verdict": "CONSERVER", "stop": 90.0, "stop_propose": 94.0}) == Defense(
        "remonter_stop", stop=94.0
    )
    assert python_defense({"verdict": "CONSERVER", "stop": 90.0, "stop_propose": 90.0}) == Defense("conserver")
    assert python_defense(None) == Defense("conserver")
