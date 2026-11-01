"""Lot L1 (docs/36 §36.9, ADR-017) — the AMF flow of regulated releases, as the DILA publishes it, read-only.

Two public sources, no key, no account:

* **API of info-financiere.gouv.fr** (Opendatasoft explore v2.1, dataset ``flux-amf-new-prod``, 539 690 records
  on 01/11/2026, 2008 →): one export per month::

      GET https://www.info-financiere.gouv.fr/api/explore/v2.1/catalog/datasets/flux-amf-new-prod/exports/jsonl
          ?select=<fields>&where=uin_dat_amf >= date'2016-01-01' and uin_dat_amf < date'2016-02-01'&timezone=UTC

  and, if the export is refused, the records of each day 100 at a time (``/records``, offset + limit ≤ 10 000);
* **packages of the DILA** (``https://echanges.dila.gouv.fr/OPENDATA/AMF/amf-gz_DDMMYYYY_HHMM.tar.gz``, 2019 →):
  one XML file per release (``xml/<diffuseur>/AAAA/MM/MD<diffuseur><numéro>_<date>.xml``) plus its PDF. Only the
  XML members are read, in memory, one by one (nothing is extracted to disk); the package is deleted once read. It
  fills the months the API lacks (2022: 10 001 records in the API against ≈ 25 000 the other years).

Only metadata are kept: issuer, ISIN, LEI, ticker, type codes, title, language, times as written, PDF path. No PDF
is downloaded. Times stay raw here; ``app.domain.filings.paris_wall_clock`` reads them (the labels are unreliable).
"""

from __future__ import annotations

import json
import re
import tarfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.data.base import ProviderUnavailable

SOURCE_API = "info_financiere"
SOURCE_DILA = "dila"
API_BASE = "https://www.info-financiere.gouv.fr"
DATASET = "flux-amf-new-prod"
DILA_BASE = "https://echanges.dila.gouv.fr/OPENDATA/AMF/"
UA = "bourse-pilot/1.0 (outil personnel, archive des communiques officiels, 1 requete/s)"
PAGE = 100  # records per page of the fallback
MAX_OFFSET = 10_000  # offset + limit of a records query
MAX_XML_BYTES = 2_000_000  # an XML member larger than this is not a release notice: skipped and counted
MAX_PACKAGE_BYTES = 3_000_000_000

Fetch = Callable[[str, dict[str, Any] | None], bytes]
Download = Callable[[str, Path], int]

API_FIELDS = (
    "uin_idt_uin", "uin_dat_amf", "uin_dat_mar", "timezone_uin_dat_amf", "identificationdiffuseur_idi_cod_dif",
    "identificationsociete_iso_nom_soc", "identificationsociete_iso_cd_isi", "identificationsociete_iso_cd_lei",
    "identificationsociete_iso_code_tkr_iso_cd_tkr", "informationdeposee_inf_stp_pri",
    "informationdeposee_inf_stp_inf", "informationdeposee_inf_tit_inf", "informationdeposee_inf_lng_inf",
    "informationdeposee_inf_cod_dif", "informationdeposee_inf_upg_inf_inf_upg_sts",
    "informationdeposee_inf_upg_inf_inf_upg_rel", "fichierdecontenu_inf_fic_nom", "sous_type_d_information",
)  # fmt: skip

_PACKAGE = re.compile(r'href="(amf-gz_(\d{2})(\d{2})(\d{4})_(\d{4})\.tar\.gz)"')
_LANG = {"francais": "fr", "français": "fr", "fr": "fr", "fre": "fr", "anglais": "en", "en": "en", "eng": "en"}


class OfficialRelease(BaseModel):
    source: str
    diffuser: str
    uin_id: str
    issuer_name: str | None = None
    isin: str | None = None  # as written; checked by the caller (app.domain.filings.valid_isin)
    lei: str | None = None
    ticker: str | None = None
    type_code: str | None = None
    type_codes: list[str] = []
    subtype_label: str | None = None  # API only (the DILA XML carries the code only)
    title: str | None = None
    language: str | None = None
    disclosure: str | None = None  # OBL | OPT | AMF
    update_status: str | None = None  # NEW | REC (rectificatif) | ADD (complément)
    update_ref: str | None = None
    market_raw: str | None = None  # UIN_DAT_MAR as written: release to the market
    amf_raw: str | None = None  # UIN_DAT_AMF as written: filing at the AMF
    tz_label: str | None = None  # API: the separate offset field; DILA: None (the offset is in the text)
    pdf_path: str | None = None


