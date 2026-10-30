# ruff: noqa: E501
"""docs/31 — trigger research harness: database → panels (cached), registration runs, ledger, reports.

The user runs it on his PC (``docker compose exec app python -m app.cli research …``); the report written
under ``data/research/`` holds statistics only (no price, no instrument) and is what a second round of
analysis reads. Every business value comes from ``config/params.yaml``; the research assumptions live in
the pre-registration file, whose sha256 is written to ``data/research/ledger.csv`` with each run.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import BacktestRun, BacktestTrade, EarningsEvent, FxRate, Instrument, PriceEod
from app.domain.calendar import MarketCalendars
from app.domain.costs import ftt_rate_any
from app.domain.research import f0 as F0
from app.domain.research.classify import Tier, unseal_confirmed
from app.domain.research.costs import Account, ttf_rates
from app.domain.research.entry_policies import entry_unseal_verdict
from app.domain.research.exit_rules import exit_unseal_verdict
from app.domain.research.panel import (
    EarningsRow,
    FeatureParams,
    IndexSeries,
    LetterLevels,
    Panel,
    earnings_flags,
    fx_path,
    valid_bars,
)
from app.domain.research.pipeline import VENUE_GROUPS, Settings, prepare_group
from app.domain.research.prereg import (
    CELL_SEP,
    Prereg,
    PreregError,
    check_frozen,
    check_ledger,
    check_run_start,
    check_unseal,
    check_unseal_span,
    file_sha256,
    parse_prereg,
)
from app.domain.research.registration import run_registration
from app.domain.research.report import assert_no_price_fields, build_report, render_fr, suppress_deep
from app.domain.research.sealed import (
    DESCRIPTIVE_PREFIX,
    check_sealed_once,
    render_sealed,
    report_view,
    sealed_plan,
    sealed_verdicts,
)
from app.services.insider_research import check_forward_unseal, insider_context, insider_report, with_insiders

log = logging.getLogger("bourse.research")
GROUPS = VENUE_GROUPS
LEDGER_FIELDS = ["timestamp", "kind", "prereg_id", "prereg_sha256", "cell_set_sha256", "m_total", "cells_run",
                 "candidates", "universe", "report", "candidates_sha256", "candidate_cells", "calibration"]  # fmt: skip
Progress = Callable[[str], None]
DEFAULT_MAX_F0_CANDIDATES = 20_000  # evaluations of the real detectors per venue group (F0 g/h), stratified


@dataclass(frozen=True)
class PanelMeta:
    start: date | None
    end: date | None
    instruments: dict[str, int]
    dropped_illiquid: int
    without_group: int
    delisted_or_inactive: int
    fx_fallback_currencies: list[str]
    fx_static_days: dict[str, dict[str, Any]]  # currency → days on the plan's static rate (before any ECB rate)
    earnings: dict[str, dict[str, int]] | None = None  # registration n° 3: announcements placed per group
    letters: dict[str, dict[str, int]] | None = None  # registration n° 4: letter values read / with a trigger


# --- plan and settings -----------------------------------------------------------------------------


def load_prereg(path: str | Path) -> Prereg:
    b = Path(path).read_bytes()
    return parse_prereg(yaml.safe_load(b), file_sha256(b))


def _tax(config: LoadedConfig) -> dict[str, Any]:
    from app.services.engine import _tax

    return _tax(config)


def resolve_settings(config: LoadedConfig, prereg: Prereg, universe: str) -> tuple[Settings, dict[str, Any]]:
    """Business values from params.yaml (rule 8), research assumptions from the plan."""
    from app.services.engine import _fees

    p = config.params
    u = p.universe or {}
    reg = getattr(p, "regime", None) or {}
    tax = _tax(config)
    risk = p.risk
    capital = float(p.capital.capital_pilote_eur)
    pfu = float(tax.get("pfu_total_rate") or 0.0)
    cto_fees = _fees(config, "cto_cash")
    srd_rate = float(p.srd.crd_daily_rate) if p.srd else 0.0
    caps = getattr(risk, "max_open_risk_pct_by_gate", None) or {"ouvert": float(risk.max_open_risk_pct)}
    slots = max(1, int(float(caps.get("ouvert", risk.max_open_risk_pct)) / float(risk.max_risk_per_trade_pct) + 1e-9))
    a = prereg.assumptions
    st = Settings(
        features=FeatureParams(
            float(u.get("min_price", 1.0)),
            float(u.get("min_adv_eur_pea", 500_000)),
            float(reg.get("breadth_green", 0.5)),
        ),  # fmt: skip
        slippage=float(risk.slippage_pct),
        risk_budget_eur=capital * float(risk.max_risk_per_trade_pct),
        notional_cap_eur=capital * float(risk.max_position_pct_of_capital),
        fx_haircut=float(getattr(risk, "fx_haircut", 0.0) or 0.0),
        cto=Account("cto", cto_fees, dividend_tax=pfu),
        pea=Account("pea", _fees(config, "pea")),
        srd=Account("srd", cto_fees, srd_daily_rate=srd_rate, dividend_tax=pfu),
        capacity_pct=float(a["capacity_pct_of_turnover"]),
        ca_jump=float(a["ca_jump"]),
        div_min=float(a["dividend_min_drop"]),
        hazard_small=float(a["bankruptcy_hazard_small"]),
        hazard_large=float(a["bankruptcy_hazard_large"]),
        takeover_jump=float(a["takeover_jump"]),
        cost_stress=float(a["cost_stress_multiplier"]),
        slip_stress=float(a["slippage_stress_multiplier"]),
        top_n=int(a["top_n_monthly"]),
        slots=slots,
        seed=prereg.seed,
        detectors=dict(p.detectors or {}),
        universe_label=universe,
        max_dividend_yield=float((a.get("dividend_yield_sanity") or [0.0, 0.12])[1]),
        ca_mask_sessions=int(prereg.segments.warmup_sessions),
        dividend_window_sessions=int(prereg.segments.warmup_sessions),
        min_turnover_cto_eur=float(u["min_adv_eur_cto"]) if u.get("min_adv_eur_cto") is not None else None,
        seal_start=prereg.segments.holdout_start,
        min_earnings_coverage=(
            float(prereg.thresholds["min_earnings_coverage"])
            if prereg.thresholds.get("min_earnings_coverage") is not None
            else None
        ),
        # registration n° 7: suspect prices skipped like a corporate action, only when the plan declares the ratio
        suspect_ratio=float(a["suspect_price_ratio"]) if a.get("suspect_price_ratio") is not None else None,
    )
    stop_p = risk.stop
    prod = {
        "zones": dict(getattr(p, "zones", None) or {}),
        "wick_buffer_pct": float(getattr(stop_p, "wick_buffer_pct", 0.005) or 0.005),
        "stop_buffer_atr_mult": float(stop_p.buffer_atr_mult),
        "min_reward_risk": float((getattr(p, "admission", None) or {}).get("min_reward_risk", 2.0)),
        "expiry_sessions": int((getattr(p, "entry", None) or {}).get("expiry_sessions", 3)),
    }
    return st, prod


def resolved_parameters(st: Settings, config: LoadedConfig) -> dict[str, Any]:
    return {
        "universe.min_price": st.features.min_price,
        "universe.min_adv_eur_pea": st.features.min_turnover_eur,
        "universe.min_adv_eur_cto": st.min_turnover_cto_eur,
        "regime.breadth_green": st.features.breadth_green,
        "risk.slippage_pct": st.slippage,
        "budget_risque_eur": st.risk_budget_eur,
        "plafond_notionnel_eur": st.notional_cap_eur,
        "risk.fx_haircut": st.fx_haircut,
        "lignes_simultanees": st.slots,
        "frais_cto": {"pct": st.cto.fees.commission_pct, "minimum_eur": st.cto.fees.commission_min_eur},
        "frais_pea": {"bandes": [list(b) for b in st.pea.fees.bands], "plafond_pct": st.pea.fees.commission_cap_pct},
        "srd.crd_daily_rate": st.srd.srd_daily_rate,
        "impot_dividendes_cto": st.cto.dividend_tax,
        "params_version": config.params.version,
    }


# --- panels ---------------------------------------------------------------------------------------


def mic_groups(
    config: LoadedConfig, calendars: MarketCalendars, venue_groups: dict[str, tuple[str, ...]] | None = None
) -> dict[str, str]:
    """MIC → venue group: the plan's own groups (registration n° 2) or the P0 / P1 / P2 lists of params.yaml."""
    u = config.params.universe or {}
    pairs = (
        list(venue_groups.items())
        if venue_groups
        else [(g, tuple(u.get(k) or [])) for g, k in (("P0", "markets_p0"), ("P1", "markets_p1"), ("P2", "markets_p2"))]
    )
    out: dict[str, str] = {}
    for grp, codes in pairs:
        for code in codes:
            mic = calendars.eodhd_to_mic.get(code)
            if mic:
                out[mic] = grp
    return out


