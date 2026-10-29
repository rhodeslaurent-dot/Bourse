"""docs/20 §20.10 — the tool's own analysis of a value a letter recommends (``app/domain/letter_setup.py``), on the
frozen OHLC fixture ``tests/fixtures/ohlc/uptrend_pullback.csv`` (300 sessions, close 136.05, ATR14 ≈ 2.15,
ADR20 ≈ 1.68 % → maximum stop distance 2.5 × ADR20 ≈ 4.2 %)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.domain.admission import Setup as SetupKind
from app.domain.detectors import DetectorContext, Setup
from app.domain.indicators import Bar, compute_daily_features
from app.domain.letter_setup import (
    UNDER_TRIGGER,
    ControlFacts,
    LetterParams,
    LetterRecommendation,
    LetterSetup,
    NotAnalysable,
    VerdictFacts,
    build,
    checklist,
    to_verify,
    verdict,
)
from app.domain.zones import detect_zones

FX = Path(__file__).resolve().parents[1] / "fixtures" / "ohlc"


@pytest.fixture(scope="module")
def bars() -> list[Bar]:
    with open(FX / "uptrend_pullback.csv", encoding="utf-8") as fh:
        return [
            Bar(float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), int(r["volume"]))
            for r in csv.DictReader(fh)
        ]


@pytest.fixture(scope="module")
def ctx(bars: list[Bar]) -> DetectorContext:
    f = compute_daily_features(bars)
    assert f.atr14 is not None
    return DetectorContext(92.0, detect_zones(bars[-250:], f.atr14, 5, 0.5, 2), 0.005, 0.25)


P = LetterParams(stop_lookback_low=3)  # a tight structure (3 sessions) so that it lies within the maximum


def _build(bars: list[Bar], ctx: DetectorContext, levels: dict[str, float], **kw: object) -> LetterSetup:
    res = build(LetterRecommendation("momentum_capital", 1, "buy", levels), bars, compute_daily_features(bars), ctx,
                P, bars[-1].close, **kw)  # type: ignore[arg-type]  # fmt: skip
    assert isinstance(res, LetterSetup), res
    return res


def test_a_wide_letter_stop_gives_way_to_the_structure_stop_and_both_are_shown(bars, ctx):
    c = bars[-1].close
    res = _build(bars, ctx, {"achat": c, "stop": round(c * 0.82, 2), "objectif": round(c * 1.25, 2)})
    expected = min(b.low for b in bars[-3:]) - 0.25 * compute_daily_features(bars).atr14  # R1 structural (no zone)
    a = res.analysis()
    assert res.mode == "achat" and res.stop_retenu == "structure"
    assert res.setup.stop == pytest.approx(expected) and a["stop_structure"] == pytest.approx(expected, abs=1e-4)
    assert a["stop_lettre"] == round(c * 0.82, 2) and a["distance_stop_lettre"] == pytest.approx(0.18, abs=1e-3)
    assert a["distance_stop_lettre"] > a["distance_max"] >= a["distance_stop_structure"]
    assert any("invalidation de la lettre à" in n and "trop large" in n for n in a["notes"])
    assert res.setup.targets[0] == round(c * 1.25, 2) and a["objectifs_lettre"] == [round(c * 1.25, 2)]
    assert not res.setup.warnings  # the structure stop is within 2.5 × ADR20


def test_a_letter_stop_within_the_maximum_is_kept(bars, ctx):
    c = bars[-1].close
    adr = compute_daily_features(bars).adr20
    stop = round(c * (1 - 1.5 * adr), 2)  # 1.5 ADR under the entry, maximum 2.5 ADR
    res = _build(bars, ctx, {"achat": c, "stop": stop, "objectif": round(c * 1.2, 2)})
    assert res.stop_retenu == "lettre" and res.setup.stop == stop and res.setup.stop_basis == "lettre"
    assert res.analysis()["stop_structure"] is not None  # computed and shown anyway


def test_the_stop_policy_parameter_is_applied(bars, ctx):
    c = bars[-1].close
    levels = {"achat": c, "stop": round(c * 0.82, 2), "objectif": round(c * 1.25, 2)}
    f = compute_daily_features(bars)
    only = build(LetterRecommendation("m", 1, "buy", levels), bars, f, ctx,
                 LetterParams(stop_policy="letter_only", stop_lookback_low=3), c)  # fmt: skip
    assert isinstance(only, LetterSetup) and only.setup.stop == round(c * 0.82, 2)
    assert any("stop trop large" in w for w in only.setup.warnings)  # 18 % under the entry: said, folded later


def test_a_breakout_trigger_entry_and_its_cap(bars, ctx):
    c = bars[-1].close
    trig = round(c * 1.02, 2)
    res = _build(bars, ctx, {"declencheur": trig, "objectif": round(c * 1.08, 2), "stop": round(c * 0.99, 2)})
    assert res.mode == "declencheur" and res.setup.kind == SetupKind.BREAKOUT
    assert res.setup.entry_level == pytest.approx(trig * 1.003) and res.setup.p_max_technical == pytest.approx(
        trig * 1.01
    )
    assert res.setup.level_low == trig and res.trigger == trig  # the obstacle a close must hold (R5)


def test_without_an_entry_level_the_entry_is_the_price(bars, ctx):
    """Sopra Steria, 29/10/2026: trendline broken, « ouverture haussière », targets and an invalidation only."""
    c = bars[-1].close
    res = _build(bars, ctx, {"objectif": round(c * 1.15, 2), "objectif_2": round(c * 1.25, 2),
                             "stop": round(c * 0.9, 2)})  # fmt: skip
    assert res.mode == "marche" and res.setup.kind == SetupKind.PULLBACK and res.setup.entry_level == c
    assert res.setup.targets[:2] == [round(c * 1.15, 2), round(c * 1.25, 2)]


def test_without_any_level_the_tool_builds_or_borrows_the_setup(bars, ctx):
    res = _build(bars, ctx, {})
    assert res.mode == "outil" and res.setup.entry_level == bars[-1].close and res.detector is None
    det = Setup("d7_trend_quality", SetupKind.PULLBACK, 137.0, 138.0, 131.0, "zone", 132.0, [145.0], "semaines")
    borrowed = _build(bars, ctx, {}, detector_setups=[det])
    assert borrowed.mode == "outil" and borrowed.detector == "d7_trend_quality"
    assert (borrowed.setup.entry_level, borrowed.setup.stop, borrowed.setup.targets) == (137.0, 131.0, [145.0])
    assert any("lettre + détecteur" in n for n in borrowed.notes)


def test_fewer_than_sixty_sessions_is_not_analysable(bars, ctx):
    short = bars[-59:]
    res = build(LetterRecommendation("m", 1, "buy", {"achat": 1.0}), short, compute_daily_features(short), ctx, P,
                short[-1].close)  # fmt: skip
    assert isinstance(res, NotAnalysable) and res.code == "historique" and "59 séances" in res.reason


def test_a_crossed_invalidation_is_said(bars, ctx):
    c = bars[-1].close
    res = _build(bars, ctx, {"stop": round(c * 1.01, 2), "objectif": round(c * 1.2, 2)})
    assert any("invalidation de la lettre franchie" in w for w in res.setup.warnings)


def _facts(**kw: object) -> ControlFacts:
    base = dict(isin="FR0000000001", sessions=300, min_sessions=60, price=100.0, price_status="delayed",
                price_fresh=False, max_age_minutes=3, level_low=95.0, level_low_origin="z", level_low_structural=True,
                target=110.0, admission="admis", p_max=100.5, min_reward_risk=2.0, entry=100.0, stop=96.0,
                max_stop_distance=0.05, blocked=(), earnings_in_horizon=None, shares=10)  # fmt: skip
    base.update(kw)
    return ControlFacts(**base)  # type: ignore[arg-type]


def test_the_control_list_makes_a_delayed_quote_to_be_verified():
    items = checklist(_facts())
    assert [i.key for i in items][:4] == ["valeur", "historique", "cours", "structure"]
    assert to_verify(items) and not next(i for i in items if i.key == "cours").ok
    assert not to_verify(checklist(_facts(price_status="eod")))  # the close of a session: an order for the next one
    assert to_verify(checklist(_facts(price_status="eod", level_low_structural=False)))


def test_the_verdict_closed_list():
    under = f"WATCH : ratio net < 2 {UNDER_TRIGGER} (gain 3 / risque 2 = 1.50)"
    assert verdict(VerdictFacts("WATCH", "refuse", (under,), True, False)) == ("rejet", under)
    assert verdict(VerdictFacts("WATCH", "refuse", (under,), True, True))[0] == "alerte_declencheur"
    wait = "WATCH : refusé — P_max 100 dépassé — limite posée en attente d'un repli"
    assert verdict(VerdictFacts("WATCH", "refuse", (wait,), True, None))[0] == "attendre_repli"
    assert verdict(VerdictFacts("BUY", "admis", (), True, None))[0] == "entrer_maintenant"
    assert verdict(VerdictFacts("BUY", "admis", (), False, None))[0] == "fiche_fixing"
    macro = "BLOQUÉ : USD CPI dans les 30 h — exposition élevée ; gel de l'entrée (R14)"
    assert verdict(VerdictFacts("BUY", "admis", (macro,), True, None)) == ("bloque", macro)
    assert verdict(VerdictFacts("WATCH", "non_testable", ("WATCH : pas d'objectif exploitable",), True, None))[0] == (
        "rejet"
    )
