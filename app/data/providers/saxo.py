"""Saxo OpenAPI — **read-only** (CLAUDE.md rule 1, docs/04 §4.5).

Allowed: ``port/v1/positions``, ``port/v1/orders`` (GET), ``port/v1/balances``,
``port/v1/closedpositions``, ``trade/v1/infoprices`` (+ its streaming subscriptions),
``chart/v1/charts``, ``ref/v1/instruments``. Any other path, or any non-GET call except the
price-subscription endpoints, is refused by :class:`SaxoClient` itself before reaching the network.
The refresh token is stored encrypted (Fernet, key ``SAXO_TOKEN_KEY`` in ``.env``).
"""

from __future__ import annotations

import logging
import os
import posixpath
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.data.base import MarketDataProvider, ProviderError
from app.data.dto import DataStatus, EodBar, IntradayBar, MarketPerimeter, PriceType, Quote, Timeframe, classify_status

log = logging.getLogger("bourse.data.saxo")

LIVE_BASE = "https://gateway.saxobank.com/openapi"
SIM_BASE = "https://gateway.saxobank.com/sim/openapi"
LIVE_AUTH = "https://live.logonvalidation.net"
SIM_AUTH = "https://sim.logonvalidation.net"

READ_ONLY_PREFIXES = (
    "/port/v1/positions", "/port/v1/orders", "/port/v1/balances", "/port/v1/closedpositions", "/port/v1/accounts",
    "/port/v1/clients", "/port/v1/netpositions", "/trade/v1/infoprices", "/chart/v1/charts", "/ref/v1/instruments",
    "/ref/v1/exchanges",
)  # fmt: skip
# The only non-GET calls allowed: creating/removing *price* streaming subscriptions.
MUTATING_ALLOWLIST = ("/trade/v1/infoprices/subscriptions", "/chart/v1/charts/subscriptions")


class ReadOnlyViolation(ProviderError):
    """Raised before any network call when a request could mutate account state."""


def _check_read_only(method: str, path: str) -> None:
    m = method.upper()
    path = posixpath.normpath(path)  # ``..`` segments are resolved before the check (m24)
    if not path.startswith(READ_ONLY_PREFIXES):
        raise ReadOnlyViolation(f"endpoint Saxo non autorisé (lecture seule) : {path}")
    if m != "GET" and not path.startswith(MUTATING_ALLOWLIST):
        raise ReadOnlyViolation(f"méthode {m} interdite sur {path} (lecture seule)")
    if "/orders" in path and m != "GET":
        raise ReadOnlyViolation("aucune écriture sur un endpoint d'ordres, jamais")


def is_live() -> bool:
    """``SAXO_ENV=sim`` selects the simulation gateway; anything else is live (read-only either way)."""
    return os.environ.get("SAXO_ENV", "live").lower() != "sim"


class TokenStore:
    """Encrypted refresh-token persistence (``SAXO_TOKEN_KEY`` = Fernet key); one file per environment."""

    def __init__(self, path: str | None = None, key: str | None = None, live: bool | None = None) -> None:
        live = is_live() if live is None else live
        default = "data/saxo_token.enc" if live else "data/saxo_token.sim.enc"
        self.path = path or os.environ.get("SAXO_TOKEN_PATH" if live else "SAXO_SIM_TOKEN_PATH", default)
        self.key = key or os.environ.get("SAXO_TOKEN_KEY", "")

    def _fernet(self):  # type: ignore[no-untyped-def]
        from cryptography.fernet import Fernet

        if not self.key:
            raise ProviderError("SAXO_TOKEN_KEY absent (clé Fernet pour chiffrer le refresh token)")
        return Fernet(self.key.encode())

    def save(self, refresh_token: str) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "wb") as fh:
            fh.write(self._fernet().encrypt(refresh_token.encode()))

    def load(self) -> str | None:
        if not os.path.exists(self.path):
            return None
        with open(self.path, "rb") as fh:
            return self._fernet().decrypt(fh.read()).decode()


