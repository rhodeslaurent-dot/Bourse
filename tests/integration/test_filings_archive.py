"""Lot L1 (ADR-017) — archive of the AMF regulated releases: API month by month, idempotent; the DILA packages
mark what the API already had and check that both give the same Paris time; packages read once; nothing before
2015-10-01; coverage flags a month far below the same month of the other years.

The API records are the real ones of 12/04/2012 moved to 2016 (the archive refuses 2012); the DILA notices are built
from the real notice of 28/10/2026 with the numbers of those records (synthetic, said so here)."""

from __future__ import annotations

import io
import json
import tarfile
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.data.base import ProviderUnavailable
from app.db.models import OfficialFiling, OfficialFilingPackage
from app.db.session import db_session
from app.domain.timeutil import from_store
from app.services import filings

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "amf_flow"


def _records_2016() -> list[dict]:
    out = []
    for line in (FIX / "api_records_2012.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        for k in ("uin_dat_amf", "uin_dat_mar"):
            rec[k] = rec[k].replace("2012-04-12", "2016-04-12")
        rec["uin_idt_uin"] = rec["uin_idt_uin"].replace("20120412", "20160412")
        out.append(rec)
    return out


def _api_fetch(by_month: dict[str, list[dict]], calls: list[str]):
    def fetch(url, params):
        assert url.endswith("/exports/jsonl")
        month = params["where"].split("'")[1][:7]
        calls.append(month)
        return "".join(json.dumps(r) + "\n" for r in by_month.get(month, [])).encode()

    return fetch


def test_api_archive_stores_once_with_paris_time(db_engine, config):
    recs = _records_2016()
    bad = {**recs[0], "uin_idt_uin": "109685_20160412", "identificationsociete_iso_cd_isi": "999999999999",
           "uin_dat_mar": "8888-01-01T00:00:00+00:00", "informationdeposee_inf_stp_pri": "004001",
           "informationdeposee_inf_stp_inf": '["004001"]'}  # fmt: skip
    calls: list[str] = []
    fetch = _api_fetch({"2016-04": [*recs, bad, recs[0]]}, calls)
    with db_session() as s:
        rep = filings.archive_api(s, config, date(2016, 3, 15), date(2016, 4, 30), fetch=fetch, sleep=lambda x: None)
        assert calls == ["2016-03", "2016-04"]
        assert (rep.read, rep.added, rep.known) == (4, 3, 1)  # the repeated record is not stored twice
        assert rep.unknown_codes == {"004001": 1} and rep.bad_isin == 1 and rep.amf_time_used == 1
        rows = {r.uin_id: r for r in s.scalars(select(OfficialFiling))}
        r = rows["109683_20160412"]
        # 16:30:31 Paris (summer time) = 14:30:31 UTC, whatever « +00:00 » says
        assert from_store(r.market_timestamp) == datetime(2016, 4, 12, 14, 30, 31, tzinfo=UTC)
        assert (r.source, r.family, r.isin, r.time_source, r.tz_label, r.tz_label_ok) == (
            "info_financiere", "droits_de_vote", "FR0000120107", "marche", "+02:00", True)  # fmt: skip
        assert r.data_status == "eod" and r.received_at and r.processed_at and r.also_in is None
        b = rows["109685_20160412"]
        assert b.isin is None and b.family == "rapport_financier" and b.time_source == "amf"
        # second run: nothing added
        rep2 = filings.archive_api(s, config, date(2016, 4, 1), date(2016, 4, 30), fetch=fetch, sleep=lambda x: None)
        assert (rep2.added, rep2.known) == (0, 4)
        assert s.scalar(select(func.count()).select_from(OfficialFiling)) == 3
        # a row stored before its code was listed gets its family on the next read
        b.family = "autre"
        s.flush()
        filings.archive_api(s, config, date(2016, 4, 1), date(2016, 4, 30), fetch=fetch, sleep=lambda x: None)
        assert b.family == "rapport_financier"


def _notice(uin: str, dif: str, mar: str) -> bytes:
    xml = (FIX / "MDECO083574_20261028.xml").read_text(encoding="utf-8")
    xml = xml.replace('UIN_IDT_UIN="083574_20261028"', f'UIN_IDT_UIN="{uin}"')
    xml = xml.replace('IDI_COD_DIF="ECO"', f'IDI_COD_DIF="{dif}"')
    xml = xml.replace('UIN_DAT_MAR="2026-10-28T08:00:09+02:00"', f'UIN_DAT_MAR="{mar}"')
    return xml.encode()


def _tar(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_dila_marks_the_api_rows_checks_the_time_and_reads_each_package_once(db_engine, config):
    with db_session() as s:
        filings.archive_api(s, config, date(2016, 4, 1), date(2016, 4, 30),
                            fetch=_api_fetch({"2016-04": _records_2016()}, []), sleep=lambda x: None)  # fmt: skip
        packages = {
            "amf-gz_12042016_1905.tar.gz": _tar({
                "x/xml/CNS/2016/04/a.xml": _notice("109683_20160412", "CNS", "2016-04-12T16:30:31+02:00"),
                "x/xml/CNS/2016/04/b.xml": _notice("109684_20160412", "CNS", "2016-04-12T18:33:07+02:00"),
                "x/pdf/CNS/2016/04/a.pdf": b"%PDF",
            }),
            "amf-gz_13042016_0800.tar.gz": _tar({"x/xml/ECO/2016/04/c.xml": _notice("000001_20160413", "ECO",
                                                                                  "2016-04-13T07:00:00+02:00")}),
            "amf-gz_14042016_0800.tar.gz": b"",  # fails to download
            "amf-gz_20052016_0800.tar.gz": _tar({}),  # outside the period
        }  # fmt: skip
        index = "".join(f'<a href="{n}">{n}</a>' for n in packages).encode()
        got: list[str] = []

        def download(url, dest):
            name = url.rsplit("/", 1)[1]
            got.append(name)
            if not packages[name]:
                raise ProviderUnavailable("HTTP 503")
            dest.write_bytes(packages[name])
            return len(packages[name])

        kw = dict(fetch=lambda url, params: index, download=download, sleep=lambda x: None)
        rep = filings.archive_dila(s, config, date(2016, 4, 12), date(2016, 4, 13), **kw)
        assert got == ["amf-gz_12042016_1905.tar.gz", "amf-gz_13042016_0800.tar.gz", "amf-gz_14042016_0800.tar.gz"]
        assert (rep.read, rep.added, rep.also_in, rep.same_time, rep.different_time) == (3, 1, 2, 1, 1)
        assert rep.packages_read == 2 and rep.packages_failed == ["amf-gz_14042016_0800.tar.gz"]
        rows = {r.uin_id: r for r in s.scalars(select(OfficialFiling))}
        assert rows["109683_20160412"].also_in == "dila" and rows["109683_20160412"].also_in_time_ok is True
        assert rows["109684_20160412"].also_in_time_ok is False  # 18:33 against 17:33: the rule would be wrong
        assert rows["109684_20160412"].source == "info_financiere"  # the first reader keeps the row
        new = rows["000001_20160413"]
        assert new.source == "dila" and new.lei == "9695006LOD5B2D7Y0N70" and new.tz_label_ok is True
        assert from_store(new.amf_timestamp) == datetime(2026, 10, 30, 21, 0, 37, tzinfo=UTC)  # label +02:00 wrong
        assert set(s.scalars(select(OfficialFilingPackage.name))) == {
            "amf-gz_12042016_1905.tar.gz", "amf-gz_13042016_0800.tar.gz"}  # fmt: skip
        # next run: only the failed package is tried again
        got.clear()
        rep2 = filings.archive_dila(s, config, date(2016, 4, 12), date(2016, 4, 13), **kw)
        assert got == ["amf-gz_14042016_0800.tar.gz"] and rep2.packages_already == 2
        got.clear()
        filings.archive_dila(s, config, date(2016, 4, 12), date(2016, 4, 13), again=True, max_packages=1, **kw)
        assert got == ["amf-gz_12042016_1905.tar.gz"]


def test_nothing_before_the_reserved_period(db_engine, config):
    with db_session() as s, pytest.raises(ValueError, match="2007-2015"):
        filings.archive_api(s, config, date(2015, 9, 30), date(2016, 1, 31), fetch=lambda u, p: b"")
    from app.cli import main

    assert main(["--params", "config/params.example.yaml", "filings-archive", "--from", "2012-01-01"]) == 2


def test_coverage_flags_a_poor_month(db_engine, config):
    now = datetime.now(UTC)
    with db_session() as s:
        for y, n in ((2019, 4), (2020, 4), (2021, 5), (2022, 1), (2023, 4)):
            for i in range(n):
                ts = datetime(y, 3, 10, 7, 0, tzinfo=UTC)
                s.add(OfficialFiling(source="info_financiere", diffuser="CNS", uin_id=f"{y}{i:04d}", family="resultats",
                                     type_code="001007", market_timestamp=ts, amf_timestamp=ts, time_source="marche",
                                     time_precision="seconde", tz_label_ok=True, data_status="eod", received_at=now,
                                     processed_at=now))  # fmt: skip
        s.flush()
        text = filings.coverage(s, config, date(2019, 1, 1), date(2023, 12, 31))
    assert "mois non couverts : 2022-03" in text
    assert "     1!" in text and "18 communiqué(s) en base" in text
    assert "résultats 18" in text and "étiquette de fuseau fausse 0 sur 18" in text
    assert "BONGRAIN" not in text  # aggregates only
