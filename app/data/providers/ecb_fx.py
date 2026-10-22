"""ECB euro foreign exchange reference rates (free, official): daily at about 16:00 CET on TARGET
days. ``rate`` = units of currency per 1 EUR (pair ``EURxxx``). Read-only, no key."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

import httpx

from app.data.base import ProviderError

BASE = "https://www.ecb.europa.eu/stats/eurofxref"
URLS = {
    "daily": f"{BASE}/eurofxref-daily.xml",
    "90d": f"{BASE}/eurofxref-hist-90d.xml",
    "hist": f"{BASE}/eurofxref-hist.xml",
}


@dataclass(frozen=True)
class FxPoint:
    day: date
    currency: str
    rate: float  # per 1 EUR


def parse(xml_text: str) -> list[FxPoint]:
    root = ET.fromstring(xml_text)
    out: list[FxPoint] = []
    for cube in root.iter():
        t = cube.attrib.get("time")
        if not t:
            continue
        d = date.fromisoformat(t)
        for c in cube:
            cur, rate = c.attrib.get("currency"), c.attrib.get("rate")
            if cur and rate:
                out.append(FxPoint(d, cur, float(rate)))
    return out


def fetch(period: str = "daily", client: httpx.Client | None = None) -> list[FxPoint]:
    c = client or httpx.Client(timeout=30)
    try:
        r = c.get(URLS[period])
        if r.status_code >= 400:
            raise ProviderError(f"BCE {r.status_code}")
        return parse(r.text)
    except httpx.HTTPError as exc:
        raise ProviderError(f"BCE indisponible : {exc.__class__.__name__}") from exc
    finally:
        if client is None:
            c.close()
