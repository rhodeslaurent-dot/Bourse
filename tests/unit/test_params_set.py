"""params-init --update --set: a scalar set in place (comment kept), and child keys with dashes merged."""

from app.cli import merge_missing_children, set_scalar

TEXT = """pea:
  eligible_countries: ["FR"]
  opening_date: null                    # date du premier versement
llm:
  prices_usd_per_mtok:
    claude-haiku-4-5: {input: 1.0, output: 5.0}
"""


def test_set_scalar_keeps_the_comment_and_quotes_dates():
    out, ok = set_scalar(TEXT, "pea.opening_date", "2026-07-08")
    assert ok and '  opening_date: "2026-07-08"                    # date du premier versement' in out
    assert set_scalar(TEXT, "pea.inconnue", "1") == (TEXT, False)
    out, ok = set_scalar(TEXT, "pea.opening_date", "null")
    assert ok and "opening_date: null" in out


def test_merge_children_with_dashes():
    example = TEXT + "    claude-haiku-5-5: {input: 0.1, output: 0.5}\n"
    out, added = merge_missing_children(example, TEXT, "llm.prices_usd_per_mtok")
    assert added == ["claude-haiku-5-5"] and "claude-haiku-5-5" in out
