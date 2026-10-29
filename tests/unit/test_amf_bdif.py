"""docs/34 §34.8 — AMF BDIF managers' dealing declarations: listing, PDF text of the AMF form, roles (never a
name), natures, numbers and dates. Synthetic fixtures only (tests/fixtures/amf_bdif)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.data.base import ProviderUnavailable
from app.data.providers import amf_bdif as A

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "amf_bdif"


def _text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_listing_keeps_the_issuer_the_publication_time_and_the_french_pdf():
    payload = json.loads(_text("listing_synthetic.json"))
    decls, skipped = A.parse_listing(payload)
    assert skipped == 1  # the item without a number
    assert [d.doc_id for d in decls] == ["2026DD9000001", "2026DD9000002", "2026DD9000003"]
    d = decls[0]
    assert d.published_at == datetime(2026, 10, 27, 17, 8, 10, 927000, tzinfo=UTC)
    assert d.info_date == date(2026, 10, 27)
    assert d.issuer_name == "SOCIETE EXEMPLE" and d.issuer_token == "RS00009991"
    assert d.pdf_url == "https://bdif.amf-france.org/back/api/v1/documents/2026/2026DD9000001/AAAA0001.pdf"
    assert d.parse_status == "listing_only" and d.price is None and d.role is None  # nothing guessed


def test_the_pdf_text_gives_role_nature_figures_and_dates_without_the_name():
    f = A.parse_declaration_text(_text("declaration_buy.txt"))
    assert f is not None
    assert f["role"] == "PDG" and f["linked_person"] is False and f["related_role"] is None
    assert f["nature"] == "acquisition" and f["n_transactions"] == 2
    assert f["isin"] == "FR0000000AA1" and f["venue"] == "Euronext Paris" and f["currency"] == "EUR"
    # aggregated block (12,60 × 2 000) + a detailed-only block (13,00 × 500): volume-weighted
    assert f["volume"] == 2500 and f["amount"] == pytest.approx(31_700) and f["price"] == pytest.approx(12.68)
    assert (f["tx_date"], f["tx_date_last"]) == (date(2026, 10, 23), date(2026, 10, 24))
    dumped = json.dumps({k: v for k, v in f.items() if k != "transactions"}, default=str)
    dumped += json.dumps([t.model_dump(mode="json") for t in f["transactions"]])
    assert "Fictive" not in dumped and "Personne" not in dumped  # PRIVACY: the declarant's name is never kept


def test_a_linked_person_keeps_only_the_role_it_is_linked_to():
    f = A.parse_declaration_text(_text("declaration_linked_sale.txt"))
    assert f is not None
    assert f["role"] == "personne liée" and f["related_role"] == "administrateur" and f["linked_person"] is True
    assert f["nature"] == "cession" and f["isin"] is None  # empty ISIN field: the next label is never read as one
    assert f["price"] == 40.0 and f["volume"] == 250 and f["tx_date"] == date(2026, 10, 27)
    assert "Imaginaire" not in json.dumps(f, default=str)


def test_mixed_natures_give_no_aggregated_figure():
    second = "INFORMATION DETAILLEE PAR OPERATION\nPRIX UNITAIRE : 13"
    text = _text("declaration_buy.txt").replace(
        f"NATURE DE LA TRANSACTION : Acquisition\n{second}", f"NATURE DE LA TRANSACTION : Cession\n{second}"
    )
    f = A.parse_declaration_text(text)
    assert f is not None and f["nature"] == "mixte"
    assert f["price"] is None and f["volume"] is None and f["amount"] is None
    assert [t.nature for t in f["transactions"]] == ["acquisition", "cession"]


def test_unrecognised_text_is_refused():
    assert A.parse_declaration_text("") is None
    assert A.parse_declaration_text("Communiqué de presse sans formulaire") is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("X Y, Directeur Général Délégué", ("directeur général délégué", None, False)),
        ("X Y, Directeur général", ("directeur général", None, False)),
        ("X Y, membre du Conseil de surveillance", ("membre du conseil de surveillance", None, False)),
        ("X Y, Chief Financial Officer", ("directeur financier", None, False)),
        ("X Y, personne étroitement liée à Z W, Président du Directoire",
         ("personne liée", "président du directoire", True)),
        ("X Y", ("non reconnu", None, False)),
        (None, (None, None, None)),
    ],
)  # fmt: skip
def test_roles_come_from_a_closed_list(text, expected):
    assert A.classify_role(text) == expected
    assert expected[0] is None or expected[0] in A.ROLES


@pytest.mark.parametrize(
    "text,expected",
    [("Acquisition", "acquisition"), ("Cession", "cession"), ("Souscription", "souscription"),
     ("Exercice de stock-options", "autre"), ("Autre (attribution gratuite)", "autre"), (None, None)],
)  # fmt: skip
def test_natures(text, expected):
    assert A.classify_nature(text) == expected


def test_numbers_and_dates():
    assert A.parse_number("1 960,5000 Euro") == 1960.5
    assert A.parse_number("1,234.5") == 1234.5
    assert A.parse_number("12 345") == 12345
    assert A.parse_number("n.c.") is None
    assert A.parse_date("2026-03-04 +0100") == date(2026, 3, 4)
    assert A.parse_date("04/03/2026") == date(2026, 3, 4)
    assert A.parse_date("1er août 2026") == date(2026, 8, 1)
    assert A.parse_date("31/02/2026") is None


def test_window_bounds_are_paris_days_in_utc():
    p = A.window_params(date(2026, 10, 1), date(2026, 10, 31), 200, 100)
    assert p["TypesInformation"] == "DD" and p["From"] == 200 and p["Size"] == 100
    assert p["DateDebut"] == "2026-09-30T22:00:00.000Z"  # summer time
    assert p["DateFin"] == "2026-10-31T22:59:59.000Z"  # winter time from 25/10


def test_fetch_pages_throttles_and_reads_pdfs_only_when_an_extractor_is_given():
    payload = json.loads(_text("listing_synthetic.json"))
    calls: list[tuple[str, dict | None]] = []
    sleeps: list[float] = []

    def fetch(url, params):
        calls.append((url, params))
        if url.endswith(A.LIST_PATH):
            return json.dumps(payload if params["From"] == 0 else {"result": []}).encode()
        if "BBBB0002" in url:
            raise ProviderUnavailable("HTTP 404")
        return b"%PDF-synthetic"

    res = A.fetch_declarations(date(2026, 10, 27), date(2026, 10, 28), fetch, page_size=4, interval_s=1.0,
                               sleep=sleeps.append)  # fmt: skip
    assert len(res.declarations) == 3 and res.skipped == 1 and res.pdf_read == 0
    assert [c[1]["From"] for c in calls] == [0, 4]  # a full page asks for the next one
    assert all(d.parse_status == "listing_only" for d in res.declarations)

    calls.clear()
    res = A.fetch_declarations(
        date(2026, 10, 27), date(2026, 10, 28), fetch, page_size=100, interval_s=1.0,
        pdf_text=lambda b: _text("declaration_buy.txt") if b.startswith(b"%PDF") else None,
        skip_pdf={"2026DD9000003"}, sleep=sleeps.append,
    )  # fmt: skip
    status = {d.doc_id: d.parse_status for d in res.declarations}
    assert status == {"2026DD9000001": "parsed", "2026DD9000002": "pdf_unavailable", "2026DD9000003": "listing_only"}
    assert len(calls) == 3 and res.pdf_read == 1  # one listing page, two PDFs (the skipped one is never asked)
    assert sleeps and all(0 < x <= 1.0 for x in sleeps)  # never two requests within a second


def test_an_unreadable_listing_is_unavailable():
    with pytest.raises(ProviderUnavailable):
        A.fetch_declarations(date(2026, 10, 27), date(2026, 10, 27), lambda u, p: b"<html>", sleep=lambda s: None)
