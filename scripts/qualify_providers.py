"""Qualification by prototype (A0.7) — run during 3 sessions on 10 FR/DE/NL values.

Usage (inside the session, Paris hours):
  uv run python scripts/qualify_providers.py --symbols tests/fixtures/qualification/symbols.yaml \
      --minutes 30 --interval 60 --out data/qualification
Then, once the reference values read in the Saxo interface are saved as
``tests/fixtures/qualification/reference_saxo_<date>.csv`` (isin,at_utc,last,volume):
  uv run python scripts/qualify_providers.py --report data/qualification/<date>.csv

Nothing here writes to a broker; Saxo is read-only (SaxoClient refuses anything else).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.dto import Quote  # noqa: E402
from app.data.qualification import (  # noqa: E402
    Observation,
    Reference,
    compare_to_reference,
    delay_stats,
    gap_summary,
    render_report,
)

FIELDS = [
    "provider",
    "symbol",
    "isin",
    "received_at",
    "market_timestamp",
    "last",
    "volume_cum",
    "data_status",
    "perimeter",
]


def _row(q: Quote, isin: str) -> dict[str, object]:
    return {
        "provider": q.source,
        "symbol": q.symbol,
        "isin": isin,
        "received_at": q.received_at.isoformat(),
        "market_timestamp": q.market_timestamp.isoformat() if q.market_timestamp else "",
        "last": q.last if q.last is not None else "",
        "volume_cum": q.volume_cum if q.volume_cum is not None else "",
        "data_status": q.data_status.value,
        "perimeter": q.market_perimeter.value,
    }


def record(args: argparse.Namespace) -> int:
    symbols = yaml.safe_load(Path(args.symbols).read_text(encoding="utf-8"))["symbols"]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    csv_path = out_dir / f"{day}.csv"
    new = not csv_path.exists()
    providers = []
    from app.data.providers.eodhd import EodhdProvider, EodhdWebSocket

    if os.environ.get("EODHD_API_TOKEN"):
        providers.append(
            ("eodhd_rest", EodhdProvider(), [s["eodhd"] for s in symbols], {s["eodhd"]: s["isin"] for s in symbols})
        )
    if os.environ.get("SAXO_ACCESS_TOKEN") or os.environ.get("SAXO_TOKEN_KEY"):
        from app.data.providers.saxo import SaxoClient, SaxoPriceProvider

        uics = [str(s["saxo_uic"]) for s in symbols if s.get("saxo_uic")]
        providers.append(
            (
                "saxo",
                SaxoPriceProvider(SaxoClient()),
                uics,
                {str(s["saxo_uic"]): s["isin"] for s in symbols if s.get("saxo_uic")},
            )
        )
    if not providers:
        print("aucun provider configuré (EODHD_API_TOKEN / SAXO_*)")
        return 2
    ws = None
    ws_task = None
    stop = asyncio.Event()
    if os.environ.get("EODHD_API_TOKEN") and not args.no_ws:
        ws = EodhdWebSocket()
        ws_symbols = [s["eodhd_ws"] for s in symbols if s.get("eodhd_ws")]
        loop = asyncio.new_event_loop()
        import threading

        ws_task = threading.Thread(target=lambda: loop.run_until_complete(ws.run(ws_symbols, stop)), daemon=True)
        ws_task.start()
    isin_by_ws = {s.get("eodhd_ws"): s["isin"] for s in symbols}
    deadline = time.time() + args.minutes * 60
    with open(csv_path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        while time.time() < deadline:
            for name, prov, syms, isin_of in providers:
                try:
                    for q in prov.fetch_quotes(syms):
                        w.writerow(_row(q, isin_of.get(q.symbol, "")))
                except Exception as exc:  # noqa: BLE001
                    w.writerow(
                        {
                            "provider": name,
                            "symbol": "",
                            "isin": "",
                            "received_at": datetime.now(UTC).isoformat(),
                            "market_timestamp": "",
                            "last": "",
                            "volume_cum": "",
                            "data_status": f"error:{exc.__class__.__name__}",
                            "perimeter": "",
                        }
                    )
            if ws is not None:
                for bar in ws.aggregator.closed:
                    w.writerow(
                        {
                            "provider": "eodhd_ws",
                            "symbol": bar.symbol,
                            "isin": isin_by_ws.get(bar.symbol, ""),
                            "received_at": bar.received_at.isoformat(),
                            "market_timestamp": bar.market_timestamp.isoformat(),
                            "last": bar.close,
                            "volume_cum": bar.volume,
                            "data_status": "bar_complete" if bar.complete_bar else "bar_incomplete",
                            "perimeter": bar.market_perimeter.value,
                        }
                    )
                ws.aggregator.closed.clear()
            fh.flush()
            time.sleep(args.interval)
    stop.set()
    if ws is not None:
        (out_dir / f"{day}-ws-events.yaml").write_text(yaml.safe_dump(ws.events, allow_unicode=True), encoding="utf-8")
    print(f"observations enregistrées dans {csv_path}")
    return 0


def report(args: argparse.Namespace) -> int:
    path = Path(args.report)
    day = path.stem
    obs: list[Observation] = []
    bars: list[tuple[str, str, datetime, bool]] = []
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["data_status"].startswith("bar_"):
                bars.append(
                    (
                        r["provider"],
                        r["symbol"],
                        datetime.fromisoformat(r["market_timestamp"]),
                        r["data_status"] == "bar_complete",
                    )
                )
                continue
            obs.append(
                Observation(
                    r["provider"],
                    r["symbol"],
                    r["isin"],
                    datetime.fromisoformat(r["received_at"]),
                    datetime.fromisoformat(r["market_timestamp"]) if r["market_timestamp"] else None,
                    float(r["last"]) if r["last"] else None,
                    int(float(r["volume_cum"])) if r["volume_cum"] else None,
                    r["data_status"],
                    r["perimeter"],
                )
            )
    refs: list[Reference] = []
    ref_path = Path("tests/fixtures/qualification") / f"reference_saxo_{day}.csv"
    if ref_path.exists():
        with open(ref_path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                refs.append(
                    Reference(
                        r["isin"],
                        datetime.fromisoformat(r["at_utc"]),
                        float(r["last"]),
                        int(r["volume"]) if r.get("volume") else None,
                    )
                )
    events: list[dict[str, object]] = []
    ev_path = path.with_name(f"{day}-ws-events.yaml")
    if ev_path.exists():
        events = yaml.safe_load(ev_path.read_text(encoding="utf-8")) or []
    providers = sorted({o.provider for o in obs})
    text = render_report(day, delay_stats(obs), compare_to_reference(obs, refs, providers), gap_summary(bars), events)
    out = path.with_suffix(".md")
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"rapport écrit : {out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="tests/fixtures/qualification/symbols.yaml")
    p.add_argument("--minutes", type=int, default=30)
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--out", default="data/qualification")
    p.add_argument("--no-ws", action="store_true")
    p.add_argument("--report", default=None, help="CSV d'observations à analyser")
    args = p.parse_args()
    return report(args) if args.report else record(args)


if __name__ == "__main__":
    sys.exit(main())