def own_index_id(config: LoadedConfig, calendars: MarketCalendars, codes: tuple[str, ...]) -> str | None:
    """The index of a one-venue group (docs/32: each venue judged on its own index), from regime.indices_by_mic."""
    if len(codes) != 1:
        return None
    mic = calendars.eodhd_to_mic.get(codes[0])
    spec = ((getattr(config.params, "regime", None) or {}).get("indices_by_mic") or {}).get(mic) or {}
    return str(spec["id"]) if spec.get("id") else None


def _fx_series(
    s: Session, currencies: set[str], days: np.ndarray, static: dict[str, float]
) -> tuple[dict[str, np.ndarray], list[str], dict[str, np.ndarray]]:
    """EUR per unit for each currency on each day: last ECB rate on or before the day — never back-filled from
    a later rate; before the first known rate (or without any) the static table of the plan, flagged: the
    days on that rate are returned per currency (listed in the report header)."""
    out: dict[str, np.ndarray] = {"EUR": np.ones(days.size)}
    fallback: list[str] = []
    static_days: dict[str, np.ndarray] = {}
    for cur in sorted(currencies - {"EUR"}):
        base, div = ("GBP", 100.0) if cur in ("GBX", "GBp") else (cur, 1.0)
        rows = s.execute(select(FxRate.day, FxRate.rate).where(FxRate.pair == f"EUR{base}").order_by(FxRate.day)).all()
        rows = [r for r in rows if r[1]]
        d = np.array([np.datetime64(r[0], "D") for r in rows], dtype="datetime64[D]")
        v = np.array([1.0 / float(r[1]) / div for r in rows], dtype=np.float64)
        rate = static.get(cur)
        out[cur], flagged = fx_path(days, d, v, float(rate) if rate else None)
        if not rows or flagged.any():
            fallback.append(cur)
        if flagged.any():
            static_days[cur] = days[flagged]
    return out, fallback, static_days


def _index_series(s: Session, isin: str | None, start: date, end: date) -> IndexSeries | None:
    if not isin:
        return None
    rows = s.execute(
        select(PriceEod.day, PriceEod.close).where(PriceEod.isin == isin, PriceEod.day <= end).order_by(PriceEod.day)
    ).all()
    if not rows:
        return None
    return IndexSeries(np.array([np.datetime64(r[0], "D") for r in rows]), np.array([float(r[1]) for r in rows]))


def _read_price_panel(
    s: Session, isins_wanted: list[str], start: date, end: date, chunk_rows: int = 250_000
) -> tuple[np.ndarray, list[str], dict[str, np.ndarray]]:
    """Daily bars of ``isins_wanted`` as (T, N) arrays, read by chunks straight into the final arrays: the whole
    table never sits in memory as rows (New York, 7 849 values with the delisted ones, killed the process on the
    PC on 29/10/2026). Same output as a one-block read: the sessions present in the data, the values with at least
    one bar (sorted), the download vintage of each bar (source × UTC reception day; labels are arbitrary codes).
    The rows come through a server-side cursor (``stream_results``): with psycopg 3 a client cursor makes libpq
    buffer the whole result before the first chunk. The arrays are sized on the values that have bars."""
    import pandas as pd

    day_rows = s.execute(
        select(PriceEod.day)
        .where(PriceEod.isin.in_(isins_wanted), PriceEod.day >= start, PriceEod.day <= end)
        .distinct()
    ).all()
    if not day_rows:
        return np.array([], dtype="datetime64[D]"), [], {}
    days = np.unique(np.array([np.datetime64(r[0], "D") for r in day_rows], dtype="datetime64[D]"))
    cols = sorted(
        str(r[0])
        for r in s.execute(
            select(PriceEod.isin)
            .where(PriceEod.isin.in_(isins_wanted), PriceEod.day >= start, PriceEod.day <= end)
            .distinct()
        ).all()
    )
    col = {k: n for n, k in enumerate(cols)}
    arrays = {
        k: np.full((days.size, len(cols)), np.nan) for k in ("open", "high", "low", "close", "adj_close", "volume")
    }
    vin = np.full((days.size, len(cols)), -1, dtype=np.int64)
    vintages: dict[str, int] = {}
    stmt = select(
        PriceEod.isin, PriceEod.day, PriceEod.open, PriceEod.high, PriceEod.low, PriceEod.close,
        PriceEod.adj_close, PriceEod.volume, PriceEod.source, PriceEod.received_at,
    ).where(PriceEod.isin.in_(isins_wanted), PriceEod.day >= start, PriceEod.day <= end)  # fmt: skip
    names = ["isin", "day", "open", "high", "low", "close", "adj_close", "volume", "source", "received_at"]
    res = s.execute(stmt, execution_options={"stream_results": True, "max_row_buffer": chunk_rows})
    for part in res.partitions(chunk_rows):
        chunk = pd.DataFrame.from_records(part, columns=names)
        r = np.searchsorted(days, pd.to_datetime(chunk["day"]).to_numpy().astype("datetime64[D]"))
        c = chunk["isin"].map(col).to_numpy(dtype=np.int64)
        for k in arrays:
            arrays[k][r, c] = pd.to_numeric(chunk[k], errors="coerce").to_numpy(dtype=np.float64)
        rec = pd.to_datetime(chunk["received_at"], utc=True, errors="coerce").dt.strftime("%Y-%m-%d").fillna("?")
        labels = (chunk["source"].astype(str) + "|" + rec).to_numpy()
        vin[r, c] = np.array([vintages.setdefault(x, len(vintages)) for x in labels], dtype=np.int64)
        del chunk, part
    arrays["vintage"] = vin
    return days, cols, arrays


