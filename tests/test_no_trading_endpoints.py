"""CLAUDE.md rule 1 / docs/14 §14.1: no trading endpoint, no mutating call to an /orders URL.

Fails if any source file references Saxo's trading endpoints or performs POST/PUT/DELETE on a
URL containing ``/orders``. Extend the forbidden list as new brokers are added.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUBSTRINGS = [
    "trade/v2/orders",
    "trade/v1/orders",
    "/trade/v2/positions",
    "TradeOrders",
    "PlaceOrder",
    "place_order",
    "placeOrder",
]
# Outbound HTTP calls only: FastAPI route decorators (``@router.post("/orders")``) declare *our*
# inbound « ordre saisi » endpoint (docs/09 §9.6) and never reach a broker.
MUTATING_ORDERS_RE = re.compile(r"(?<!@router\.)(?<!@app\.)\b(post|put|delete|patch)\s*\([^)]*/orders", re.I)


def _source_files():
    for p in (ROOT / "app").rglob("*.py"):
        yield p
    for p in (ROOT / "scripts").glob("*"):
        if p.is_file():
            yield p
    for p in (ROOT / "alembic").rglob("*.py"):
        yield p
    for p in (ROOT / "config").glob("*.yaml"):
        yield p


def test_no_trading_endpoint_strings():
    hits = []
    for p in _source_files():
        text = p.read_text(encoding="utf-8", errors="ignore")
        for s in FORBIDDEN_SUBSTRINGS:
            if s in text:
                hits.append(f"{p.relative_to(ROOT)}: {s}")
    assert not hits, "trading endpoint reference found:\n" + "\n".join(hits)


def test_no_mutating_call_on_orders_url():
    hits = []
    for p in _source_files():
        text = p.read_text(encoding="utf-8", errors="ignore")
        for m in MUTATING_ORDERS_RE.finditer(text):
            hits.append(f"{p.relative_to(ROOT)}: {m.group(0)[:60]}")
    assert not hits, "mutating HTTP call on an /orders URL:\n" + "\n".join(hits)


def test_guard_catches_outbound_but_not_inbound_routes():
    assert MUTATING_ORDERS_RE.search('client.post("/trade/v2/orders", json=x)')
    assert MUTATING_ORDERS_RE.search('httpx.delete(f"{base}/port/v1/orders/{oid}")')
    assert not MUTATING_ORDERS_RE.search('@router.post("/orders")')


def test_no_trading_permission_requested():
    """Saxo OAuth scopes / permissions must stay read-only when they appear."""
    for p in _source_files():
        text = p.read_text(encoding="utf-8", errors="ignore").lower()
        assert "trading permission" not in text, p
        assert "allow_trading" not in text, p
