from pathlib import Path

import pytest

from app.data.providers.boursobank_csv import BoursoFormatError, parse_number, parse_operations, parse_positions

FX = Path(__file__).resolve().parents[1] / "fixtures" / "csv"


def test_parse_french_numbers():
    assert parse_number("2 204,80") == 2204.80
    assert parse_number("1.234,56") == 1234.56
    assert parse_number("+5,39 %") == 5.39
    assert parse_number("") is None
    with pytest.raises(BoursoFormatError):
        parse_number("abc")


def test_parse_positions_fixture():
    rows = parse_positions((FX / "boursobank_positions_anonymised.csv").read_text(encoding="utf-8"))
    assert [r.isin for r in rows] == ["FR0000120271", "FR0000120073", "NL0010273215"]
    assert rows[0].quantity == 40 and rows[0].buying_price == 52.30 and rows[0].last_price == 55.12


def test_parse_operations_fixture_skips_non_trades():
    ops = parse_operations((FX / "boursobank_operations_anonymised.csv").read_text(encoding="utf-8"))
    assert len(ops) == 3 and ops[2].side == "sell" and ops[0].fees == 3.90 and ops[0].reference == "OP-000123"
    assert ops[0].date.isoformat().startswith("2026-09-12T09:31:07")


def test_format_change_raises_instead_of_importing_garbage():
    with pytest.raises(BoursoFormatError):
        parse_positions("colonneA;colonneB\n1;2\n")
    with pytest.raises(BoursoFormatError):
        parse_positions("name;isin;quantity;buyingPrice\nX;PASUNISIN;1;2\n")
