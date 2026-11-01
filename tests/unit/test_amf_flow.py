"""Lot L1 — reading the AMF flow: API records (real records of 12/04/2012, as returned on 01/11/2026), monthly export
and its day-by-day fallback, DILA index and packages (real notices of 28 and 30/10/2026, channel list trimmed)."""

from __future__ import annotations

import io
import json
import tarfile
from datetime import date
from pathlib import Path

import pytest

from app.data.base import ProviderUnavailable
from app.data.providers.amf_flow import (
    PAGE,
    SOURCE_API,
    SOURCE_DILA,
    fetch_api_month,
    iter_packages,
    list_packages,
    parse_api_record,
    parse_dila_xml,
    read_package,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "amf_flow"


def _api_records() -> list[dict]:
    return [json.loads(line) for line in (FIX / "api_records_2012.jsonl").read_text(encoding="utf-8").splitlines()]


def test_parse_api_record_real():
    r = parse_api_record(_api_records()[0])
    assert r is not None and r.source == SOURCE_API
    assert (r.diffuser, r.uin_id, r.isin, r.ticker) == ("CNS", "109683_20120412", "FR0000120107", "BH")
    assert (r.type_code, r.type_codes, r.disclosure, r.update_status) == ("006000", ["006000"], "OBL", "NEW")
    assert r.language == "fr" and parse_api_record(_api_records()[1]).language == "en"
    assert (r.market_raw, r.amf_raw, r.tz_label) == ("2012-04-12T16:30:31+00:00", "2012-04-12T16:30:31+00:00", "02:00")
    assert r.subtype_label == "Total du nombre de droits de vote et du capital"
    assert r.pdf_path == "CNS/2012/04/FCCNS109683_20120412.pdf" and r.lei is None


def test_parse_api_record_without_number_is_skipped():
    rec = _api_records()[0]
    rec["uin_idt_uin"] = None
    assert parse_api_record(rec) is None


def _jsonl(records: list[dict]) -> bytes:
    return "".join(json.dumps(r) + "\n" for r in records).encode()


def test_month_export_one_request_with_paris_month_bounds():
    calls: list[tuple[str, dict]] = []

    def fetch(url, params):
        calls.append((url, params))
        return _jsonl(_api_records())

    b = fetch_api_month(2016, 12, fetch, sleep=lambda s: None)
    assert b.mode == "export" and len(b.releases) == 2 and b.skipped == 0
    url, params = calls[0]
    assert len(calls) == 1 and url.endswith("/datasets/flux-amf-new-prod/exports/jsonl")
    assert params["where"] == "uin_dat_amf >= date'2016-12-01' and uin_dat_amf < date'2017-01-01'"
    assert params["timezone"] == "UTC" and "uin_dat_mar" in params["select"]


@pytest.mark.parametrize("answer", [b'{"error_code": "ODSQLError", "message": "x"}', b"<html>busy</html>", None])
def test_refused_export_falls_back_to_day_by_day_paging(answer):
    rec = _api_records()[0]
    calls: list[tuple[str, dict]] = []

    def fetch(url, params):
        calls.append((url, params))
        if url.endswith("/exports/jsonl"):
            if answer is None:
                raise ProviderUnavailable("HTTP 503")
            return answer
        if params["where"].startswith("uin_dat_amf >= date'2016-02-03'"):  # a busy day: 150 records, 2 pages
            n = PAGE if params["offset"] == 0 else 50
            res = [{**rec, "uin_idt_uin": f"{params['offset'] + i:06d}_20160203"} for i in range(n)]
            return json.dumps({"total_count": 150, "results": res}).encode()
        return json.dumps({"total_count": 0, "results": []}).encode()

    b = fetch_api_month(2016, 2, fetch, sleep=lambda s: None)
    assert b.mode == "records" and len(b.releases) == 150 and "export refusé" in b.notes[0]
    assert len([c for c in calls if c[0].endswith("/records")]) == 29 + 1  # 29 days + the second page of the 3rd
    assert len({r.uin_id for r in b.releases}) == 150


def test_unreadable_day_raises():
    def fetch(url, params):
        if url.endswith("/exports/jsonl"):
            raise ProviderUnavailable("HTTP 503")
        return b"not json"

    with pytest.raises(ProviderUnavailable):
        fetch_api_month(2016, 2, fetch, sleep=lambda s: None)


def test_dila_index_sorted_and_impossible_dates_dropped():
    html = (FIX / "dila_index.html").read_bytes()
    pk = list_packages(lambda url, params: html)
    assert [p.name for p in pk] == [
        "amf-gz_01012022_0000.tar.gz", "amf-gz_03012022_0800.tar.gz", "amf-gz_03012022_1000.tar.gz",
        "amf-gz_02012023_0800.tar.gz",
    ]  # fmt: skip
    assert pk[0].day == date(2022, 1, 1) and pk[0].hhmm == "0000"


def test_parse_dila_xml_real_notices():
    eden = parse_dila_xml((FIX / "MDECO083574_20261028.xml").read_bytes())
    assert eden is not None and eden.source == SOURCE_DILA
    assert (eden.diffuser, eden.uin_id, eden.isin, eden.ticker) == ("ECO", "083574_20261028", "FR0010908533", "EDEN")
    assert eden.market_raw == "2026-10-28T08:00:09+02:00" and eden.amf_raw == "2026-10-30T22:00:37+02:00"
    assert (eden.type_code, eden.type_codes, eden.language, eden.tz_label) == ("001006", ["001006"], "en", None)
    assert eden.title and "€800 million" in eden.title and eden.update_ref is None
    pat = parse_dila_xml((FIX / "MD142652806_20261030.xml").read_bytes())
    assert pat is not None and (pat.diffuser, pat.type_code, pat.language) == ("142", "001007", "fr")
    assert pat.pdf_path == "142/2026/10/FC142652806_20261030.pdf"


def test_parse_dila_xml_rejects_other_documents():
    assert parse_dila_xml(b"<html/>") is None
    assert parse_dila_xml(b"<UniteInformation") is None
    assert parse_dila_xml(b'<UniteInformation UIN_IDT_UIN="1_20260101"/>') is None  # no diffuser


def _package(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def test_read_package_reads_xml_only(tmp_path):
    xml = (FIX / "MDECO083574_20261028.xml").read_bytes()
    p = _package(tmp_path / "amf-gz_30102026_2200.tar.gz", {
        "x/xml/ECO/2026/10/MDECO083574_20261028.xml": xml,
        "x/pdf/ECO/2026/10/FCECO083574_20261028.pdf": b"%PDF-1.4 not read",
        "x/xml/ECO/2026/10/broken.xml": b"<oops",
    })  # fmt: skip
    got = read_package(p)
    assert (got.xml_members, got.unreadable, len(got.releases)) == (2, 1, 1)
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(b"not a tarball")
    with pytest.raises(ProviderUnavailable):
        read_package(bad)


def test_iter_packages_deletes_each_file_and_reports_failures(tmp_path):
    src = _package(tmp_path / "src.tar.gz", {"a.xml": (FIX / "MD142652806_20261030.xml").read_bytes()})
    pk = list_packages(lambda url, params: (FIX / "dila_index.html").read_bytes())[:2]
    work = tmp_path / "work"
    work.mkdir()

    def download(url, dest):
        if url.endswith(pk[1].name):
            raise ProviderUnavailable("HTTP 404")
        dest.write_bytes(src.read_bytes())
        return dest.stat().st_size

    out = list(iter_packages(pk, work, download, sleep=lambda s: None))
    assert out[0][1] is not None and len(out[0][1].releases) == 1 and out[0][2] > 0
    assert out[1][1] is None and "404" in (out[1][3] or "")
    assert list(work.iterdir()) == []  # nothing left on disk
