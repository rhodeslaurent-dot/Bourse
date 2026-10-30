"""Pre-registration file → typed plan (docs/31 §4.1). Pure: parses an already-loaded mapping.

The plan's cells are expanded from the families' grids; the count of registered cells (confirmatory +
exploratory + stage-1 cap + F0 + stage-2 cap) must not exceed ``max_total_cells`` and must equal
``expected_total_cells``. The file's sha256 and the hash of the expanded cell set identify a registration:
the same file hash with another cell set is refused (the ledger keeps both), and the holdout is unsealed at
most once per frozen candidate file.

Registration n° 7 (docs/34 §34.14) adds two keys: ``draft: true`` (a BROUILLON: parsed, but ``run``, ``calibrate``
and ``unseal`` refuse it until the key is removed — removing it gives the frozen file its sha256) and
``replication.mode: confirmatory`` (the plan's cells are declared for the sealed old replication period itself: no
candidate file, the single ``unseal`` is the confirmatory test, ``run`` on the discovery is descriptive only).
"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from app.domain.research.insider_triggers import INSIDER_TRIGGERS
from app.domain.research.triggers import GAP_TRIGGERS, cell_needs_earnings, check_gap_params

CellTier = Literal["confirmatory", "exploratory", "f0"]
Mode = Literal["event", "monthly", "monthly_overlay"]
MOMENTUM_TRIGGERS = frozenset({"t01", "t03", "c1"})
EARNINGS_TRIGGERS = frozenset({"t14", "t15", "t16"})  # registration n° 3: need the announcement history
# registration n° 4 (entry policies, docs/34 §34.11): production setups (detector + zones + R1 stop + exploitable
# targets), letters with an explicit trigger, and t00 / t14 replayed under the entry policies
SETUP_TRIGGERS = frozenset({"d7x", "d2x", "d8x"})
ENTRY_TRIGGERS = SETUP_TRIGGERS | {"t00", "t14", "letters_trigger"}
ENTRY_POLICIES = ("P0", "P1", "P2", "P3", "P4")
ENTRY_EXITS = ("X8", "XP", "X3")
# registration n° 6 (exit rules on frozen entries, docs/34 §34.13): XP production, XS production stop without target,
# XQ partial at +1 R then chandelier; X1 / X3 / X4 / X5 / X6 / X8 as defined by the earlier plans (the initial stop of
# X4 / X5 / X6 is the cell's ``stop``: the production stop on a setup trigger)
EXIT_RULE_CODES = ("XP", "XS", "XQ", "X1", "X3", "X4", "X5", "X6", "X8")
EXIT_BASELINES = ("XP", "X3")
EXIT_STOPS = ("prod", "atr2.5", "sigma3", "bench")
EXIT_ENTRIES = ("P1", "P2")
KNOWN_TRIGGERS = (
    frozenset({"t00", "t01", "t03", "c1", "t05", "t07", "t08", "t09", "d2x", "f0", "d7x", "d8x", "letters_trigger"})
    | EARNINGS_TRIGGERS
)
SEALED_FAR_FUTURE = date(2999, 12, 31)
REPLICATION_MODES = ("candidates", "confirmatory")
# registration n° 7: the verdict of a plan whose confirmatory cells are declared for the sealed period
SEALED_KEYS = ("min_t_week", "min_dsr", "dsr_prior_trials", "placebo_max_abs_t")
R_STOP_EXITS = ("X4", "X8", "XT")  # exits on an ATR stop: controls and cells in R of the trade (pipeline.primary_unit)
# plans n° 1 to 6 were run with X4 alone in R of the trade (X8 in R_ref): kept for them, so that their replication
# computes exactly what their discovery computed; a plan opts in with the assumption ``atr_stop_exits_in_r_stop``
LEGACY_R_STOP_EXITS = ("X4",)


def r_stop_exits(assumptions: Mapping[str, Any]) -> tuple[str, ...]:
    return R_STOP_EXITS if assumptions.get("atr_stop_exits_in_r_stop") else LEGACY_R_STOP_EXITS


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
    unit: Literal["r_ref", "r_stop", "eur_300"] = "r_ref"
    accounts: tuple[str, ...] = ("cto",)
    overlay: str | None = None
    max_hold: int | None = None
    # registration n° 4: the entry policy tested against a baseline policy on the same signals (paired per signal)
    entry: str | None = None
    baseline_entry: str | None = None
    confirm_cap_atr: float | None = None  # P2: maximum price = level × (1 + x · ATR14 / close)
    diagnostic: bool = False  # reported, never a candidate, outside every multiple-testing family
    # registration n° 6: the exit tested against a baseline exit on the same filled entries (paired per trade)
    baseline_exit: str | None = None
    baseline_h: int | None = None  # time exit of the baseline (default ``h``)
    stop: str | None = None  # initial stop of the tested exit: prod | atr2.5 | sigma3 | bench (default by code)

    @property
    def is_entry_cell(self) -> bool:
        return self.entry is not None or self.baseline_entry is not None

    @property
    def ref_h(self) -> int:
        """Horizon of the baseline exit, of the σ stops and of the R_ref unit of both arms."""
        return int(self.baseline_h if self.baseline_h is not None else self.h)

    @property
    def entry_cap_atr(self) -> float:
        """E_open(x) cap of the cell's orders. Only a D2x cell (its own cap rule ``cap``, as ``p_max``) may
        have none; every other cell gets it from the plan (``momentum_entry_cap_atr`` for T01 / T03 / C1)."""
        if self.cap_atr is not None:
            return float(self.cap_atr)
        if self.cap is not None:
            return math.nan  # unused: the orders' limit is the D2x cap rule
        raise ValueError(f"cellule {self.id} : plafond d'entrée absent du plan")

    @property
    def combo_key(self) -> tuple[str, float, int, int, int | None, str | None]:
        """Entry / exit combination simulated once on the whole eligible panel (controls); a D2x cell carries
        its own cap rule, so its controls enter with the same rule."""
        cap = self.cap_atr if self.cap_atr is not None else 0.0
        return (self.exit, float(cap), self.validity, self.h, self.max_hold, self.cap)


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
    # registration n° 2: group → EODHD exchange codes, each group judged on its own venue index (None: the
    # P0 / P1 / P2 groups of params.yaml, regime on the CAC 40 for Paris and the STOXX 600 elsewhere)
    venue_groups: dict[str, tuple[str, ...]] | None = None
    # registration n° 3: a sealed period BEFORE the discovery (old replication), opened once by ``unseal`` for
    # the frozen candidates; no ``run`` may load a bar of it (``check_run_start``)
    replication: tuple[date, date] | None = None
    # registration n° 7: ``replication.mode: confirmatory`` — the cells are tested directly on the replication
    # period (folds ``replication.wf_test``), the discovery having been read already (``run`` descriptive only)
    sealed_confirmation: bool = False
    replication_folds: tuple[tuple[date, date], ...] = ()
    draft: bool = False  # BROUILLON: never run, calibrated nor unsealed (``check_frozen``)

    @property
    def is_exit_plan(self) -> bool:
        """Registration n° 6: the cells compare exits on the same filled entries (paired per trade)."""
        return any(c.baseline_exit is not None for c in self.cells)

    @property
    def is_entry_plan(self) -> bool:
        """Registration n° 4: every cell compares entry policies on the same signals (paired per signal)."""
        return any(c.is_entry_cell for c in self.cells) and not self.is_exit_plan

    @property
    def needs_letters(self) -> bool:
        return any(c.trigger == "letters_trigger" for c in self.cells)

    @property
    def needs_earnings(self) -> bool:
        return any(cell_needs_earnings(c.trigger, c.params) for c in self.cells)

    @property
    def sealed_window(self) -> tuple[date, date]:
        """Signal window of the single unseal: the old replication period when the plan declares one, else
        the holdout (from ``holdout_start`` on)."""
        return self.replication if self.replication is not None else (self.segments.holdout_start, SEALED_FAR_FUTURE)

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
             "accounts", "overlay", "max_hold", "params", "grid", "fixed", "entry", "baseline_entry",
             "confirm_cap_atr", "diagnostic", "baseline_exit", "baseline_h", "stop"}  # fmt: skip
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
        entry=str(raw["entry"]) if raw.get("entry") is not None else None,
        baseline_entry=str(raw["baseline_entry"]) if raw.get("baseline_entry") is not None else None,
        confirm_cap_atr=float(raw["confirm_cap_atr"]) if raw.get("confirm_cap_atr") is not None else None,
        diagnostic=bool(raw.get("diagnostic", False)),
        baseline_exit=str(raw["baseline_exit"]) if raw.get("baseline_exit") is not None else None,
        baseline_h=int(raw["baseline_h"]) if raw.get("baseline_h") is not None else None,
        stop=str(raw["stop"]) if raw.get("stop") is not None else None,
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
        if raw["trigger"] in ("t05", "t07", "t08", "t09") or raw["trigger"] in EARNINGS_TRIGGERS:
            raw.setdefault("cap_atr", 1.0)
        label = ".".join(f"{k}{_num(v)}" for k, v in zip(keys, values, strict=True))
        cid = f"{fam['family']}.{raw['trigger']}." + label
        out.append(_cell(raw, "exploratory", str(fam["family"]), cid))
    return out


KNOWN_TRIGGERS = KNOWN_TRIGGERS | INSIDER_TRIGGERS  # registration n° 5: managers' dealings (insider_triggers)
KNOWN_TRIGGERS = KNOWN_TRIGGERS | GAP_TRIGGERS  # registration n° 7: opening gap down (triggers.t18_gap_down)


Span = tuple[date, date]


def _replication(raw: Mapping[str, Any], segments: Segments) -> tuple[Span | None, bool, tuple[Span, ...]]:
    """The old replication period, its mode and (confirmatory mode) its own walk-forward folds, all inside it."""
    rep = raw.get("replication")
    if not rep:
        return None, False, ()
    period = (_d(rep["start"]), _d(rep["end"]))
    if not period[0] < period[1] < segments.discovery[0]:
        raise PreregError("période de réplication : début < fin < début de la découverte exigés")
    mode = str(rep.get("mode", "candidates"))
    if mode not in REPLICATION_MODES:
        raise PreregError(f"période de réplication : mode {mode!r} inconnu ({', '.join(REPLICATION_MODES)})")
    if mode != "confirmatory":
        return period, False, ()
    folds = tuple((_d(a), _d(b)) for a, b in rep.get("wf_test") or [])
    if not folds:
        raise PreregError("réplication confirmatoire : plis (replication.wf_test) absents")
    prev = period[0] - timedelta(days=1)
    for a, b in folds:
        if not (prev < a <= b <= period[1]):
            raise PreregError("réplication confirmatoire : plis ordonnés, disjoints et dans la période exigés")
        prev = b
    missing = [k for k in SEALED_KEYS if k not in (raw.get("thresholds") or {}).get("sealed", {})]
    if missing:
        raise PreregError(f"réplication confirmatoire : seuils du verdict scellé absents (thresholds.sealed) {missing}")
    return period, True, folds


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
    unknown = sorted({c.trigger for c in cells} - KNOWN_TRIGGERS)
    if unknown:
        raise PreregError(f"déclencheur(s) inconnu(s) du banc : {unknown}")
    for c in cells:
        if c.trigger in GAP_TRIGGERS:
            if c.cap is not None or c.cap_atr is None:  # ``cap`` is the D2x cap rule, never a t18 split (cap_class)
                raise PreregError(f"cellule {c.id} : t18 exige cap_atr (E_open), sans « cap » (découpage : cap_class)")
            try:
                check_gap_params(c.params)
            except ValueError as e:
                raise PreregError(f"cellule {c.id} : {e}") from e
    replication, sealed_conf, rep_folds = _replication(raw, segments)
    assumptions = {
        k: (v["value"] if isinstance(v, Mapping) and "value" in v else v) for k, v in raw["assumptions"].items()
    }
    # the E_open cap of the momentum cells is a registered assumption of the plan (never a code default)
    mom_cap = assumptions.get("momentum_entry_cap_atr")
    for k, c in enumerate(cells):
        if c.trigger in MOMENTUM_TRIGGERS and c.cap_atr is None:
            if mom_cap is None:
                raise PreregError(f"cellule {c.id} : hypothèse momentum_entry_cap_atr absente du plan")
            cells[k] = dataclasses.replace(c, cap_atr=float(mom_cap))
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
        venue_groups={str(k): tuple(str(x) for x in v) for k, v in raw["venue_groups"].items()}
        if raw.get("venue_groups")
        else None,
        replication=replication,
        sealed_confirmation=sealed_conf,
        replication_folds=rep_folds,
        draft=bool(raw.get("draft", False)),
    )
    if p.is_exit_plan:
        check_exit_plan(p)
    elif p.is_entry_plan:
        check_entry_plan(p)
    else:
        check_units(p)
    if p.total_cells > p.max_total_cells:
        raise PreregError(f"{p.total_cells} cellules enregistrées > plafond {p.max_total_cells}")
    expected = raw.get("expected_total_cells")
    if expected is not None and int(expected) != p.total_cells:
        raise PreregError(f"{p.total_cells} cellules développées ≠ {expected} annoncées dans le plan")
    return p


PAIRED_KEYS = ("unit", "min_delta_r_per_signal", "min_t_newey_west_weekly", "min_ci_low", "min_positive_folds",
               "min_fill_rate", "replication_min_ci_low", "book_min_orders_per_month")  # fmt: skip
NEEDS_SETUP = ("P0", "P3", "P4")  # policies that read the setup's structural stop and targets


def check_entry_plan(p: Prereg) -> None:
    """An entry-policy plan (registration n° 4): every cell is an entry cell with known policies, triggers and
    exits; a non-diagnostic cell has a baseline different from its policy; the paired thresholds are stated."""
    missing = [k for k in PAIRED_KEYS if k not in (p.thresholds.get("paired") or {})]
    if missing:
        raise PreregError(f"plan de politiques d'entrée : seuils appariés absents {missing}")
    for c in p.cells:
        if c.tier == "f0" or not c.is_entry_cell:
            raise PreregError(f"cellule {c.id} : un plan de politiques d'entrée ne contient que des cellules d'entrée")
        pols = [x for x in (c.entry, c.baseline_entry) if x is not None]
        if any(x not in ENTRY_POLICIES for x in pols):
            raise PreregError(f"cellule {c.id} : politique inconnue {pols}")
        if c.trigger not in ENTRY_TRIGGERS or c.exit not in ENTRY_EXITS:
            raise PreregError(f"cellule {c.id} : déclencheur ou sortie hors du banc des politiques d'entrée")
        if not c.diagnostic and (c.entry is None or c.baseline_entry is None or c.entry == c.baseline_entry):
            raise PreregError(f"cellule {c.id} : une politique et une référence différentes sont exigées")
        if c.trigger == "t14" and (any(x in NEEDS_SETUP for x in pols) or c.exit == "XP"):
            raise PreregError(f"cellule {c.id} : t14 n'a ni stop structurel ni objectif (P1 / P2 et X3 seulement)")
        if c.unit != "r_stop":
            raise PreregError(f"cellule {c.id} : l'unité d'une cellule d'entrée est r_stop")


EXIT_PAIRED_KEYS = ("unit", "min_delta_r_per_trade", "min_delta_eur_300_per_trade", "min_t_newey_west_weekly",
                    "min_ci_low", "min_positive_folds", "replication_min_ci_low", "max_dd_worse_ratio")  # fmt: skip
SETUP_LEVEL_TRIGGERS = SETUP_TRIGGERS | {"t00", "letters_trigger"}  # triggers with a structural stop and a target


def default_stop(code: str, trigger: str) -> str:
    """The initial stop of an exit code: XP / XS / XQ always the production stop; X4 / X5 / X6 the production stop
    on a setup trigger, else their own bench stop; X1 / X3 / X8 their own bench stop."""
    if code in ("XP", "XS", "XQ") or (code in ("X4", "X5", "X6") and trigger in SETUP_LEVEL_TRIGGERS):
        return "prod"
    return "bench"


def check_exit_plan(p: Prereg) -> None:
    """An exit-rule plan (registration n° 6): every cell names a common entry (P1 / P2), a known exit, a known
    baseline (or is the diagnostic excursion table), the paired thresholds are stated; a production level needs a
    setup trigger; the unit r_stop needs both arms on the same initial stop (else eur_300 or r_ref)."""
    missing = [k for k in EXIT_PAIRED_KEYS if k not in (p.thresholds.get("paired") or {})]
    if missing:
        raise PreregError(f"plan de règles de sortie : seuils appariés absents {missing}")
    max_hold = int(p.thresholds["a12"]["max_hold_sessions"])
    for c in p.cells:
        if c.tier == "f0" or c.entry not in EXIT_ENTRIES or c.baseline_entry is not None:
            raise PreregError(
                f"cellule {c.id} : une cellule de sortie a une entrée commune P1 ou P2, sans référence d'entrée"
            )
        if c.exit not in EXIT_RULE_CODES or (c.baseline_exit is not None and c.baseline_exit not in EXIT_BASELINES):
            raise PreregError(f"cellule {c.id} : sortie ou sortie de référence inconnue ({c.exit}, {c.baseline_exit})")
        if c.baseline_exit is None and not c.diagnostic:
            raise PreregError(f"cellule {c.id} : une sortie de référence est exigée hors diagnostic")
        if c.baseline_exit == c.exit:
            raise PreregError(f"cellule {c.id} : la sortie testée et la référence sont identiques")
        if c.stop is not None and c.stop not in EXIT_STOPS:
            raise PreregError(f"cellule {c.id} : stop initial inconnu {c.stop}")
        if c.trigger not in ENTRY_TRIGGERS:
            raise PreregError(f"cellule {c.id} : déclencheur hors du banc des règles de sortie")
        st = c.stop or default_stop(c.exit, c.trigger)
        st_ref = default_stop(c.baseline_exit, c.trigger) if c.baseline_exit else st
        if c.trigger not in SETUP_LEVEL_TRIGGERS and ("prod" in (st, st_ref) or c.baseline_exit == "XP"):
            raise PreregError(
                f"cellule {c.id} : {c.trigger} n'a ni stop structurel ni objectif (sorties de banc seulement)"
            )
        if (
            c.unit == "r_stop"
            and c.baseline_exit is not None
            and (st != st_ref or (st == "bench" and c.exit != c.baseline_exit))
        ):
            raise PreregError(
                f"cellule {c.id} : l'unité r_stop exige le même stop initial des deux côtés (eur_300 ou r_ref sinon)"
            )
        if max(c.h, c.ref_h) > max_hold:
            raise PreregError(f"cellule {c.id} : durée au-delà de A12 ({max_hold} séances)")


def check_units(p: Prereg) -> None:
    """A cell of the standard bench is measured in the unit of its combination's controls: R of the trade
    (``r_stop``) on an ATR-stop exit (X4, X8, XT), R_ref otherwise — else its excess would mix two units."""
    for c in p.cells:
        if c.tier == "f0":
            continue
        want = "r_stop" if c.exit in r_stop_exits(p.assumptions) else "r_ref"
        if c.unit != want:
            raise PreregError(f"cellule {c.id} : la sortie {c.exit} se mesure en {want} (unité des contrôles appariés)")


def file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def check_frozen(prereg: Prereg) -> None:
    """A BROUILLON plan (``draft: true``) is never run, calibrated nor unsealed: the key is removed when the plan is
    frozen, which gives the file the sha256 recorded in the docs and in the ledger."""
    if prereg.draft:
        raise PreregError(
            f"plan {prereg.id} au statut BROUILLON (clé « draft: true ») : le geler d'abord — retirer la clé, inscrire "
            "l'empreinte sha256 dans la documentation — avant toute calibration, exécution ou descellement"
        )


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


def check_run_start(prereg: Prereg, start: date) -> None:
    """A registration run never loads a bar of the plan's sealed replication period (registration n° 3): its
    data start after the replication end, so no signal, feature or warm-up reads that period before the
    single unseal. Signals themselves are always inside ``segments.discovery``."""
    if prereg.replication is not None and start <= prereg.replication[1]:
        raise PreregError(
            f"exécution refusée : les données commenceraient le {start}, dans la période de réplication scellée "
            f"({prereg.replication[0]} → {prereg.replication[1]}) ; --from postérieur au {prereg.replication[1]} "
            f"(défaut 2016-01-04), la réplication ne s'ouvre qu'une fois par « research unseal »"
        )


REPLICATION_WARMUP_DAYS = 366  # data before the replication start: the plan's 252-session warm-up


def check_unseal_span(prereg: Prereg, start: date, end: date) -> None:
    """The unseal of a replication period needs data from a year before its start (warm-up) to its end plus the
    plan's end margin (``end_margin_sessions``, ≈ × 7/5 calendar days): its last signals can then be held to
    their exit. Without a replication period nothing is checked (the holdout runs to the end of the data)."""
    if prereg.replication is None:
        return
    a, b = prereg.replication
    lo = a - timedelta(days=REPLICATION_WARMUP_DAYS)
    hi = b + timedelta(days=math.ceil(prereg.segments.end_margin_sessions * 7 / 5) + 10)
    if start > lo or end < hi:
        raise PreregError(
            f"descellement refusé : la réplication {a} → {b} demande des données du {lo} au moins (préchauffage) "
            f"au {hi} au moins (sorties des derniers signaux) : --from {lo} --to {hi}"
        )


CELL_SEP = ";"  # separator of the cell ids in a ledger column


def ledger_cells(text: str | None) -> list[str]:
    return [x for x in (text or "").split(CELL_SEP) if x]


def check_unseal(
    candidates: Mapping[str, Any] | None, candidates_sha256: str | None, prereg: Prereg,
    rows: Sequence[Mapping[str, str]],
) -> list[str]:  # fmt: skip
    """The single holdout unseal: needs a frozen candidate file of this plan (≤ ``candidates_max`` cells),
    never used before, **written by a ledgered run**: a ``run`` row of the ledger with the same plan hash and
    cell set, the same candidate-file sha256 and the same cells, all classified CANDIDAT by that run (a file
    edited or made by hand has another hash and is refused). Returns the candidate cell ids."""
    if candidates is None or candidates_sha256 is None:
        raise PreregError("descellement refusé : fichier de candidats figé absent")
    if candidates.get("prereg_sha256") != prereg.sha256:
        raise PreregError("descellement refusé : le fichier de candidats ne vient pas de ce plan")
    ids = [str(x) for x in candidates.get("cells") or []]
    if not ids:
        raise PreregError("descellement refusé : aucun candidat dans le fichier")
    if len(set(ids)) != len(ids):
        raise PreregError("descellement refusé : cellule en double dans le fichier")
    if len(ids) > int(prereg.thresholds["candidates_max"]):
        raise PreregError(f"descellement refusé : plus de {prereg.thresholds['candidates_max']} candidats")
    known = {c.id for c in prereg.cells}
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise PreregError(f"descellement refusé : cellules inconnues du plan {unknown}")
    for r in rows:
        if r.get("kind") == "unseal" and r.get("prereg_sha256") == prereg.sha256:
            raise PreregError("descellement refusé : la période scellée de ce plan a déjà été ouverte une fois")
    runs = [
        r for r in rows
        if r.get("kind", "run") == "run" and r.get("prereg_sha256") == prereg.sha256
        and r.get("cell_set_sha256") == prereg.cell_set_hash and r.get("candidates_sha256") == candidates_sha256
    ]  # fmt: skip
    if not runs:
        raise PreregError(
            "descellement refusé : aucune exécution inscrite au registre n'a écrit ce fichier de candidats "
            "(empreinte inconnue : fichier modifié ou fait à la main)"
        )
    ledgered = ledger_cells(runs[-1].get("candidate_cells"))
    if sorted(ledgered) != sorted(ids):
        raise PreregError("descellement refusé : les cellules du fichier ne sont pas celles inscrites au registre")
    levels = candidates.get("niveaux") or {}
    if any(levels.get(i) != "CANDIDAT" for i in ids):
        raise PreregError("descellement refusé : toutes les cellules du fichier doivent être classées CANDIDAT")
    return ids