# ---------------------------------------------------------------------------------------------------------------
# shared helpers


def _txt(v: Any, limit: int | None = None) -> str | None:
    if v is None:
        return None
    s = " ".join(str(v).split())
    if not s:
        return None
    return s[:limit] if limit else s


def _lang(v: Any) -> str | None:
    s = _txt(v)
    if s is None:
        return None
    return _LANG.get(s.lower(), s.lower()[:8])


def _codes(v: Any) -> list[str]:
    """INF_STP_INF: a JSON list in a string (API) or a list (DILA); unique, order kept."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            v = [v]
    out: list[str] = []
    for c in v if isinstance(v, list) else []:
        s = _txt(c, 6)
        if s and s not in out:
            out.append(s)
    return out


class Throttle:
    def __init__(self, interval_s: float, sleep: Callable[[float], None]) -> None:
        self.interval = interval_s
        self.sleep = sleep
        self.last: float | None = None

    def wait(self) -> None:
        now = time.monotonic()
        if self.last is not None and self.interval > 0:
            gap = self.interval - (now - self.last)
            if gap > 0:
                self.sleep(gap)
        self.last = time.monotonic()


def http_fetch(timeout: float = 120.0) -> Fetch:
    import httpx

    client = httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": UA})

    def get(url: str, params: dict[str, Any] | None) -> bytes:
        try:
            r = client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"source injoignable : {exc.__class__.__name__}") from exc
        if r.status_code >= 400:
            raise ProviderUnavailable(f"HTTP {r.status_code} sur {url}")
        return r.content

    return get


def http_download(timeout: float = 300.0) -> Download:
    import httpx

    client = httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": UA})

    def download(url: str, dest: Path) -> int:
        n = 0
        try:
            with client.stream("GET", url) as r:
                if r.status_code >= 400:
                    raise ProviderUnavailable(f"HTTP {r.status_code} sur {url}")
                with dest.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        n += len(chunk)
                        if n > MAX_PACKAGE_BYTES:
                            raise ProviderUnavailable(f"paquet de plus de {MAX_PACKAGE_BYTES} octets : {url}")
                        f.write(chunk)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"téléchargement interrompu : {exc.__class__.__name__}") from exc
        return n

    return download


# ---------------------------------------------------------------------------------------------------------------
# API info-financiere


def parse_api_record(rec: dict[str, Any]) -> OfficialRelease | None:
    """One record of the API → OfficialRelease; None without diffuser or number (counted by the caller)."""
    dif = _txt(rec.get("identificationdiffuseur_idi_cod_dif"), 8)
    uin = _txt(rec.get("uin_idt_uin"), 32)
    if not dif or not uin:
        return None
    return OfficialRelease(
        source=SOURCE_API, diffuser=dif, uin_id=uin,
        issuer_name=_txt(rec.get("identificationsociete_iso_nom_soc"), 200),
        isin=_txt(rec.get("identificationsociete_iso_cd_isi"), 32),
        lei=_txt(rec.get("identificationsociete_iso_cd_lei"), 20),
        ticker=_txt(rec.get("identificationsociete_iso_code_tkr_iso_cd_tkr"), 16),
        type_code=_txt(rec.get("informationdeposee_inf_stp_pri"), 6),
        type_codes=_codes(rec.get("informationdeposee_inf_stp_inf")),
        subtype_label=_txt(rec.get("sous_type_d_information"), 160),
        title=_txt(rec.get("informationdeposee_inf_tit_inf"), 2000),
        language=_lang(rec.get("informationdeposee_inf_lng_inf")),
        disclosure=_txt(rec.get("informationdeposee_inf_cod_dif"), 4),
        update_status=_txt(rec.get("informationdeposee_inf_upg_inf_inf_upg_sts"), 4),
        update_ref=_txt(rec.get("informationdeposee_inf_upg_inf_inf_upg_rel"), 40),
        market_raw=_txt(rec.get("uin_dat_mar"), 40),
        amf_raw=_txt(rec.get("uin_dat_amf"), 40),
        tz_label=_txt(rec.get("timezone_uin_dat_amf"), 8),
        pdf_path=_txt(rec.get("fichierdecontenu_inf_fic_nom"), 200),
    )  # fmt: skip


def _where(lo: date, hi: date) -> str:
    """Filing dates in [lo, hi) — the API stores the Paris wall clock, so these are Paris days."""
    return f"uin_dat_amf >= date'{lo.isoformat()}' and uin_dat_amf < date'{hi.isoformat()}'"


@dataclass
class ApiBatch:
    releases: list[OfficialRelease] = field(default_factory=list)
    skipped: int = 0  # records without diffuser or number
    mode: str = "export"  # export | records (day by day)
    truncated_days: list[str] = field(default_factory=list)  # more than 10 000 records in a day: not all read
    notes: list[str] = field(default_factory=list)


def _parse_jsonl(payload: bytes) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    text = payload.decode("utf-8")
    if text.lstrip().startswith(("<", '{"error')):
        raise ProviderUnavailable("l'export a renvoyé une erreur au lieu des communiqués")
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError as exc:
            raise ProviderUnavailable("export illisible (ligne JSON invalide)") from exc
        if not isinstance(obj, dict):
            raise ProviderUnavailable("export illisible (ligne non objet)")
        if "error_code" in obj:
            raise ProviderUnavailable(f"export refusé : {obj.get('message', obj['error_code'])}")
        out.append(obj)
    return out


def fetch_api_month(
    year: int,
    month: int,
    fetch: Fetch | None = None,
    base_url: str = API_BASE,
    min_interval_s: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    throttle: Throttle | None = None,
) -> ApiBatch:
    """The releases filed during one Paris month: one export; if it is refused or unreadable, day by day."""
    get = fetch or http_fetch()
    th = throttle or Throttle(max(min_interval_s, 1.0), sleep)
    lo = date(year, month, 1)
    hi = date(year + (month == 12), month % 12 + 1, 1)
    root = f"{base_url.rstrip('/')}/api/explore/v2.1/catalog/datasets/{DATASET}"
    batch = ApiBatch()
    select = ",".join(API_FIELDS)
    rows: list[dict[str, Any]] | None
    try:
        th.wait()
        rows = _parse_jsonl(
            get(f"{root}/exports/jsonl", {"select": select, "where": _where(lo, hi), "timezone": "UTC"})
        )
    except ProviderUnavailable as exc:
        batch.notes.append(f"{lo:%Y-%m} : export refusé ({exc}), lecture jour par jour")
        rows = None
    if rows is None:
        batch.mode = "records"
        rows = []
        day = lo
        while day < hi:
            rows.extend(_records_day(day, root, select, get, th, batch))
            day += timedelta(days=1)
    for rec in rows:
        r = parse_api_record(rec)
        if r is None:
            batch.skipped += 1
        else:
            batch.releases.append(r)
    return batch


def _records_day(day: date, root: str, select: str, get: Fetch, th: Throttle, batch: ApiBatch) -> list[dict]:
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        th.wait()
        params = {"select": select, "where": _where(day, day + timedelta(days=1)), "timezone": "UTC",
                  "order_by": "uin_idt_uin", "limit": PAGE, "offset": offset}  # fmt: skip
        try:
            payload = json.loads(get(f"{root}/records", params))
        except ValueError as exc:
            raise ProviderUnavailable(f"réponse illisible pour le {day}") from exc
        if not isinstance(payload, dict) or "results" not in payload:
            raise ProviderUnavailable(f"réponse inattendue pour le {day} : {str(payload)[:120]}")
        res = payload["results"] or []
        out.extend(r for r in res if isinstance(r, dict))
        total = int(payload.get("total_count") or 0)
        offset += PAGE
        if len(res) < PAGE or offset >= total:
            break
        if offset + PAGE > MAX_OFFSET:
            batch.truncated_days.append(day.isoformat())
            break
    return out


# ---------------------------------------------------------------------------------------------------------------
# DILA packages


@dataclass(frozen=True)
class Package:
    name: str
    day: date  # date in the package name
    hhmm: str


def list_packages(fetch: Fetch | None = None, base_url: str = DILA_BASE) -> list[Package]:
    """Every package listed on the DILA index page, in date order."""
    html = (fetch or http_fetch())(base_url, None).decode("utf-8", "replace")
    seen: dict[str, Package] = {}
    for m in _PACKAGE.finditer(html):
        try:
            d = date(int(m.group(4)), int(m.group(3)), int(m.group(2)))
        except ValueError:
            continue
        seen.setdefault(m.group(1), Package(m.group(1), d, m.group(5)))
    return sorted(seen.values(), key=lambda p: (p.day, p.hhmm, p.name))


def parse_dila_xml(data: bytes) -> OfficialRelease | None:
    """One release notice of a DILA package → OfficialRelease; None when it is not one (counted by the caller)."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    if root.tag != "UniteInformation":
        return None
    dif_el = root.find("identificationDiffuseur")
    soc = root.find("identificationSociete")
    inf = root.find("InformationDeposee")
    fic = root.find("FichierDeContenu")
    dif = _txt(dif_el.get("IDI_COD_DIF") if dif_el is not None else None, 8)
    uin = _txt(root.get("UIN_IDT_UIN"), 32)
    if not dif or not uin:
        return None
    tkr = soc.find("ISO_CODE_TKR") if soc is not None else None
    upg = inf.find("INF_UPG_INF") if inf is not None else None
    return OfficialRelease(
        source=SOURCE_DILA, diffuser=dif, uin_id=uin,
        issuer_name=_txt(soc.get("ISO_NOM_SOC") if soc is not None else None, 200),
        isin=_txt(soc.get("ISO_CD_ISI") if soc is not None else None, 32),
        lei=_txt(soc.get("ISO_CD_LEI") if soc is not None else None, 20),
        ticker=_txt(tkr.get("ISO_CD_TKR") if tkr is not None else None, 16),
        type_code=_txt(inf.get("INF_STP_PRI") if inf is not None else None, 6),
        type_codes=_codes([e.text for e in inf.findall("INF_STP_INF")] if inf is not None else []),
        title=_txt(inf.get("INF_TIT_INF") if inf is not None else None, 2000),
        language=_lang(inf.get("INF_LNG_INF") if inf is not None else None),
        disclosure=_txt(inf.get("INF_COD_DIF") if inf is not None else None, 4),
        update_status=_txt(upg.get("INF_UPG_STS") if upg is not None else None, 4),
        update_ref=_txt(upg.get("INF_UPG_REL") if upg is not None else None, 40),
        market_raw=_txt(root.get("UIN_DAT_MAR"), 40),
        amf_raw=_txt(root.get("UIN_DAT_AMF"), 40),
        pdf_path=_txt(fic.get("INF_FIC_NOM") if fic is not None else None, 200),
    )  # fmt: skip


