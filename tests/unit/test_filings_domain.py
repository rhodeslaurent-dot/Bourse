"""Lot L1 (ADR-017) — official releases: family of a type code, ISIN check, Paris wall clock (the source's offset
label is never trusted), coverage of a month against the same month of the other years."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.filings import (
    FAMILIES,
    family_for,
    family_of,
    known_codes,
    month_coverage,
    month_key,
    paris_wall_clock,
    valid_isin,
)


def test_every_code_maps_to_a_declared_family_and_unknown_is_autre():
    assert set(known_codes().values()) <= set(FAMILIES)
    assert family_of("001007") == ("resultats", True)
    assert family_of("001002") == ("operation", True)
    assert family_of("009003") == ("opa", True)
    assert family_of("013004") == ("rachat_actions", True)
    assert family_of("023000") == ("franchissement_seuil", True)
    assert family_of("300005") == ("rapport_financier", True)  # document de référence (secondary 003000)
    assert family_of("777000") == ("autre", False)  # an unknown code: never guessed from the title
    assert family_of(None) == ("autre", False)


def test_unknown_code_takes_the_secondary_code_then_its_type():
    assert family_for("999001", ["999001", "003000"]) == ("rapport_financier", False)
    assert family_for("004001", ["004001"]) == ("rapport_financier", False)  # type 004 = half-year report only
    assert family_for("013009", []) == ("rachat_actions", False)
    assert family_for("001009", []) == ("autre", False)  # type 001 mixes several families: not chosen
    assert family_for("001007", ["006000"]) == ("resultats", True)  # a known primary code wins
    assert family_for(None, []) == ("autre", False)


def test_isin_shape_and_check_digit():
    for good in ("FR0000120107", "FR0010785790", "FR0010908533", "DE0007164600", "US0378331005"):
        assert valid_isin(good) == good
    assert valid_isin(" fr0000120107 ") == "FR0000120107"
    assert valid_isin("FR0000120108") is None  # wrong check digit
    assert valid_isin("999999999999") is None  # placeholder of the flow for an unlisted issuer
    assert valid_isin(None) is None and valid_isin("") is None


def test_api_wall_clock_with_separate_label():
    # API: the Paris wall clock printed as « +00:00 », the real offset in timezone_uin_dat_amf
    t = paris_wall_clock("2012-04-12T16:30:31+00:00", "02:00")
    assert t.utc == datetime(2012, 4, 12, 14, 30, 31, tzinfo=UTC)
    assert (t.label, t.paris_offset, t.label_ok, t.precision) == ("+02:00", "+02:00", True, "seconde")


def test_dila_labels_right_in_january_and_july_wrong_after_the_october_change():
    jan = paris_wall_clock("2026-01-15T07:00:00+01:00")
    jul = paris_wall_clock("2025-07-15T07:00:00+02:00")
    assert jan.utc == datetime(2026, 1, 15, 6, 0, tzinfo=UTC) and jan.label_ok is True
    assert jul.utc == datetime(2025, 7, 15, 5, 0, tzinfo=UTC) and jul.label_ok is True
    # 28/10/2026: Paris is back to +01:00 but the package still writes +02:00 — the 07:30 release stays 07:30 Paris
    oct_ = paris_wall_clock("2026-10-28T07:30:00+02:00")
    assert oct_.utc == datetime(2026, 10, 28, 6, 30, tzinfo=UTC)
    assert (oct_.label, oct_.paris_offset, oct_.label_ok) == ("+02:00", "+01:00", False)


def test_sentinel_unreadable_and_date_only():
    assert paris_wall_clock("8888-01-01T00:00:00+02:00").utc is None  # « no market date » of the flow
    assert paris_wall_clock("8888-01-01T00:00:00+02:00").precision == "illisible"
    assert paris_wall_clock("n/a").precision == "illisible"
    assert paris_wall_clock(None).utc is None
    assert paris_wall_clock("2026-02-30T10:00:00+01:00").utc is None  # impossible date
    day = paris_wall_clock("2026-03-04T00:00:00+01:00")
    assert day.precision == "jour" and day.utc == datetime(2026, 3, 3, 23, 0, tzinfo=UTC)
    assert paris_wall_clock("2026-03-04T10:00:00").label is None and paris_wall_clock("2026-03-04 10:00:00").utc


def test_ambiguous_autumn_hour_takes_the_first_one():
    t = paris_wall_clock("2026-10-25T02:30:00")
    assert t.utc == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)  # the first 02:30 (summer time)


def test_month_key_is_the_paris_month():
    assert month_key(datetime(2026, 1, 31, 23, 30, tzinfo=UTC)) == "2026-02"


def test_month_coverage_flags_a_hole_and_scales_partial_months():
    months = [f"{y}-03" for y in (2019, 2020, 2021, 2022, 2023)] + ["2023-04"]
    counts = {"2019-03": 2000, "2020-03": 2100, "2021-03": 1900, "2022-03": 400, "2023-03": 1000, "2023-04": 50}
    cov = {c.month: c for c in month_coverage(counts, months, 0.5, {"2023-03": 0.5})}
    # the partial 2023-03 is left out of the other years' references
    assert cov["2022-03"].covered is False and cov["2022-03"].reference == 2000.0  # median of 2019, 2020, 2021
    assert cov["2019-03"].covered is True
    assert cov["2020-03"].reference == 1900.0  # median of 2019, 2021, 2022
    # partial month: half of the median of the full other years
    assert cov["2023-03"].reference == 975.0 and cov["2023-03"].covered is True
    assert cov["2023-04"].covered is None  # no other April to compare with
