"""Free fundamentals and analyst consensus from Yahoo Finance through ``yfinance`` (docs/22 §22.7):
unofficial and fragile, so every value keeps ``source=yahoo`` and its date, a missing or malformed
field stays missing (never estimated), and calls are throttled by the caller."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.data.base import ProviderUnavailable

# EODHD exchange code → Yahoo suffix
YAHOO_SUFFIX = {
    "PA": "PA", "AS": "AS", "BR": "BR", "XETRA": "DE", "MI": "MI", "MC": "MC", "ST": "ST", "CO": "CO",
    "HE": "HE", "OL": "OL", "LS": "LS", "IR": "IR", "VI": "VI", "WAR": "WA", "LSE": "L", "SW": "SW", "US": "",
}  # fmt: skip
FIELDS = {
    "currency": str, "financialCurrency": str, "revenueGrowth": float, "earningsGrowth": float,
    "operatingMargins": float, "profitMargins": float, "totalDebt": float, "totalCash": float, "ebitda": float,
    "trailingPE": float, "forwardPE": float, "targetMeanPrice": float, "targetLowPrice": float,
    "targetHighPrice": float, "numberOfAnalystOpinions": float, "recommendationKey": str, "sector": str,
    "industry": str, "dividendYield": float,
}  # fmt: skip


def yahoo_symbol(ticker_eodhd: str | None) -> str | None:
    if not ticker_eodhd or "." not in ticker_eodhd:
        return None
    code, ex = ticker_eodhd.rsplit(".", 1)
    if ex not in YAHOO_SUFFIX:
        return None
    suffix = YAHOO_SUFFIX[ex]
    return f"{code.replace('_', '-')}.{suffix}" if suffix else code.replace("_", "-")


def clean(info: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, typ in FIELDS.items():
        v = info.get(k)
        if v is None or v == "" or (isinstance(v, float) and v != v):
            continue
        try:
            out[k] = typ(v)
        except (TypeError, ValueError):
            continue
    return out


def fetch(symbol: str, info_fn: Callable[[str], dict[str, Any]] | None = None) -> dict[str, Any]:
    if info_fn is None:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover
            raise ProviderUnavailable("yfinance non installé") from exc

        def info_fn(sym: str) -> dict[str, Any]:
            return dict(yf.Ticker(sym).info or {})

    try:
        return clean(info_fn(symbol))
    except Exception as exc:  # noqa: BLE001 — unofficial source: an error means « indisponible »
        raise ProviderUnavailable(f"yahoo {symbol}: {exc.__class__.__name__}") from exc
