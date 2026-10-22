"""French TTF applicability (docs/08 §8.7, docs/22 R11): the official yearly list decides (issuer's
head office, not the listing venue — ArcelorMittal, Luxembourg, is out of scope). Without the list
for the year, a flagged estimate is used: French ISIN and market cap above the threshold."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

LIST_PATH = Path("data/ttf_list.yaml")  # ./data is shared by the container and the PC CLI


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def official_list(path: Path = LIST_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _load(str(path), path.stat().st_mtime)


def ttf_status(
    isin: str, market_cap_eur: float | None, threshold_eur: float, year: int, path: Path = LIST_PATH
) -> tuple[bool, str]:
    """(due, basis). ``basis`` says whether the official list or an estimate decided."""
    lst = official_list(path)
    if lst and int(lst.get("year", 0)) == year:
        return isin in set(lst.get("isins") or []), f"liste officielle {year} (BOFiP)"
    due = isin.startswith("FR") and market_cap_eur is not None and market_cap_eur > threshold_eur
    return due, f"estimation : liste officielle {year} non chargée (ttf-list-load)"