def _cache_key(parts: list[Any]) -> str:
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:16]


def select_instruments(s: Session, config: LoadedConfig, calendars: MarketCalendars, universe: str,
                       max_instruments: int | None,
                       venue_groups: dict[str, tuple[str, ...]] | None = None) -> tuple[dict[str, list[Instrument]], int]:  # fmt: skip
    groups = mic_groups(config, calendars, venue_groups)
    reg = getattr(config.params, "regime", None) or {}
    idx = set((reg.get("index_isins") or {}).values())  # index pseudo-instruments are never traded
    idx |= {str(v.get("id")) for v in (reg.get("indices_by_mic") or {}).values() if isinstance(v, dict)}
    idx |= {str((reg.get("europe_index") or {}).get("id"))}
    q = select(Instrument)
    if universe == "in_universe":
        q = q.where(Instrument.in_universe.is_(True))
    insts = [
        i
        for i in s.scalars(q.order_by(Instrument.isin))
        if i.isin not in idx and not i.isin.startswith("IDX.") and (i.kind or "stock") == "stock"
    ]
    insts.sort(key=lambda i: (not i.in_universe, i.isin))
    if max_instruments:
        insts = insts[:max_instruments]
    out: dict[str, list[Instrument]] = defaultdict(list)
    without = 0
    for i in insts:
        g = groups.get(i.mic)
        if g is None:
            without += 1
            continue
        out[g].append(i)
    return out, without


def load_panels(
    s: Session, config: LoadedConfig, calendars: MarketCalendars, prereg: Prereg, start: date, end: date,
    universe: str, max_instruments: int | None, cache_dir: Path | None, progress: Progress | None = None,
    drop_illiquid: bool = True, default_groups: bool = False,
) -> tuple[list[Panel], PanelMeta]:  # fmt: skip
    """Panels of the venue groups. ``drop_illiquid`` removes the instruments never liquid on the period (the
    registration); the calibration keeps them (every run-2 trade must be locatable)."""
    import pandas as pd

    p = config.params
    tax = _tax(config)
    u = p.universe or {}
    pea_countries = set((p.pea or {}).get("eligible_countries") or [])
    ttf_rate = float(tax.get("ttf_rate") or 0.0)
    ttf_cap = float(tax.get("ttf_cap_threshold_eur") or 1e9)
    ftt = dict(tax.get("ftt_by_country") or {})
    fx_fee = float(((p.brokers or {}).get("saxo") or {}).get("fx_fee_pct") or 0.0)
    min_turn = float(u.get("min_adv_eur_pea", 500_000))
    idx_isins = (getattr(p, "regime", None) or {}).get("index_isins") or {}
    cac = _index_series(s, idx_isins.get("cac40"), start, end)
    stoxx = _index_series(s, idx_isins.get("stoxx600"), start, end)
    rows_total = s.scalar(select(func.count(PriceEod.id))) or 0
    last_day = s.scalar(select(func.max(PriceEod.day)))
    vgroups = None if default_groups else prereg.venue_groups  # the run-2 replay is always on P0 / P1 / P2
    by_group, without = select_instruments(s, config, calendars, universe, max_instruments, vgroups)
    static = dict(prereg.a("fx_static_eur_per_unit") or {})
    panels: list[Panel] = []
    counts: dict[str, int] = {}
    dropped = 0
    delisted = 0
    fx_fb: set[str] = set()
    fx_static: dict[str, set[Any]] = defaultdict(set)
    earn_counts: dict[str, dict[str, int]] = {}
    letter_counts: dict[str, dict[str, int]] = {}
    lo_all: date | None = None
    hi_all: date | None = None
    for grp in list(vgroups) if vgroups else GROUPS:
        insts = by_group.get(grp) or []
        own = own_index_id(config, calendars, vgroups[grp]) if vgroups else None
        own_idx = _index_series(s, own, start, end) if own else None
        if not insts:
            continue
        key = _cache_key(
            ["v2", grp, start, end, universe, max_instruments, rows_total, last_day, sorted(i.isin for i in insts)]
        )
        path = cache_dir / f"{grp}_{start}_{end}_{key}.npz" if cache_dir else None
        if path is not None and path.exists():
            z = np.load(path, allow_pickle=False)
            days, isins = z["days"], [str(x) for x in z["isins"]]
            arrays = {k: z[k] for k in ("open", "high", "low", "close", "adj_close", "volume", "vintage")}
            if progress:
                progress(f"{grp} : panneau lu depuis le cache ({len(isins)} valeurs)")
        else:
            if progress:
                progress(f"{grp} : lecture des cours de {len(insts)} valeurs")
            days, isins, arrays = _read_price_panel(s, [i.isin for i in insts], start, end)
            if not isins:
                continue
            # adjusted close missing → the last known adjustment factor (1 before any)
            fac = arrays["adj_close"] / arrays["close"]
            fac = pd.DataFrame(fac).ffill().fillna(1.0).to_numpy()
            arrays["adj_close"] = np.where(np.isfinite(arrays["close"]), arrays["close"] * fac, np.nan)
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(path, days=days, isins=np.array(isins), **arrays)
        meta = {i.isin: i for i in insts}
        known = [k for k, x in enumerate(isins) if x in meta]
        if len(known) < len(isins):  # no copy of the (T, N) arrays in the usual case
            isins = [isins[k] for k in known]
            arrays = {k: v[:, known] for k, v in arrays.items()}
        cur = {meta[x].currency or "EUR" for x in isins}
        fxs, fb, sd = _fx_series(s, cur, days, static)
        fx_fb |= set(fb)
        for k_, v_ in sd.items():
            fx_static[k_] |= set(v_.tolist())
        fx = (
            np.column_stack([fxs.get(meta[x].currency or "EUR", np.full(days.size, np.nan)) for x in isins])
            if isins
            else np.zeros((days.size, 0))
        )
        turn = arrays["close"] * arrays["volume"] * fx
        liquid = (np.nan_to_num(turn) >= min_turn).sum(axis=0) >= 30  # a 60-session median above needs ≥ 30 such days
        if not drop_illiquid:
            liquid = np.ones(len(isins), dtype=bool)
        keep = np.nonzero(liquid)[0]
        dropped += int((~liquid).sum())
        isins_k = [isins[k] for k in keep.tolist()]
        caps = np.array([float(meta[x].market_cap_eur or 0.0) for x in isins_k])
        last = {
            x: days[np.nonzero(np.isfinite(arrays["close"][:, k]))[0].max()]
            for x, k in zip(isins_k, keep.tolist(), strict=True)
        }
        cut = (days[-1] if days.size else np.datetime64(end, "D")) - np.timedelta64(30, "D")
        delisted += sum(1 for x in isins_k if not meta[x].active or last[x] < cut)
        panel = Panel(
            grp, days, tuple(isins_k),
            *(arrays[k][:, keep] for k in ("open", "high", "low", "close", "adj_close", "volume")),
            fx[:, keep],
            # the « local index » flag: Paris on the CAC 40 (registration n° 1); a one-venue group on its own index
            np.ones(len(isins_k), dtype=bool) if own_idx is not None else np.array([meta[x].mic == "XPAR" for x in isins_k], dtype=bool),
            np.array([bool(meta[x].small_cap) or (meta[x].market_cap_eur or 0) < float(u.get("small_cap_threshold_eur", 1e9)) for x in isins_k], dtype=bool),
            np.array([bool(meta[x].pea_eligible) or (meta[x].pea_eligible is None and x[:2] in pea_countries) for x in isins_k], dtype=bool),
            ttf_rates(isins_k, caps.tolist(), ttf_rate, ttf_cap),
            np.array([ftt_rate_any(ftt.get(x[:2])) for x in isins_k]),
            np.array([fx_fee if (meta[x].currency or "EUR") != "EUR" else 0.0 for x in isins_k]),
            np.array([bool(meta[x].in_universe) for x in isins_k], dtype=bool),
            own_idx if own_idx is not None else cac, stoxx, arrays["vintage"][:, keep].astype(np.int64),
        )  # fmt: skip
        if prereg.needs_earnings:
            panel, earn_counts[grp] = attach_earnings(s, panel, prereg)
        if prereg.needs_letters:
            panel, letter_counts[grp] = attach_letters(s, panel, prereg)
        panels.append(panel)
        counts[grp] = len(isins_k)
        if days.size:
            d0 = date.fromisoformat(str(days[0]))
            d1 = date.fromisoformat(str(days[-1]))
            lo_all = d0 if lo_all is None or d0 < lo_all else lo_all
            hi_all = d1 if hi_all is None or d1 > hi_all else hi_all
    static_days = {
        cur: {"jours": len(ds), "du": str(min(ds)), "au": str(max(ds)), "taux_statique": static.get(cur)}
        for cur, ds in sorted(fx_static.items())
        if ds
    }
    return panels, PanelMeta(lo_all, hi_all, counts, dropped, without, delisted, sorted(fx_fb), static_days,
                             earn_counts if prereg.needs_earnings else None,
                             letter_counts if prereg.needs_letters else None)  # fmt: skip