class SaxoClient:
    def __init__(
        self,
        client: httpx.Client | None = None,
        live: bool | None = None,
        access_token: str | None = None,
        token_store: TokenStore | None = None,
    ) -> None:
        live = is_live() if live is None else live
        self.live = live
        self.base = LIVE_BASE if live else SIM_BASE
        self.auth_base = LIVE_AUTH if live else SIM_AUTH
        self.client = client or httpx.Client(timeout=30)
        self.access_token = access_token or os.environ.get("SAXO_ACCESS_TOKEN")
        self.token_expiry = 0.0
        self.store = token_store or TokenStore(live=live)
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        _check_read_only(method, path)
        self.calls.append((method.upper(), path))
        self._ensure_token()
        headers = {"Authorization": f"Bearer {self.access_token}", **kwargs.pop("headers", {})}
        r = self.client.request(method, f"{self.base}{path}", headers=headers, **kwargs)
        if r.status_code == 429:
            raise ProviderError(f"Saxo rate limit: {r.headers.get('X-RateLimit-AppDay-Remaining', '?')} restants")
        if r.status_code >= 400:
            raise ProviderError(f"Saxo {r.status_code} on {path}: {r.text[:160]}")
        return r.json() if r.content else {}

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params)

    # --- OAuth ------------------------------------------------------------------------------
    def _ensure_token(self) -> None:
        if self.access_token and (self.token_expiry == 0.0 or time.time() < self.token_expiry - 60):
            return
        refresh = self.store.load()
        if not refresh:
            raise ProviderError("aucun jeton Saxo : lancer `python -m app.cli saxo-auth` (Authorization Code)")
        self.refresh(refresh)

    def refresh(self, refresh_token: str) -> None:
        r = self.client.post(
            f"{self.auth_base}/token",
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=(os.environ.get("SAXO_APP_KEY", ""), os.environ.get("SAXO_APP_SECRET", "")),
        )
        if r.status_code >= 400:
            raise ProviderError(f"Saxo refresh failed: {r.status_code}")
        data = r.json()
        self.access_token = data["access_token"]
        self.token_expiry = time.time() + int(data.get("expires_in", 1200))
        if data.get("refresh_token"):
            self.store.save(data["refresh_token"])

    # --- portfolio (read) -------------------------------------------------------------------
    def positions(self, client_key: str) -> list[dict[str, Any]]:
        return self.get(
            "/port/v1/positions", ClientKey=client_key, FieldGroups="PositionBase,PositionView,DisplayAndFormat"
        ).get("Data", [])

    def open_orders(self, client_key: str) -> list[dict[str, Any]]:
        return self.get("/port/v1/orders", ClientKey=client_key, FieldGroups="DisplayAndFormat").get("Data", [])

    def balances(self, client_key: str, account_key: str | None = None) -> dict[str, Any]:
        params = {"ClientKey": client_key}
        if account_key:
            params["AccountKey"] = account_key
        return self.get("/port/v1/balances", **params)

    def me(self) -> dict[str, Any]:
        """``port/v1/clients/me`` → ClientKey, DefaultAccountKey, Name (read)."""
        return self.get("/port/v1/clients/me")

    def closed_positions(self, client_key: str) -> list[dict[str, Any]]:
        return self.get("/port/v1/closedpositions", ClientKey=client_key).get("Data", [])


