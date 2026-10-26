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


def test_missing_feeds_are_appended_without_touching_the_users_own():
    """26/10/2026: the podcast feeds (docs/23 §23.7) were missing from the user's params.yaml — a list is
    never merged by the key-based merges — so no media opinion was ever read."""
    from pathlib import Path

    import yaml

    from app.cli import merge_missing_feeds

    ex = Path("config/params.example.yaml").read_text(encoding="utf-8")
    user = "".join(ln for ln in ex.splitlines(keepends=True) if "cest_votre_argent" not in ln)
    user = user.replace('url: "https://www.actusnews.com/fr/rss"', 'url: "https://mon-adresse.example/rss"')
    out, added = merge_missing_feeds(ex, user)
    feeds = yaml.safe_load(out)["data"]["rss_feeds"]
    assert added == ["cest_votre_argent"] and "cest_votre_argent" in [f["name"] for f in feeds]
    assert next(f for f in feeds if f["name"] == "actusnews")["url"] == "https://mon-adresse.example/rss"
    assert yaml.safe_load(out)["data"]["imap"] == yaml.safe_load(ex)["data"]["imap"]  # the next key is intact
    assert merge_missing_feeds(ex, out) == (out, [])