EARNINGS_MARGIN_DAYS = 100  # defaults when a plan does not state them (registration n° 3 states both)
EARNINGS_MAX_GAP_DAYS = 7


def attach_earnings(s: Session, panel: Panel, prereg: Prereg) -> tuple[Panel, dict[str, int]]:
    """Registration n° 3: the stored announcements of the panel's values (every source) as reaction-session,
    known-span and surprise matrices (``earnings_flags``); the margins are assumptions of the plan. Only the
    announcements within that margin of the panel's span are read (dates and EPS, never a price)."""
    a = prereg.assumptions
    margin = int(a.get("earnings_known_margin_days", EARNINGS_MARGIN_DAYS))
    if not panel.days.size:
        return panel, {}
    lo = date.fromisoformat(str(panel.days[0])[:10]) - timedelta(days=margin)
    hi = date.fromisoformat(str(panel.days[-1])[:10]) + timedelta(days=margin)
    rows = [
        EarningsRow(r.isin, np.datetime64(r.day, "D"), str(r.when or "unknown"), r.eps_actual, r.eps_estimate)
        for r in s.execute(
            select(EarningsEvent.isin, EarningsEvent.day, EarningsEvent.when, EarningsEvent.eps_actual,
                   EarningsEvent.eps_estimate)
            .where(EarningsEvent.isin.in_(list(panel.isins)), EarningsEvent.day >= lo, EarningsEvent.day <= hi)
        )
    ]  # fmt: skip
    fl = earnings_flags(panel.days, valid_bars(panel), panel.isins, rows, margin,
                        int(a.get("earnings_reaction_max_gap_days", EARNINGS_MAX_GAP_DAYS)))  # fmt: skip
    counts = {"publications_placees": fl.placed, "publications_non_placees": fl.unplaced,
              "publications_hors_periode": fl.outside, "heure_contradictoire": fl.conflicting_when,
              "lignes_sources": len(rows)}  # fmt: skip
    return replace(panel, earn_react=fl.react, earn_known=fl.known, earn_surprise_pos=fl.surprise_pos), counts


def attach_letters(s: Session, panel: Panel, prereg: Prereg) -> tuple[Panel, dict[str, int]]:
    """Registration n° 4 (X6): the stored letter values « achat » / « surveiller » of the panel's values with an
    explicit trigger, stop and target (closed list of level names), dated by the day they are known
    (``letters_known_after_paris``). Levels and dates only, never a price of the letter's text."""
    from app.db.models import NewsletterItem, NewsletterValue
    from app.domain.research.entry_policies import letter_config, letter_known_day

    after = str(prereg.assumptions.get("letters_known_after_paris", "17:30"))
    rows = s.execute(
        select(NewsletterValue.isin, NewsletterValue.levels, NewsletterItem.received_at)
        .join(NewsletterItem, NewsletterItem.id == NewsletterValue.item_id)
        .where(NewsletterValue.isin.in_(list(panel.isins)), NewsletterValue.direction.in_(("buy", "watch")))
    ).all()
    out: list[LetterLevels] = []
    for isin, levels, received in rows:
        cfg = letter_config(levels or {}) if isinstance(levels, dict) else None
        if cfg is None or received is None:
            continue
        out.append(LetterLevels(str(isin), np.datetime64(letter_known_day(received, after), "D"), *cfg))
    return replace(panel, letters=tuple(out)), {"valeurs_lues": len(rows), "avec_declencheur_stop_objectif": len(out)}


# --- run 2 --------------------------------------------------------------------------------------------


def load_run2(s: Session, run_id: int | None) -> tuple[list[F0.Run2Trade], int | None]:
    run = s.get(BacktestRun, run_id) if run_id else s.scalar(select(BacktestRun).order_by(BacktestRun.id.desc()))
    if run is None:
        return [], None
    rows = s.scalars(select(BacktestTrade).where(BacktestTrade.run_id == run.id))
    return [
        F0.Run2Trade(t.isin, t.detector, t.signal_day, t.entry_day, t.exit_day, t.entry, t.exit, t.stop, t.target,
                     t.reason, t.r_gross, t.r_net, t.mfe_r, t.mae_r, t.status == "admis" and not t.warned)
        for t in rows
    ], run.id  # fmt: skip


# --- ledger and files ----------------------------------------------------------------------------------


