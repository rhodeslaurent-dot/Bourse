"""Generate per-venue holiday / half-day lists from the open-source ``exchange_calendars`` library
(dev tool, not a runtime dependency): ``pip install exchange_calendars`` then
``python scripts/gen_calendars.py XMIL XMAD … --years 2025,2026,2027``.

Prints YAML blocks for ``config/market_calendars.yaml`` (weekdays without a session = holidays;
early closes = half days, local time). Existing venues are compared, never overwritten: any
difference is printed for a human decision (CLAUDE.md rule 2: never guess a calendar).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import yaml


def venue_block(mic: str, years: list[int]) -> dict:
    import exchange_calendars as xc

    cal = xc.get_calendar(mic, start=f"{min(years)}-01-01", end=f"{max(years)}-12-31")
    tz = str(cal.tz)
    sessions = {d.date() for d in cal.sessions}
    holidays: dict[int, list[str]] = {}
    half: dict[int, dict[str, str]] = {}
    regular_close = None
    for y in years:
        d = dt.date(y, 1, 1)
        hol = []
        while d.year == y:
            if d.weekday() < 5 and d not in sessions:
                hol.append(d.isoformat())
            d += dt.timedelta(days=1)
        holidays[y] = hol
    closes = cal.closes.dt.tz_convert(tz)
    opens = cal.opens.dt.tz_convert(tz)
    regular_close = closes.dt.strftime("%H:%M").mode()[0]
    regular_open = opens.dt.strftime("%H:%M").mode()[0]
    for ts in closes:
        hhmm = ts.strftime("%H:%M")
        if hhmm < regular_close:
            half.setdefault(ts.year, {})[ts.date().isoformat()] = hhmm
    return {
        "name": cal.name,
        "tz": tz,
        "open": regular_open,
        "close": regular_close,
        "holidays": holidays,
        "half_days": {y: half.get(y, {}) for y in years},
        "source": f"exchange_calendars {xc.__version__} (généré le {dt.date.today():%d/%m/%Y}) — à confirmer sur le calendrier officiel de la place",  # noqa: E501
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mics", nargs="+")
    ap.add_argument("--years", default="2025,2026,2027")
    ap.add_argument("--compare", default="config/market_calendars.yaml")
    a = ap.parse_args()
    years = [int(y) for y in a.years.split(",")]
    existing = (
        yaml.safe_load(Path(a.compare).read_text(encoding="utf-8"))["markets"] if Path(a.compare).exists() else {}
    )
    out = {}
    for mic in a.mics:
        blk = venue_block(mic, years)
        if mic in existing:
            for y in years:
                have = {str(x) for x in (existing[mic].get("holidays") or {}).get(y, [])}
                gen = set(blk["holidays"][y])
                if have != gen:
                    print(
                        f"# {mic} {y} : écart — fichier {sorted(have - gen)} / bibliothèque {sorted(gen - have)}",
                        file=sys.stderr,
                    )
            continue
        out[mic] = blk
    if out:
        print(yaml.safe_dump({"markets": out}, allow_unicode=True, sort_keys=False, width=200))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