@dataclass
class PackageRead:
    releases: list[OfficialRelease] = field(default_factory=list)
    xml_members: int = 0
    unreadable: int = 0  # XML members that are not a release notice (or too large)


def read_package(path: Path) -> PackageRead:
    """The release notices of a package, read in stream order; PDF members are skipped without being read."""
    out = PackageRead()
    try:
        with tarfile.open(path, mode="r|gz") as tf:
            for m in tf:
                if not m.isfile() or not m.name.lower().endswith(".xml"):
                    continue
                out.xml_members += 1
                if m.size > MAX_XML_BYTES:
                    out.unreadable += 1
                    continue
                fh = tf.extractfile(m)
                rel = parse_dila_xml(fh.read()) if fh is not None else None
                if rel is None:
                    out.unreadable += 1
                else:
                    out.releases.append(rel)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ProviderUnavailable(f"paquet illisible {path.name} : {exc.__class__.__name__}") from exc
    return out


def iter_packages(
    packages: list[Package],
    workdir: Path,
    download: Download | None = None,
    base_url: str = DILA_BASE,
    min_interval_s: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[tuple[Package, PackageRead | None, int, str | None]]:
    """(package, content or None, bytes downloaded, error) for each package; each file is deleted once read."""
    dl = download or http_download()
    th = Throttle(max(min_interval_s, 1.0), sleep)
    for p in packages:
        dest = workdir / p.name
        th.wait()
        try:
            n = dl(base_url.rstrip("/") + "/" + p.name, dest)
            yield p, read_package(dest), n, None
        except ProviderUnavailable as exc:
            yield p, None, 0, str(exc)
        finally:
            dest.unlink(missing_ok=True)