def read_ledger(out_dir: Path) -> list[dict[str, str]]:
    path = out_dir / "ledger.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def append_ledger(out_dir: Path, row: dict[str, Any]) -> None:
    path = out_dir / "ledger.csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with path.open(encoding="utf-8", newline="") as fh:
            header = next(csv.reader(fh), [])
        if header != LEDGER_FIELDS:  # a ledger of an older layout: rewritten with the new columns (rows kept)
            old = read_ledger(out_dir)
            with path.open("w", encoding="utf-8", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
                w.writeheader()
                w.writerows([{k: r.get(k) or "" for k in LEDGER_FIELDS} for r in old])
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LEDGER_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in LEDGER_FIELDS})


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def _write(out_dir: Path, name: str, report: dict[str, Any], text: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    j, t = out_dir / f"{name}.json", out_dir / f"{name}.txt"
    j.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=_json_default), encoding="utf-8")
    t.write_text(text, encoding="utf-8")
    return j, t


def _json_default(x: Any) -> Any:
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, set):
        return sorted(x)
    if isinstance(x, date):
        return x.isoformat()
    return str(x)


CALIBRATION_MISSING = "INVALIDE : aucune calibration de ce plan et de ce run couvrant la fin des données"


def select_calibration(
    calibrations: Sequence[tuple[str, Mapping[str, Any]]], plan_sha256: str, run_id: int | None, data_end: date | None,
) -> tuple[str, str]:  # fmt: skip
    """The calibration a report relies on: the newest one (by file name) of the same plan sha256 and the same
    run id whose price data reach the report's data end. Returns (verdict, label); no such calibration →
    INVALIDE (never the newest file of another plan or run)."""
    ok = []
    for name, d in calibrations:
        end = d.get("fin_donnees")
        if d.get("plan_sha256") != plan_sha256 or run_id is None or d.get("run") != run_id or not end:
            continue
        if data_end is not None and date.fromisoformat(str(end)) < data_end:
            continue
        ok.append((name, d))
    if not ok:
        hint = f" (research calibrate --run {run_id})" if run_id is not None else " (aucun backtest de référence)"
        return "INVALIDE", CALIBRATION_MISSING + hint
    name, d = sorted(ok, key=lambda x: x[0])[-1]
    verdict = "VALIDE" if d.get("verdict") == "VALIDE" else "INVALIDE"
    return verdict, f"{verdict} (concordance {d.get('concordance')}, run {run_id}, {name})"


