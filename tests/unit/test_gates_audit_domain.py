"""Audit of the decision funnel (29/10/2026) — pure parts: fee-net ratio (B1), size × 0.5 (high risk), missing data
and what each absence costs, R14 history text, macro history parsers (hand-written page snippets), gate prefixes."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.data.base import ProviderError, ProviderUnavailable
from app.data.providers import macro_history as mh
from app.domain.costs import CostParams, net_outcome
from app.domain.events import EventParams, event_exposure
from app.domain.risk import EntryOrderKind, FeeSchedule, SizingInput, scale_size, size_position
from app.domain.scoring import FundamentalFacts, ScoreInput, missing_data
from app.services.gates_coverage import reason_prefix

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "macro_history"


def test_fee_net_ratio_is_before_tax():
    cp = CostParams(FeeSchedule("saxo", 0.0008, 2.0), tax_rate=0.314)
    no = net_outcome(100, 100.0, 116.0, 94.81, cp)
    assert no.fee_net_reward_risk is not None and no.net_reward_risk is not None
    assert no.fee_net_reward_risk == pytest.approx(no.net_reward_risk / (1 - 0.314), rel=1e-9)
    assert no.fee_net_reward_risk > 2.5 > no.net_reward_risk  # 2.9 net of fees, 2.0 after tax


def test_scale_size_halves_the_budget_and_iterates_the_fees():
    fees = FeeSchedule("saxo", 0.0008, 2.0)
    full = size_position(SizingInput(40_000, 0.0075, 100.0, 95.0, EntryOrderKind.LIMIT, 0.002, fees, 0.2, 0.05))
    half = scale_size(full, 0.5, fees, 0.0, 1.0)
    assert 0 < half.shares <= full.shares // 2
    assert half.risk_budget_eur == pytest.approx(full.risk_budget_eur / 2)
    assert half.risk_if_filled_at_max_eur + half.fees_estimated_eur <= half.risk_budget_eur + 1e-6
    assert scale_size(full, 0.0001, fees, 0.0, 1.0).blocked  # no share left: said, never a silent 0


def _inp(**kw):
    base = dict(rs_rank=85, above_mm50_mm200=True, mm50_above_mm200=True, above_rising_mm20=True, rvol=1.6,
                clean_trigger=True, over_extended=False, resistance_within_3pct=False, stop_distance_adr=1.2,
                earnings_within_10_days=None, adv_eur=6e6, market_cap_eur=2e9, gap_pct=0.01, non_eur=False,
                regime="green")  # fmt: skip
    base.update(kw)
    return ScoreInput(**base)


def test_missing_data_lists_each_absence_and_its_cost():
    out = {m["donnee"]: m for m in missing_data(_inp(), ["T", "R", "M"], 5.0, 2, shorts_covered=False)}
    assert out["date des prochains résultats"]["points"] == 4 and out["date des prochains résultats"]["bloc"] == "R"
    assert out["force du secteur"]["points"] == 3 and out["force du secteur"]["ratio_admission"] > 0
    assert "0/5 critères" in out["fondamentaux"]["note"] and "non attribué" in out["fondamentaux"]["note"]
    assert "non évaluée" in out["positions courtes"]["note"]
    known = _inp(earnings_within_10_days=False, sector_rs_pct=70.0,
                 fundamentals=FundamentalFacts(0.2, 0.2, 1.0, 10.0, 12.0, True))  # fmt: skip
    assert missing_data(known, ["T", "R", "M"], 5.0, 2, shorts_covered=True) == []


def test_unknown_exposure_says_the_history_is_being_built():
    p = EventParams(8, 4, 0.75, 1.5)
    e = event_exposure([0.01, 0.02], 100, 95, p)
    assert not e.known and "historique en cours de constitution (2/4)" in e.text


def test_official_page_parsers_read_dates_only():
    ecb = mh.parse_ecb_decisions((FIX / "ecb_mopo.html").read_text())
    assert [e.day for e in ecb] == [date(2017, 1, 19), date(2024, 7, 18), date(2024, 9, 12)]
    assert all(e.family == "ecb_decision" and e.ts is None and e.source for e in ecb)
    fed = mh.parse_fed_calendar((FIX / "fed_fomccalendars.html").read_text())
    assert [(e.family, e.day) for e in fed] == [
        ("fed_decision", date(2024, 7, 31)), ("fed_decision", date(2024, 9, 18)),
        ("fomc_minutes", date(2024, 8, 21)), ("fomc_minutes", date(2024, 10, 9)),
    ]  # fmt: skip
    bls = mh.parse_bls_archive((FIX / "bls_cpi.html").read_text())
    assert [(e.family, e.day) for e in bls] == [
        ("us_cpi", date(2024, 9, 11)),
        ("us_cpi", date(2024, 10, 10)),
        ("us_jobs", date(2024, 10, 4)),
    ]  # fmt: skip  — the impossible date 13/45 is skipped, never guessed


def test_eodhd_economic_events_mapping_and_plan():
    rows = [
        {"type": "Fed Interest Rate Decision", "country": "US", "date": "2024-09-18 18:00:00"},
        {"type": "ADP Employment Change", "country": "US", "date": "2024-10-02 12:15:00"},
        {"type": "Non Farm Payrolls", "country": "US", "date": "2024-10-04 12:30:00"},
        {"type": "ECB Interest Rate Decision", "country": "EU", "date": "2024-09-12 12:15:00"},
        {"type": "GDP Growth Rate", "country": "US", "date": "2024-09-26 12:30:00"},
    ]

    def get(path, **kw):
        return rows if kw["country"] == "US" else [r for r in rows if r["country"] == "EU"]

    evs = mh.eodhd_events(get, date(2024, 1, 1), date(2024, 12, 31))
    fams = sorted((e.family, e.day, e.currency) for e in evs)
    assert ("fed_decision", date(2024, 9, 18), "USD") in fams and ("us_jobs", date(2024, 10, 4), "USD") in fams
    assert ("ecb_decision", date(2024, 9, 12), "EUR") in fams
    assert not any(e.day == date(2024, 10, 2) for e in evs)  # ADP is not the NFP
    assert all(e.ts is not None and e.source == "eodhd_economic_events" for e in evs)

    def refused(path, **kw):
        raise ProviderError("EODHD 403 on /economic-events: plan")

    with pytest.raises(ProviderUnavailable):
        mh.eodhd_events(refused, date(2024, 1, 1), date(2024, 12, 31))


def test_reason_prefix_drops_numbers():
    assert reason_prefix("WATCH : ratio d'admission 0.62 < 0.75 (scoring)") == "WATCH : ratio d'admission <"
    assert reason_prefix("BLOQUÉ : USD Federal Funds Rate dans les 30 h — exposition") == (
        "BLOQUÉ : USD Federal Funds Rate dans"
    )