class SaxoPriceProvider(MarketDataProvider):
    name = "saxo"
    declared_delay_minutes = 0  # realtime only when subscribed (field DelayedByMinutes tells the truth)

    def __init__(self, client: SaxoClient, stale_after: dict[str, int] | None = None) -> None:
        self.client = client
        self.stale_after = stale_after or {"realtime": 3, "delayed": 20}

    def fetch_quotes(self, symbols: Sequence[str]) -> list[Quote]:
        """``symbols`` are Saxo Uics (as strings). ``DelayedByMinutes`` decides the status."""
        if not symbols:
            return []
        data = self.client.get(
            "/trade/v1/infoprices/list",
            Uics=",".join(symbols),
            AssetType="Stock",
            FieldGroups="Quote,DisplayAndFormat,PriceInfoDetails",
        )
        now = datetime.now(UTC)
        out = []
        for item in data.get("Data", []):
            q = item.get("Quote", {})
            delay = int(q.get("DelayedByMinutes") or 0)
            ts = item.get("LastUpdated")
            mts = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
            status = classify_status(mts, now, delay, self.stale_after)
            if q.get("PriceTypeAsk") == "NoAccess" or q.get("Mid") is None and q.get("Ask") is None:
                status = DataStatus.UNAVAILABLE
            details = item.get("PriceInfoDetails", {})
            out.append(
                Quote(
                    source=self.name,
                    market_perimeter=MarketPerimeter.PRIMARY,
                    received_at=now,
                    processed_at=datetime.now(UTC),
                    data_status=status,
                    symbol=str(item.get("Uic")),
                    market_timestamp=mts,
                    price_type=PriceType.LAST if details.get("LastTraded") is not None else PriceType.BID,
                    last=details.get("LastTraded"),
                    bid=q.get("Bid"),
                    ask=q.get("Ask"),
                    volume_cum=int(details["Volume"]) if details.get("Volume") is not None else None,
                    currency=item.get("DisplayAndFormat", {}).get("Currency"),
                    declared_delay_minutes=delay,
                )
            )
        return out

    def fetch_eod(self, symbols: Sequence[str], start: date, end: date) -> list[EodBar]:
        raise ProviderError("Saxo n'est pas retenu pour l'EOD (EODHD primaire)")

    def fetch_intraday(self, symbol: str, day: date, tf: Timeframe = Timeframe.M5) -> list[IntradayBar]:
        horizon = {Timeframe.M1: 1, Timeframe.M5: 5, Timeframe.D1: 1440}[tf]
        data = self.client.get("/chart/v1/charts", Uic=symbol, AssetType="Stock", Horizon=horizon, Count=1200)
        now = datetime.now(UTC)
        return [
            IntradayBar(
                source=self.name,
                market_perimeter=MarketPerimeter.PRIMARY,
                received_at=now,
                processed_at=datetime.now(UTC),
                data_status=DataStatus.DELAYED if data.get("DataVersion") is None else DataStatus.REALTIME,
                symbol=symbol,
                market_timestamp=datetime.fromisoformat(r["Time"].replace("Z", "+00:00")),
                tf=tf,
                open=float(r["Open"]),
                high=float(r["High"]),
                low=float(r["Low"]),
                close=float(r["Close"]),
                volume=int(r.get("Volume") or 0),
            )
            for r in data.get("Data", [])
            if datetime.fromisoformat(r["Time"].replace("Z", "+00:00")).date() == day
        ]

    def supports_streaming(self) -> bool:
        return True


# --- portfolio mapping (read) --------------------------------------------------------------


@dataclass
class BrokerPositionDto:
    position_id: str
    uic: str
    isin: str | None
    symbol: str
    qty: int
    open_price: float
    opened_at: datetime
    current_price: float | None
    currency: str
    account_key: str
    srd: bool


@dataclass
class BrokerOrderDto:
    order_id: str
    uic: str
    isin: str | None
    side: str
    qty: int
    filled: int
    order_type: str  # Limit | Stop | StopLimit | TrailingStop | Market
    price: float | None
    stop_price: float | None
    status: str
    account_key: str


def _iso(ts: str | None) -> datetime:
    if not ts:
        return datetime.now(UTC)
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


