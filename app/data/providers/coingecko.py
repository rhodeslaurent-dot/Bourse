"""Crypto-assets underlying (docs/22 §22.5 rank 3): daily prices from the public CoinGecko API (free,
no key for the public endpoints). 24/7 market: a day's value is the last price of the UTC day."""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx

from app.data.base import ProviderError

BASE = "https://api.coingecko.com/api/v3"


def daily_prices(coin_id: str, days: int = 400, client: httpx.Client | None = None) -> list[tuple[date, float]]:
    c = client or httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    try:
        r = c.get(
            f"{BASE}/coins/{coin_id}/market_chart", params={"vs_currency": "eur", "days": days, "interval": "daily"}
        )
        if r.status_code >= 400:
            raise ProviderError(f"CoinGecko {r.status_code}")
        out: dict[date, float] = {}
        for ts, price in r.json().get("prices", []):
            out[datetime.fromtimestamp(ts / 1000, tz=UTC).date()] = float(price)
        return sorted(out.items())
    except httpx.HTTPError as exc:
        raise ProviderError(f"CoinGecko indisponible : {exc.__class__.__name__}") from exc
    finally:
        if client is None:
            c.close()
