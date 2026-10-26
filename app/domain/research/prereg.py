"""Pre-registration file → typed plan (docs/31 §4.1). Pure: parses an already-loaded mapping.

The plan's cells are expanded from the families' grids; the count of registered cells (confirmatory +
exploratory + stage-1 cap + F0 + stage-2 cap) must not exceed ``max_total_cells`` and must equal
``expected_total_cells``. The file's sha256 and the hash of the expanded cell set identify a registration:
the same file hash with another cell set is refused (the ledger keeps both), and the holdout is unsealed at
most once per frozen candidate file.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

CellTier = Literal["confirmatory", "exploratory", "f0"]
Mode = Literal["event", "monthly", "monthly_overlay"]


class PreregError(ValueError):
    """The plan or the ledger forbids the requested run."""


@dataclass(frozen=True)
class Cell:
    id: str
    tier: CellTier
    family: str  # H (confirmatory), F1, F2, F3, F5, F0
    trigger: str  # t01 | t03 | c1 | t05 | t07 | t08 | t09 | d2x | f0
    params: dict[str, Any] = field(default_factory=dict)
    gate: str = "none"
    h: int = 20
    exit: str = "X3"
    cap_atr: float | None = None
    cap: str | None = None  # D2x cap "kind:value"
    validity: int = 1
    mode: Mode = "event"
    unit: Literal["r_ref", "r_stop"] = "r_ref"
    accounts: tuple[str, ...] = ("cto",)
    overlay: str | None = None
    max_hold: int | None = None

    @property
    def combo_key(self) -> tuple[str, float, int, int, int | None]:
        """Entry / exit combination simulated once on the whole eligible panel (controls)."""
        cap = self.cap_atr if self.cap_atr is not None else 1.0
        return (self.exit, float(cap), self.validity, self.h, self.max_hold)


@dataclass(frozen=True)
class Segments:
    warmup_sessions: int
    discovery: tuple[date, date]
    embargo: tuple[date, date]
    holdout_start: date
    end_margin_sessions: int
    purge_sessions: int
    wf_test: tuple[tuple[date, date], ...]


@dataclass(frozen=True)
class Prereg:
    id: str
    sha256: str
    seed: int
    segments: Segments
    parameters_keys: dict[str, str]
    assumptions: dict[str, Any]  # key → value
    assumption_flags: dict[str, bool]
    thresholds: dict[str, Any]
    inference: dict[str, Any]
    cells: tuple[Cell, ...]
    stage1_exits: tuple[str, ...]
    stage1_max_cells: int
    stage2_max_cells: int
    max_total_cells: int

    @property
    def confirmatory(self) -> list[Cell]:
        return [c for c in self.cells if c.tier == "confirmatory"]

    @property
    def exploratory(self) -> list[Cell]:
        return [c for c in self.cells if c.tier == "exploratory"]

    @property
    def f0(self) -> list[Cell]:
        return [c for c in self.cells if c.tier == "f0"]

    @property
    def total_cells(self) -> int:
        return len(self.cells) + self.stage1_max_cells + self.stage2_max_cells

    @property
    def cell_set_hash(self) -> str:
        payload = json.dumps(
            [sorted(c.id for c in self.cells), self.stage1_max_cells, self.stage2_max_cells], separators=(",", ":")
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def counts(self) -> dict[str, int]:
        return {
            "confirmatoires": len(self.confirmatory),
            "exploratoires_etape0": len(self.exploratory),
            "etape1_plafond": self.stage1_max_cells,
            "diagnostics_f0": len(self.f0),
            "etape2_plafond": self.stage2_max_cells,
            "total": self.total_cells,
        }

    def a(self, key: str) -> Any:
        return self.assumptions[key]


def _d(x: Any) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x))


def _num(v: Any) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


def _cell(raw: Mapping[str, Any], tier: CellTier, family: str, cid: str) -> Cell:
    known = {"id", "family", "trigger", "mode", "gate", "h", "exit", "cap_atr", "cap", "validity", "unit",
             "accounts", "overlay", "max_hold", "params", "grid", "fixed"}  # fmt: skip
    params = dict(raw.get("params") or {})
    params.update({k: v for k, v in raw.items() if k not in known})
    return Cell(
        id=cid,
        tier=tier,
        family=family,
        trigger=str(raw.get("trigger", "f0")),
        params=params,
        gate=str(raw.get("gate", "none")),
        h=int(raw.get("h", 20)),
        exit=str(raw.get("exit", "X3")),
        cap_atr=float(raw["cap_atr"]) if raw.get("cap_atr") is not None else None,
        cap=str(raw["cap"]) if raw.get("cap") is not None else None,
        validity=int(raw.get("validity", 1)),
        mode=raw.get("mode", "event"),
        unit=raw.get("unit", "r_ref"),
        accounts=tuple(raw.get("accounts") or ("cto",)),
        overlay=raw.get("overlay"),
        max_hold=int(raw["max_hold"]) if raw.get("max_hold") is not None else None,
    )


def expand_family(fam: Mapping[str, Any]) -> list[Cell]:
    grid: Mapping[str, Sequence[Any]] = fam.get("grid") or {}
    fixed: Mapping[str, Any] = fam.get("fixed") or {}
    keys = list(grid)
    out = []
    for values in itertools.product(*(grid[k] for k in keys)):
        raw: dict[str, Any] = {k: v for k, v in fam.items() if k not in ("grid", "fixed", "family")}
        raw.update(fixed)
        raw.update(dict(zip(keys, values, strict=True)))
        raw.setdefault("gate", "none")
        if raw["trigger"] == "t08" or raw["trigger"] in ("t07", "t09", "t05"):
            raw.setdefault("cap_atr", 1.0)
        label = ".".join(f"{k}{_num(v)}" for k, v in zip(keys, values, strict=True))
        cid = f"{fam['family']}.{raw['trigger']}." + label
        out.append(_cell(raw, "exploratory", str(fam["family"]), cid))
    return out


def parse_prereg(raw: Mapping[str, Any], sha256: str) -> Prereg:
    seg = raw["segments"]
    segments = Segments(
        int(seg["warmup_sessions"]),
        (_d(seg["discovery"][0]), _d(seg["discovery"][1])),
        (_d(seg["embargo"][0]), _d(seg["embargo"][1])),
        _d(seg["holdout_start"]),
        int(seg["end_margin_sessions"]),
        int(seg["purge_sessions"]),
        tuple((_d(a), _d(b)) for a, b in seg["wf_test"]),
    )
    cells: list[Cell] = [_cell(c, "confirmatory", "H", str(c["id"])) for c in raw.get("confirmatory") or []]
    for fam in raw.get("exploratory") or []:
        cells += expand_family(fam)
    cells += [_cell({"trigger": "f0"}, "f0", "F0", str(c["id"])) for c in raw.get("f0") or []]
    ids = [c.id for c in cells]
    if len(set(ids)) != len(ids):
        raise PreregError("identifiants de cellules en double")
    assumptions = {
        k: (v["value"] if isinstance(v, Mapping) and "value" in v else v) for k, v in raw["assumptions"].items()
    }
    flags = {
        k: bool(v.get("assumption", False)) if isinstance(v, Mapping) else False for k, v in raw["assumptions"].items()
    }
    p = Prereg(
        id=str(raw["id"]),
        sha256=sha256,
        seed=int(raw["seed"]),
        segments=segments,
        parameters_keys=dict(raw.get("parameters_keys") or {}),
        assumptions=assumptions,
        assumption_flags=flags,
        thresholds=dict(raw["thresholds"]),
        inference=dict(raw["inference"]),
        cells=tuple(cells),
        stage1_exits=tuple(raw["stage1"]["exits"]),
        stage1_max_cells=int(raw["stage1"]["max_cells"]),
        stage2_max_cells=int(raw["stage2"]["max_cells"]),
        max_total_cells=int(raw["max_total_cells"]),
    )
    if p.total_cells > p.max_total_cells:
        raise PreregError(f"{p.total_cells} cellules enregistrées > plafond {p.max_total_cells}")
    expected = raw.get("expected_total_cells")
    if expected is not None and int(expected) != p.total_cells:
        raise PreregError(f"{p.total_cells} cellules développées ≠ {expected} annoncées dans le plan")
    return p


def file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# --- ledger rules -----------------------------------------------------------------------------------


def check_ledger(rows: Sequence[Mapping[str, str]], prereg: Prereg) -> None:
    """Refuse a run whose plan hash is already registered with a different cell set."""
    for r in rows:
        if r.get("kind", "run") != "run":
            continue
        if r.get("prereg_sha256") == prereg.sha256 and r.get("cell_set_sha256") not in (None, prereg.cell_set_hash):
            raise PreregError(
                "ce plan (même empreinte) a déjà été exécuté avec un autre ensemble de cellules : "
                "créer un nouvel enregistrement (nouveau fichier) au lieu de modifier celui-ci"
            )


def check_unseal(
    candidates: Mapping[str, Any] | None, candidates_sha256: str | None, prereg: Prereg,
    rows: Sequence[Mapping[str, str]],
) -> list[str]:  # fmt: skip
    """The single holdout unseal: needs a frozen candidate file of this plan (≤ ``candidates_max`` cells),
    never used before. Returns the candidate cell ids."""
    if candidates is None or candidates_sha256 is None:
        raise PreregError("descellement refusé : fichier de candidats figé absent")
    if candidates.get("prereg_sha256") != prereg.sha256:
        raise PreregError("descellement refusé : le fichier de candidats ne vient pas de ce plan")
    ids = [str(x) for x in candidates.get("cells") or []]
    if not ids:
        raise PreregError("descellement refusé : aucun candidat dans le fichier")
    if len(ids) > int(prereg.thresholds["candidates_max"]):
        raise PreregError(f"descellement refusé : plus de {prereg.thresholds['candidates_max']} candidats")
    known = {c.id for c in prereg.cells}
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise PreregError(f"descellement refusé : cellules inconnues du plan {unknown}")
    for r in rows:
        if r.get("kind") == "unseal" and r.get("prereg_sha256") == prereg.sha256:
            raise PreregError("descellement refusé : la période scellée de ce plan a déjà été ouverte une fois")
    return ids