class SaxoPortfolio:
    """Maps Saxo read endpoints to broker DTOs; ISIN resolved via ``ref/v1/instruments/details``."""

    def __init__(self, client: SaxoClient, client_key: str | None = None) -> None:
        self.client = client
        self.client_key = client_key or os.environ.get("SAXO_CLIENT_KEY", "")
        self._isin_cache: dict[str, str | None] = {}
        self.isin_errors: dict[str, str] = {}  # uic → reason (diagnostics for saxo-check)

    def isin_of(self, uic: str, asset_type: str = "Stock") -> str | None:
        """ISIN from ``ref/v1/instruments/details/{Uic}/{AssetType}``; tries the position's asset
        type first, then the common ones. Never guesses: ``None`` and a recorded reason otherwise."""
        if uic in self._isin_cache:
            return self._isin_cache[uic]
        tried = []
        for at in dict.fromkeys([asset_type or "Stock", "Stock", "SrdStock", "Etf", "Rights"]):
            try:
                data = self.client.get(f"/ref/v1/instruments/details/{uic}/{at}")
            except ProviderError as exc:
                tried.append(f"{at}: {str(exc)[:80]}")
                continue
            isin = data.get("Isin") or data.get("ISIN")
            if isin:
                self._isin_cache[uic] = str(isin)
                return self._isin_cache[uic]
            tried.append(f"{at}: réponse sans champ Isin (clés : {', '.join(sorted(data)[:12])})")
        self._isin_cache[uic] = None
        self.isin_errors[uic] = " | ".join(tried)
        return None

    def positions(self) -> list[BrokerPositionDto]:
        out = []
        for p in self.client.positions(self.client_key):
            base, view, disp = p.get("PositionBase", {}), p.get("PositionView", {}), p.get("DisplayAndFormat", {})
            uic = str(base.get("Uic"))
            out.append(
                BrokerPositionDto(
                    position_id=str(p.get("PositionId")),
                    uic=uic,
                    isin=self.isin_of(uic),
                    symbol=disp.get("Symbol", uic),
                    qty=int(base.get("Amount") or 0),
                    open_price=float(base.get("OpenPrice") or 0),
                    opened_at=_iso(base.get("ExecutionTimeOpen")),
                    current_price=view.get("CurrentPrice"),
                    currency=disp.get("Currency", "EUR"),
                    account_key=str(base.get("AccountKey", "")),
                    srd=bool(base.get("IsMarketOpen", True)) and str(base.get("AssetType", "")).lower() == "srdstock",
                )
            )
        return out

    def orders(self) -> list[BrokerOrderDto]:
        out = []
        for o in self.client.open_orders(self.client_key):
            uic = str(o.get("Uic"))
            out.append(
                BrokerOrderDto(
                    order_id=str(o.get("OrderId")),
                    uic=uic,
                    isin=self.isin_of(uic),
                    side="buy" if str(o.get("BuySell", "")).lower() == "buy" else "sell",
                    qty=int(o.get("Amount") or 0),
                    filled=int(o.get("FilledAmount") or 0),
                    order_type=str(o.get("OpenOrderType", "")),
                    price=o.get("Price"),
                    stop_price=o.get("StopLimitPrice")
                    or (o.get("Price") if "Stop" in str(o.get("OpenOrderType", "")) else None),
                    status=str(o.get("Status", "")),
                    account_key=str(o.get("AccountKey", "")),
                )
            )
        return out

    def closed_fills(self) -> list[dict[str, Any]]:
        """Closed positions → sell fills (ClosingPositionId, ClosingPrice, ExecutionTimeClose)."""
        out = []
        for c in self.client.closed_positions(self.client_key):
            cp = c.get("ClosedPosition", c)
            uic = str(cp.get("Uic"))
            out.append(
                {
                    "fill_id": str(
                        cp.get("ClosingPositionId")
                        or c.get("ClosedPositionUniqueId")
                        or f"{cp.get('OpeningPositionId')}-close"
                    ),
                    "isin": self.isin_of(uic),
                    "qty": abs(int(cp.get("Amount") or 0)),
                    "price": float(cp.get("ClosingPrice") or 0),
                    "ts": _iso(cp.get("ExecutionTimeClose")),
                    "currency": c.get("DisplayAndFormat", {}).get("Currency", "EUR"),
                    "srd": str(cp.get("AssetType", "")).lower() == "srdstock",
                }
            )
        return out

    def cash(self) -> float | None:
        b = self.client.balances(self.client_key)
        return float(b["CashBalance"]) if b.get("CashBalance") is not None else None
