"""Lot S5 — readers of the SEC daily index, of a filing's header, of a Form 4 and of the FINRA short-volume file.
All documents are synthetic, in the documented formats; no network."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.data.providers import finra_regsho, sec_edgar

INDEX = """Description:           Master Index of EDGAR Dissemination Feed
Last Data Received:    November 13, 2026
Comments:              webmaster@sec.gov
Anonymous FTP:         ftp://ftp.sec.gov/edgar/




CIK|Company Name|Form Type|Date Filed|File Name
--------------------------------------------------------------------------------
1000001|ACME CORP|8-K|20261113|edgar/data/1000001/0001000001-26-000010.txt
1000001|ACME CORP|4|20261113|edgar/data/1000001/0001000001-26-000011.txt
1999999|DOE JANE|4|20261113|edgar/data/1999999/0001000001-26-000011.txt
1000002|OTHER INC|SC 13G/A|2026-11-13|edgar/data/1000002/0001000002-26-000001.txt
broken|line
"""

HEADER_8K = """<SEC-DOCUMENT>0001000001-26-000010.txt : 20261113
<SEC-HEADER>0001000001-26-000010.hdr.sgml : 20261113
<ACCEPTANCE-DATETIME>20261113163005
ACCESSION NUMBER:		0001000001-26-000010
CONFORMED SUBMISSION TYPE:	8-K
PUBLIC DOCUMENT COUNT:		3
CONFORMED PERIOD OF REPORT:	20261113
ITEM INFORMATION:		Results of Operations and Financial Condition
ITEM INFORMATION:		Financial Statements and Exhibits
FILED AS OF DATE:		20261113
</SEC-HEADER>
<DOCUMENT>… exhibits never read …
"""


def form4(code: str = "P", shares: str = "1000", price: str = "20.5", plan: str | None = None,
          officer: str = "1") -> str:  # fmt: skip
    p = f"<aff10b5One>{plan}</aff10b5One>" if plan is not None else ""
    return f"""<SEC-DOCUMENT>0001000001-26-000011.txt : 20261113
<SEC-HEADER>
<ACCEPTANCE-DATETIME>20261113201500
CONFORMED SUBMISSION TYPE:	4
</SEC-HEADER>
<DOCUMENT>
<TYPE>4
<TEXT>
<XML>
<?xml version="1.0"?>
<ownershipDocument>
  <schemaVersion>X0508</schemaVersion>
  <documentType>4</documentType>
  {p}
  <issuer><issuerCik>0001000001</issuerCik><issuerName>Acme Corp</issuerName>
    <issuerTradingSymbol>acme</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0001999999</rptOwnerCik><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>{officer}</isOfficer>
      <officerTitle>Chief Financial Officer</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-11-12</value></transactionDate>
      <transactionCoding><transactionFormType>4</transactionFormType><transactionCode>{code}</transactionCode>
      </transactionCoding>
      <transactionAmounts><transactionShares><value>{shares}</value></transactionShares>
        <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>{"A" if code in ("P", "A") else "D"}</value>
        </transactionAcquiredDisposedCode></transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-11-12</value></transactionDate>
      <transactionCoding><transactionCode>F</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>50</value></transactionShares>
        <transactionPricePerShare><value>20</value></transactionPricePerShare></transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
</XML>
</TEXT>
</DOCUMENT>
"""


def test_the_daily_index_lists_each_filing_line():
    rows = sec_edgar.parse_master_index(INDEX)
    assert [(r.cik, r.form) for r in rows] == [(1000001, "8-K"), (1000001, "4"), (1999999, "4"), (1000002, "SC 13G/A")]
    assert rows[0].day == date(2026, 11, 13) and rows[3].day == date(2026, 11, 13)
    assert rows[1].accession == "0001000001-26-000011" == rows[2].accession  # issuer and owner: one filing
    assert sec_edgar.index_url(date(2026, 11, 13)).endswith("/daily-index/2026/QTR4/master.20261113.idx")


def test_the_header_gives_the_acceptance_time_in_utc_and_the_items():
    h = sec_edgar.parse_header(HEADER_8K)
    assert h.form == "8-K" and h.accession == "0001000001-26-000010"
    assert h.accepted_at == datetime(2026, 11, 13, 21, 30, 5, tzinfo=UTC)  # 16:30:05 New York (EST, UTC−5)
    assert h.items == ("Results of Operations and Financial Condition", "Financial Statements and Exhibits")
    assert sec_edgar.family_of_items(h.items) == "resultats"
    assert sec_edgar.family_of_items(("Financial Statements and Exhibits",)) is None
    assert sec_edgar.family_of_items(("Other Events", "Entry into a Material Definitive Agreement")) == "operation"


def test_a_form4_gives_the_role_and_the_trades_never_the_name():
    f = sec_edgar.parse_form4(form4())
    assert f is not None and f.issuer_cik == 1000001 and f.symbol == "ACME" and f.role == "dirigeant"
    assert "DOE" not in repr(f) and "Chief" not in repr(f)  # neither the name nor the title is kept
    buy, sell, net, d0, d1, n = f.open_market()
    assert buy == pytest.approx(20500.0) and sell == 0 and net == 1000 and n == 1  # F (tax) is not a market trade
    assert d0 == d1 == date(2026, 11, 12)
    sale = sec_edgar.parse_form4(form4("S", plan="1", officer="0"))
    assert sale is not None and sale.plan_10b5_1 is True and sale.role == "administrateur"
    assert sale.open_market()[1] == pytest.approx(20500.0)
    assert sec_edgar.parse_form4("<XML><!DOCTYPE x [<!ENTITY a 'b'>]><ownershipDocument/></XML>") is None
    assert sec_edgar.parse_form4("<XML><ownershipDocument><broken></XML>") is None


def test_company_tickers_map_each_cik_to_its_first_ticker():
    data = {"0": {"cik_str": 1000001, "ticker": "acme", "title": "Acme"},
            "1": {"cik_str": 1000001, "ticker": "ACME-P", "title": "Acme"}, "2": {"cik_str": "x"}}  # fmt: skip
    assert sec_edgar.parse_company_tickers(data) == {1000001: "ACME"}


def test_the_finra_file_is_read_line_by_line_and_never_guessed():
    text = (
        "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n"
        "20261113|ACME|400|10|1000|B,Q,N\n"
        "20261113|BRK.B|50|0|200|Q\n"
        "20261113|BAD|500|0|100|Q\n"  # short above total: skipped
        "20261113|ZERO|0|0|0|Q\n"
        "3\n"
    )
    rows = finra_regsho.parse(text)
    assert [(r.symbol, r.short_volume, r.total_volume) for r in rows] == [("ACME", 400, 1000), ("BRK.B", 50, 200)]
    assert rows[0].day == date(2026, 11, 13)
    assert finra_regsho.norm("BRK.B") == finra_regsho.norm("brk-b") == finra_regsho.norm("BRK/B") == "BRK-B"
    assert finra_regsho.url(date(2026, 11, 13)).endswith("/CNMSshvol20261113.txt")