def _calibrations(out_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    cals = []
    for f in sorted(out_dir.glob("calibration_*.json")):
        try:
            cals.append((f.name, json.loads(f.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return cals


def calibration_status(out_dir: Path, plan_sha256: str, run_id: int | None, data_end: date | None) -> tuple[str, str]:
    return select_calibration(_calibrations(out_dir), plan_sha256, run_id, data_end)


def plan_calibration(calibrations: Sequence[tuple[str, Mapping[str, Any]]], plan_sha256: str) -> tuple[str, str]:
    """Registration n° 7: the newest calibration of this plan (same sha256, any reference backtest) — its sealed
    unseal reads data far before the calibration's end, the engine check is what matters. None → INVALIDE."""
    ok = sorted((x for x in calibrations if x[1].get("plan_sha256") == plan_sha256), key=lambda x: x[0])
    if not ok:
        return "INVALIDE", CALIBRATION_MISSING + " (research calibrate --prereg <plan> --run N)"
    name, d = ok[-1]
    verdict = "VALIDE" if d.get("verdict") == "VALIDE" else "INVALIDE"
    return verdict, f"{verdict} (concordance {d.get('concordance')}, run {d.get('run')}, {name})"


def freeze_candidates(
    out_dir: Path, name: str, prereg: Prereg, res: Mapping[str, Any], calibration_verdict: str
) -> tuple[str, list[str]] | None:  # fmt: skip
    """Write the frozen candidate file of a run (its sha256 and cells go to the ledger, the only proof an unseal
    accepts) — never when the calibration is not VALIDE (the report is not exploitable)."""
    cells = res.get("cellules") or {}
    cands = [c for c in res.get("candidats") or [] if (cells.get(c) or {}).get("niveau") == Tier.CANDIDATE.value]
    if not cands or calibration_verdict != "VALIDE":
        return None
    disc = {c: {k: cells[c].get(k) for k in ("n", "mean_r", "mean_eur_300", "mean_excess", "profit_factor",
                                            "mean_pct_notionnel")} for c in cands}  # fmt: skip
    for c in cands:  # registration n° 4: the paired interval and the (b) criteria of the discovery
        disc[c].update({k: cells[c][k] for k in ("ic", "decision_b") if k in cells[c]})
    body = yaml.safe_dump(
        {"prereg_id": prereg.id, "prereg_sha256": prereg.sha256, "cells": cands,
         "niveaux": {c: Tier.CANDIDATE.value for c in cands}, "decouverte": disc},
        allow_unicode=True, sort_keys=False,
    ).encode("utf-8")  # fmt: skip
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}_candidates.yaml").write_bytes(body)
    return file_sha256(body), cands


def run_ledger_row(
    name: str, prereg: Prereg, res: Mapping[str, Any], universe: str, report: str,
    frozen: tuple[str, list[str]] | None, calibration_verdict: str,
) -> dict[str, Any]:  # fmt: skip
    return {
        "timestamp": name, "kind": "run", "prereg_id": prereg.id, "prereg_sha256": prereg.sha256,
        "cell_set_sha256": prereg.cell_set_hash, "m_total": prereg.total_cells,
        "cells_run": (res.get("comptes_cellules") or {}).get("total_execute", ""),
        "candidates": len(res.get("candidats") or []), "universe": universe, "report": report,
        "candidates_sha256": frozen[0] if frozen else "", "candidate_cells": CELL_SEP.join(frozen[1]) if frozen else "",
        "calibration": calibration_verdict,
    }  # fmt: skip


LIMITS = [
    "liquidité point-in-time, PAS sans biais de survie : la table des instruments vient des listes d'aujourd'hui",
    "capitalisations, petites capitalisations, TTF et secteurs : valeurs d'aujourd'hui (signalé)",
    "période scellée « semi-propre » : le run 2 a vu les détecteurs d2–d7 sur cette période",
    "cours non ajustés pour les exécutions ; dividendes déduits du facteur d'ajustement (crédit brut / net / exclu)",
    "rang de force relative recalculé par groupe de places sur les seules valeurs éligibles (≠ features_daily du run 2)",
    "contrôle apparié = moyenne de toutes les valeurs de la même strate (limite des 20 tirages du plan)",
    "avant impôt (CTO), sauf la variante « dividendes nets » ; aucun résultat ne change un paramètre sans ADR",
]


ENTRY_LIMITS = [
    "politiques d'entrée (n° 4) : barres quotidiennes — l'ordre des cours dans une séance est inconnu : stop d'abord, une "
    "limite exécutée en séance ne revendique pas le plus haut ; stop d'achat annulé par une ouverture au-dessus de son prix "
    "maximal (jamais poursuivi) ; pas de cotation approché ; aucune file d'attente ni exécution partielle",
    "politiques d'entrée : contrôles appariés de strate sans objet (géométrie de chaque configuration) — la référence est "
    "le placebo X1 (même chaîne sur des dates au hasard) ; la découverte 2017-2026 est aussi lue par le rejeu exploratoire",
    "lettres (X6) : historique importé après coup (newsletters-history), niveaux lus par l'IA puis ramenés à la liste "
    "fermée ; date de disponibilité = réception (après 17:30 Paris : séance suivante)",
]


EXIT_LIMITS = [
    "règles de sortie (n° 6) : barres quotidiennes — stop d'abord ; la vente partielle n'a jamais lieu dans une séance où le "
    "stop est touché ni sur une exécution en séance à la limite ; le stop remonté au prix de revient est supposé touché "
    "après la vente quand le plus bas l'atteint dans la même séance ; aucune file d'attente",
    "règles de sortie : chaque paire partage la même exécution ; une exécution hors du cadre de XP (stop glissé < prix < "
    "objectif 1) est écartée des deux côtés et comptée ; la découverte 2017-2026 a déjà été lue pour d7 (11 sorties du "
    "n° 1, XP et X8 du n° 4) : pénalité DSR déclarée",
]


def replication_label(ledger: Sequence[Mapping[str, str]], prereg: Prereg, today: str) -> str:
    """Registration n° 6: « propre » unless a plan sharing its old replication (``replication_shared_with``) was
    unsealed on another day than ``today`` (YYYYMMDD) — its entries and exits were then seen on that period."""
    shared = {str(x) for x in prereg.assumptions.get("replication_shared_with") or []}
    days = {
        str(r.get("timestamp", ""))[:8] for r in ledger if r.get("kind") == "unseal" and r.get("prereg_id") in shared
    }
    return "semi-propre (entrées et deux sorties vues)" if days - {today} else "propre"


def _header(meta: PanelMeta, universe: str, calibration: tuple[str, str], st: Settings, config: LoadedConfig,
            prereg: Prereg, run2_id: int | None, t0: float) -> dict[str, Any]:  # fmt: skip
    return {
        "genere_le": datetime.now(UTC).isoformat(timespec="seconds"),
        "debut_donnees": meta.start.isoformat() if meta.start else None,
        "fin_donnees": meta.end.isoformat() if meta.end else None,
        "univers": universe,
        "instruments": meta.instruments,
        "ecartes_jamais_liquides": meta.dropped_illiquid,
        "sans_groupe_de_places": meta.without_group,
        "radies_ou_inactifs": meta.delisted_or_inactive,
        "devises_taux_de_repli": meta.fx_fallback_currencies,
        "jours_devise_taux_statique": meta.fx_static_days,
        "calibration": calibration[1],
        "calibration_verdict": calibration[0],
        "run2": run2_id,
        "parametres": resolved_parameters(st, config),
        "hypotheses": {
            k: {"valeur": prereg.assumptions[k], "hypothese": prereg.assumption_flags.get(k, False)}
            for k in prereg.assumptions
        },  # fmt: skip
        "duree_s": round(time.time() - t0, 1),
        "limites": LIMITS
        + (earnings_limits(prereg) if prereg.needs_earnings else [])
        + (ENTRY_LIMITS if prereg.is_entry_plan else [])
        + (EXIT_LIMITS if prereg.is_exit_plan else []),
        **({"publications_resultats": meta.earnings} if meta.earnings is not None else {}),
        **({"lettres": meta.letters} if meta.letters is not None else {}),
    }


def earnings_limits(prereg: Prereg) -> list[str]:
    """Limits of a plan with earnings triggers (registration n° 3), with the plan's own assumptions."""
    a = prereg.assumptions
    margin = a.get("earnings_known_margin_days", EARNINGS_MARGIN_DAYS)
    leads = sorted({int(c.params.get("lead", 5)) for c in prereg.cells if c.trigger == "t15"})
    return [
        "publications de résultats : historique chargé après coup (EODHD ou Yahoo) ; une valeur n'est « connue » qu'à "
        f"moins de {margin} jours d'une publication chargée — hors de là, aucun signal t14 / t15 / t16",
        f"HYPOTHÈSE t15 : la date de publication est supposée connue {', '.join(map(str, leads)) or '—'} séances à "
        "l'avance (calendrier financier publié par la plupart des émetteurs européens), de même pour la fenêtre "
        "± 2 séances du contrôle t16 ; une publication imprévue (avertissement) la contredit",
        "séance de réaction : jour de publication (avant l'ouverture, en séance, heure inconnue) ou séance suivante "
        "(après la clôture) ; sources contradictoires sur l'heure : les deux séances marquées (comptées)",
    ] + (
        [
            "t18 (découpage « hors publication ») : aucune séance de réaction à ± 2 séances du saut — les dates des deux "
            "séances suivantes sont supposées connues (calendrier financier), comme pour t16"
        ]
        if any(c.trigger == "t18" and c.params.get("earnings") == "off" for c in prereg.cells)
        else []
    )


# --- commands --------------------------------------------------------------------------------------


def run(
    s: Session, config: LoadedConfig, calendars: MarketCalendars, prereg_path: str, out_dir: Path, start: date,
    end: date, universe: str = "in_universe", max_instruments: int | None = None, stage: str = "all",
    run2_id: int | None = None, progress: Progress | None = None, use_cache: bool = True,
    max_f0_candidates: int = DEFAULT_MAX_F0_CANDIDATES,
) -> dict[str, Any]:  # fmt: skip
    t0 = time.time()
    prereg = load_prereg(prereg_path)
    check_frozen(prereg)  # registration n° 7: a BROUILLON is never run
    check_run_start(prereg, start)  # registration n° 3: no bar of the sealed old replication period is read
    ledger = read_ledger(out_dir)
    check_ledger(ledger, prereg)
    st, prod = resolve_settings(config, prereg, universe)
    run2, rid = load_run2(s, run2_id)
    pre = calibration_status(out_dir, prereg.sha256, rid, None)
    if pre[0] != "VALIDE":  # 26/10/2026: a run against the wrong reference backtest is refused before any work
        runs = sorted({str(d.get("run")) for _, d in _calibrations(out_dir) if d.get("plan_sha256") == prereg.sha256})
        raise PreregError(
            f"{pre[1]} — backtest de référence de ce run : n° {rid}"
            + (
                f" ; calibrations de ce plan : run(s) {', '.join(runs)} → relancer avec --run {runs[-1]}"
                if runs
                else ""
            )
        )
    panels, meta = load_panels(s, config, calendars, prereg, start, end, universe, max_instruments,
                               out_dir / "cache" if use_cache else None, progress)  # fmt: skip
    st, insider_cov = insider_context(s, st, prereg, panels)  # registration n° 5 (AMF declarations), else no-op
    res = run_registration(panels, prereg, st, run2, prod, stage, None, progress, True, max_f0_candidates)
    cal = calibration_status(out_dir, prereg.sha256, rid, meta.end)
    header = _header(meta, universe, cal, st, config, prereg, rid, t0)
    report = build_report(prereg, res, header)
    report, insider_txt = insider_report(report, res, prereg.thresholds, insider_cov)  # n° 5: coverage, A1 abstention
    if prereg.sealed_confirmation:  # registration n° 7: the discovery was read before the plan — descriptive only
        report["A_resume"]["titre"] = f"{DESCRIPTIVE_PREFIX} — {report['A_resume']['titre']}"
    name = _stamp()
    j, t = _write(out_dir, name, report, render_fr(report) + insider_txt)
    frozen = None if prereg.sealed_confirmation else freeze_candidates(out_dir, name, prereg, res, cal[0])
    append_ledger(out_dir, run_ledger_row(name, prereg, res, universe, j.name, frozen, cal[0]))
    return {"json": str(j), "txt": str(t), "titre": report["A_resume"]["titre"], "candidats": res.get("candidats") or [],
            "candidats_figes": frozen is not None, "calibration": cal[0], "duree_s": round(time.time() - t0, 1)}  # fmt: skip


def decompose(s: Session, config: LoadedConfig, calendars: MarketCalendars, run_id: int | None, out_dir: Path,
              progress: Progress | None = None) -> dict[str, Any]:  # fmt: skip
    """(a) of F0: the run-2 decomposition alone (needs the prices for the ex-dividend dates)."""
    run2, rid = load_run2(s, run_id)
    if not run2:
        return {"erreur": "aucun backtest enregistré"}
    prereg_like = _default_prereg()
    st, _ = resolve_settings(config, prereg_like, "in_universe")
    isins = sorted({t.isin for t in run2})
    panels, _ = _panels_for(s, config, calendars, prereg_like, isins, run2, progress)
    divs: dict[str, list[date]] = {}
    base = date(1970, 1, 1).toordinal()
    for k, p in enumerate(panels):
        g = prepare_group(k, p, st)
        for col, isin in enumerate(p.isins):
            pos = np.nonzero(g.bars.div[:, col] > 0)[0]
            t = g.f.pk.order[pos, col]
            divs[isin] = [
                date.fromordinal(base + int(x)) for x in p.days[t].astype("datetime64[D]").astype(np.int64).tolist()
            ]
    min_n = int(prereg_like.thresholds["report_min_n"])
    out = {"run": rid, "decomposition": suppress_deep(F0.decompose(run2, divs), min_n)}
    assert_no_price_fields(out)
    name = f"decomposition_run{rid}_{_stamp()}"
    text = _decomp_text(out)
    _write(out_dir, name, out, text)
    out["fichier"] = str(out_dir / f"{name}.json")
    return out


def _decomp_text(d: dict[str, Any]) -> str:
    lines = [f"Décomposition du backtest n° {d['run']} (trades de production)"]
    for det, v in d["decomposition"].items():
        if "supprime" in v:
            lines.append(f"\n{det} : {v['supprime']} (n {v.get('n')})")
            continue
        lines.append(
            f"\n{det} : n {v['n']} ; R brut {v['r_brut_moyen']:+.3f} ; coûts {v['cout_moyen_r']:.3f} R ; R net {v['r_net_moyen']:+.3f}"
        )
        for k, x in v["sorties"].items():
            lines.append(
                f"  sortie {k} : {x['part']:.0%} des trades, {x['r_total']:+.1f} R au total ({x['r_moyen']:+.2f} R en moyenne)"
            )
        lines.append(
            f"  perdants détenus pendant un détachement : {v['perdants_avec_detachement']} ({v['part_perdants_avec_detachement']})"
        )
        lines.append(
            f"  MAE des gagnants (quartiles) {v['mae_quartiles_gagnants']} ; MFE des perdants {v['mfe_quartiles_perdants']}"
        )
        lines.append(
            f"  perdants passés par +1 R : {v['perdants_passes_par_1r']} ; par +2 R : {v['perdants_passes_par_2r']}"
        )
    return "\n".join(lines) + "\n"


def _default_prereg() -> Prereg:
    return load_prereg(Path(__file__).resolve().parents[2] / "research" / "prereg" / "2026-10-27_reg1.yaml")


def _panels_for(s: Session, config: LoadedConfig, calendars: MarketCalendars, prereg: Prereg, isins: list[str],
                run2: list[F0.Run2Trade], progress: Progress | None) -> tuple[list[Panel], PanelMeta]:  # fmt: skip
    start = min(t.signal_day for t in run2) - timedelta(days=420)
    end = max(t.exit_day for t in run2) + timedelta(days=10)
    panels, meta = load_panels(s, config, calendars, prereg, start, end, "all", None, None, progress,
                               drop_illiquid=False, default_groups=True)  # fmt: skip
    keep = set(isins)
    out = []
    for p in panels:
        cols = np.array([k for k, x in enumerate(p.isins) if x in keep], dtype=np.int64)
        if cols.size:
            out.append(p.select(cols))
    return out, meta


def calibrate(s: Session, config: LoadedConfig, calendars: MarketCalendars, run_id: int | None, prereg_path: str,
              out_dir: Path, progress: Progress | None = None) -> dict[str, Any]:  # fmt: skip
    """Golden replay: the vectorised engine in run-2 conventions against the stored run-2 trades."""
    prereg = load_prereg(prereg_path)
    check_frozen(prereg)  # registration n° 7: a BROUILLON is never calibrated (its sha256 is not the frozen one)
    run2, rid = load_run2(s, run_id)
    if not run2:
        return {"verdict": "INVALIDE", "erreur": "aucun backtest enregistré"}
    st, _ = resolve_settings(config, prereg, "in_universe")
    panels, _ = _panels_for(s, config, calendars, prereg, sorted({t.isin for t in run2}), run2, progress)
    holds = {d: F0.detector_hold(d, st.detectors) for d in {t.detector for t in run2}}
    parts: dict[str, list[Any]] = defaultdict(list)
    for k, p in enumerate(panels):
        g = prepare_group(k, p, st)
        for key, v in F0.calibration_parts(g, run2, st, holds).items():
            parts[key] += v
    verdict = F0.calibration_verdict(parts, Counter(t.detector for t in run2))
    verdict["run"] = rid
    verdict["plan_sha256"] = prereg.sha256
    last_day = s.scalar(select(func.max(PriceEod.day)))
    verdict["fin_donnees"] = last_day.isoformat() if last_day else None  # price data the replay was made with
    name = f"calibration_{_stamp()}"
    text = (f"Calibration (rejeu du backtest n° {rid}) : {verdict['verdict']}\n"
            f"concordance {verdict['concordance']} sur les {verdict['trades_run2']} trades du run 2 "
            f"({verdict['trades_localises']} localisés)\n"
            + "".join(f"  {d} : {v}\n" for d, v in verdict["par_detecteur"].items()) + f"règle : {verdict['regle']}\n"
            + f"portée : {verdict['portee']}\n")  # fmt: skip
    _write(out_dir, name, verdict, text)
    verdict["fichier"] = str(out_dir / f"{name}.json")
    return verdict


def unseal(s: Session, config: LoadedConfig, calendars: MarketCalendars, prereg_path: str, candidates_path: str | None,
           out_dir: Path, start: date, end: date, universe: str = "all", progress: Progress | None = None) -> dict[str, Any]:  # fmt: skip
    """The single opening of the sealed period for a frozen candidate file."""
    prereg = load_prereg(prereg_path)
    check_frozen(prereg)
    if prereg.sealed_confirmation:  # registration n° 7: the plan's own cells, no candidate file
        return unseal_confirmatory(
            s, config, calendars, prereg, candidates_path, out_dir, start, end, universe, progress
        )
    ledger = read_ledger(out_dir)
    raw, sha = None, None
    if candidates_path and Path(candidates_path).exists():
        b = Path(candidates_path).read_bytes()
        raw, sha = yaml.safe_load(b), file_sha256(b)
    ids = check_unseal(raw, sha, prereg, ledger)
    check_unseal_span(prereg, start, end)  # an old replication period needs its warm-up and its whole span
    check_forward_unseal(s, prereg_path, prereg, ledger, end)  # registration n° 5: the forward confirmation rule
    st, prod = resolve_settings(config, prereg, universe)
    st = with_insiders(s, st, prereg)
    panels, meta = load_panels(s, config, calendars, prereg, start, end, universe, None, out_dir / "cache", progress)
    res = run_registration(panels, prereg, st, (), prod, "0", ids, progress, False)
    disc = (raw or {}).get("decouverte") or {}
    verdicts = {}
    label = replication_label(ledger, prereg, _stamp()[:8]) if prereg.is_exit_plan else None
    for cid in ids:
        c = res["cellules"].get(cid, {})
        d = disc.get(cid) or {}
        if label is not None:  # registration n° 6: the paired Δ per trade, the replication labelled
            verdicts[cid] = exit_unseal_verdict(c, d, prereg.thresholds, label)
            continue
        if prereg.is_entry_plan:  # registration n° 4: the paired Δ (a) and the tested arm's A2 (b)
            verdicts[cid] = entry_unseal_verdict(c, d, prereg.thresholds)
            continue
        ok = unseal_confirmed(c, d, prereg.thresholds)  # A2 in full (incl. % of notional) and the same sign
        verdicts[cid] = {"n": c.get("n"), "mean_r": c.get("mean_r"), "mean_eur_300": c.get("mean_eur_300"),
                         "profit_factor": c.get("profit_factor"), "mean_pct_notionnel": c.get("mean_pct_notionnel"),
                         "decouverte_mean_r": d.get("mean_r"),
                         "verdict": "confirmé — ADR de mise en production à 50 % du risque par trade" if ok else "non confirmé"}  # fmt: skip
    a, b = prereg.sealed_window
    label = f"réplication ancienne {a} → {b}" if prereg.replication is not None else "scellée"
    out = {"plan": prereg.id, "plan_sha256": prereg.sha256, "candidats_sha256": sha, "periode": label,
           "fin_donnees": meta.end.isoformat() if meta.end else None, "verdicts": verdicts}  # fmt: skip
    if res.get("couverture_resultats"):
        out["couverture_resultats"] = res["couverture_resultats"]
    name = f"descellement_{_stamp()}"
    text = f"Descellement de la période {label}\n" + "".join(f"  {k} : {v}\n" for k, v in verdicts.items())
    text += "".join(
        f"  couverture des publications {g} : {v}\n" for g, v in (out.get("couverture_resultats") or {}).items()
    )
    _write(out_dir, name, out, text)
    append_ledger(out_dir, {"timestamp": name, "kind": "unseal", "prereg_id": prereg.id, "prereg_sha256": prereg.sha256,
                            "cell_set_sha256": prereg.cell_set_hash, "candidates": len(ids), "universe": universe,
                            "report": f"{name}.json", "candidates_sha256": sha or "",
                            "candidate_cells": CELL_SEP.join(ids)})  # fmt: skip
    return out


def unseal_confirmatory(
    s: Session, config: LoadedConfig, calendars: MarketCalendars, prereg: Prereg, candidates_path: str | None,
    out_dir: Path, start: date, end: date, universe: str = "all", progress: Progress | None = None,
) -> dict[str, Any]:  # fmt: skip
    """Registration n° 7: the single opening of the sealed old replication period **by the plan's declared cells**
    (``replication.mode: confirmatory``) — no candidate file. Refused before any read: a candidate file, another
    universe than ``all`` (A9 needs the point-in-time universe), an earlier unseal of the plan (sha256 or id), a span
    without the warm-up or the last exits, no VALIDE calibration of this plan (the opening is unique: never spent on
    an unchecked engine). Then the whole registration machinery on the period (``sealed_plan``), the verdict of each
    confirmatory cell (``sealed_verdicts``), the full aggregated report and one ``unseal`` ledger row."""
    t0 = time.time()
    if universe != "all":
        raise PreregError("descellement refusé : --universe all exigé (A9 : univers point-in-time)")
    ledger = read_ledger(out_dir)
    check_ledger(ledger, prereg)
    check_sealed_once(prereg, ledger, candidates_path)
    check_unseal_span(prereg, start, end)
    cal = plan_calibration(_calibrations(out_dir), prereg.sha256)
    if cal[0] != "VALIDE":
        raise PreregError(f"descellement refusé : {cal[1]} — l'ouverture est unique, le moteur doit être calibré avant")
    st, prod = resolve_settings(config, prereg, universe)
    panels, meta = load_panels(s, config, calendars, prereg, start, end, universe, None, out_dir / "cache", progress)
    sp = sealed_plan(prereg)
    res = run_registration(panels, sp, st, (), prod, "all", None, progress, False)
    verdict = sealed_verdicts(res, sp)
    res["titre"] = verdict["titre"]
    res["candidats"] = list(verdict["confirmes"])
    res["surveillance"] = []
    res["segment"] = "période scellée, confirmatoire"
    header = _header(meta, universe, cal, st, config, prereg, None, t0)
    report = build_report(sp, res, header)
    view = report_view(verdict, int(prereg.thresholds["report_min_n"]))  # rows under 20 masked, verdicts kept
    report["V_verdict_periode_scellee"] = view
    assert_no_price_fields(report)
    name = f"descellement_{_stamp()}"
    j, t = _write(out_dir, name, report, render_fr(report) + render_sealed(view))
    ids = [c.id for c in prereg.confirmatory]
    append_ledger(out_dir, {"timestamp": name, "kind": "unseal", "prereg_id": prereg.id, "prereg_sha256": prereg.sha256,
                            "cell_set_sha256": prereg.cell_set_hash, "m_total": prereg.total_cells,
                            "cells_run": len(res.get("cellules") or {}), "candidates": len(verdict["confirmes"]),
                            "universe": universe, "report": j.name, "candidates_sha256": "",
                            "candidate_cells": CELL_SEP.join(ids), "calibration": cal[0]})  # fmt: skip
    return {"plan": prereg.id, "plan_sha256": prereg.sha256, "periode": verdict["periode"], "titre": verdict["titre"],
            "json": str(j), "txt": str(t), "verdicts": view["confirmatoires"], "placebo": view["placebo"],
            "duree_s": round(time.time() - t0, 1)}  # fmt: skip


__all__ = ["PreregError", "calibrate", "decompose", "run", "unseal", "unseal_confirmatory", "load_prereg"]
