# ruff: noqa: E501
"""Registration n° 5 (docs/34 §34.12): managers' dealing declarations (AMF BDIF, MAR art. 19) as research
triggers. Pure: the declarations come as DTOs (``InsiderRow``), the panel's calendar places them.

Trigger ``t17`` (one function, its variants are cell parameters of the plan):

- a declaration is retained only when ``parse_status = parsed``, it carries an ISIN (the declaration's, else the
  stored name mapping), its nature is the side's (``acquisition`` for purchases, ``cession`` for sales; ``mixte``
  never), the declarant's role is in the plan's executive closed list (or any role), every transaction is on
  shares (``insider_share_wording``) with no excluded wording (options, free shares, subscriptions…), every
  transaction took place on a trading venue (``insider_on_market_wording``; off-market or unknown venue →
  excluded), price and volume are both parsed, the currency is the euro (stated, or not stated on a value quoted
  in euros) and price × volume ≥ ``min_amount_eur``. Unknown → excluded and counted, never guessed;
- it is placed on the value's first own session **on** the publication day when the AMF published it at or
  before ``insider_close_cutoff_paris`` (Paris time), else on the first own session **after** it (published after
  the close or on a non-session day); a session more than ``insider_max_gap_days`` calendar days later is not a
  placement (suspension, not yet listed); a parsed price farther than ``insider_max_price_ratio`` from the close of
  the placement session is a parse error (excluded, counted);
- one event per value every ``dedup_sessions`` own sessions (the first one, outcome-free); the cluster variant
  keeps the session where at least ``cluster_min_roles`` distinct executive roles have bought within
  ``cluster_window_sessions`` own sessions (names are never stored: distinct people are told apart by their
  role, conservatively); the shifted control moves each event ``shift_sessions`` own sessions later;
- signals only on Paris-listed values (``Panel.is_xpar``: the plan's own Paris group, or the Paris columns of P0
  in the shadow tracking) and on eligible bars.

The signal is known at the close of its session; the order works from the next open (E_open), so the entry is
always after the publication.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from app.domain.research.panel import I64, Bool, Features, Panel

INSIDER_TRIGGERS = frozenset({"t17"})
PARIS = ZoneInfo("Europe/Paris")
SIDES = {"purchase": "acquisition", "sale": "cession"}
OK = "retenue"
# exclusion reasons, in the order they are checked (the coverage funnel)
REASONS = ("non_analysee", "sans_isin", "autre_nature", "fonction", "instrument_ou_operation_exclus", "lieu_inconnu",
           "hors_marche", "lieu_non_reconnu", "montant_inconnu", "devise", "sous_seuil")  # fmt: skip
PLACEMENT = ("placees", "hors_panneau", "hors_paris", "sans_seance_proche", "prix_incoherent")


@dataclass(frozen=True)
class InsiderRow:
    """One stored declaration (``insider_declarations``), without any personal data."""

    isin: str | None
    published_at: datetime  # AMF publication time (aware; naive = UTC)
    parse_status: str
    role: str | None = None
    nature: str | None = None
    nature_detail: str | None = None
    venue: str | None = None
    price: float | None = None
    volume: float | None = None
    currency: str | None = None
    quote_currency: str | None = None  # the value's quote currency (instruments), for a form that states none
    isin_source: str | None = None  # declaration | name
    transactions: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class InsiderRules:
    """The plan's registered assumptions (``insider_*`` keys): never a code default."""

    executive_roles: frozenset[str]
    close_cutoff: time
    max_gap_days: int
    max_price_ratio: float
    share_wording: tuple[str, ...]
    excluded_wording: tuple[str, ...]
    on_market_wording: tuple[str, ...]
    off_market_wording: tuple[str, ...]


@dataclass(frozen=True)
class InsiderData:
    rows: tuple[InsiderRow, ...]
    rules: InsiderRules


@dataclass(frozen=True)
class CellRule:
    """The t17 parameters of one cell."""

    side: str = "purchase"
    roles: str = "executive"  # executive | any
    min_amount_eur: float = math.inf
    dedup_sessions: int = 20
    shift_sessions: int = 0
    cluster_min_roles: int = 0
    cluster_window_sessions: int = 10

    @staticmethod
    def of(params: Mapping[str, Any]) -> CellRule:
        side = str(params.get("side", "purchase"))
        roles = str(params.get("roles", "executive"))
        if side not in SIDES or roles not in ("executive", "any"):
            raise ValueError(f"t17 : côté {side!r} ou fonctions {roles!r} inconnus")
        return CellRule(side, roles, float(params["min_amount_eur"]), int(params.get("dedup_sessions", 20)),
                        int(params.get("shift_sessions", 0)), int(params.get("cluster_min_roles", 0)),
                        int(params.get("cluster_window_sessions", 10)))  # fmt: skip


def needs_insiders(cells: Sequence[Any]) -> bool:
    return any(getattr(c, "trigger", None) in INSIDER_TRIGGERS for c in cells)


def rules_from(assumptions: Mapping[str, Any]) -> InsiderRules:
    """The plan's assumptions → rules; a missing key is refused (the plan is the only source)."""
    a = assumptions
    hh, mm = (int(x) for x in str(a["insider_close_cutoff_paris"]).split(":"))
    return InsiderRules(
        frozenset(str(x) for x in a["insider_executive_roles"]), time(hh, mm), int(a["insider_max_gap_days"]),
        float(a["insider_max_price_ratio"]), tuple(str(x) for x in a["insider_share_wording"]),
        tuple(str(x) for x in a["insider_excluded_wording"]), tuple(str(x) for x in a["insider_on_market_wording"]),
        tuple(str(x) for x in a["insider_off_market_wording"]),
    )  # fmt: skip


# --- screening of one declaration --------------------------------------------------------------------------------


def plain(s: object) -> str:
    """Lower-case ASCII words (accents dropped; apostrophes and hyphens are separators)."""
    raw = re.sub(r"['’\-/]", " ", str(s or ""))
    text = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode().lower()
    return " ".join(text.split())


def _has(text: str, words: Sequence[str]) -> bool:
    return any(re.search(r"(?<![a-z0-9])" + re.escape(plain(w)), text) for w in words)


def _txs(r: InsiderRow) -> list[Mapping[str, Any]]:
    return [t for t in r.transactions if isinstance(t, Mapping)]


def screen(r: InsiderRow, rules: InsiderRules, rule: CellRule) -> str:
    """``retenue`` or the first exclusion reason (``REASONS``)."""
    if r.parse_status != "parsed":
        return "non_analysee"
    if not r.isin:
        return "sans_isin"
    if r.nature != SIDES[rule.side]:
        return "autre_nature"
    if rule.roles == "executive" and r.role not in rules.executive_roles:
        return "fonction"
    txs = _txs(r)
    if not txs:
        return "instrument_ou_operation_exclus"  # the instrument is unknown: never assumed to be a share
    for t in txs:
        inst = plain(t.get("instrument"))
        wording = " ".join(plain(x) for x in (t.get("nature_detail"), r.nature_detail, t.get("instrument")))
        if not inst or not _has(inst, rules.share_wording) or _has(wording, rules.excluded_wording):
            return "instrument_ou_operation_exclus"
        if t.get("nature") not in (None, SIDES[rule.side]):
            return "autre_nature"
    venues = [plain(t.get("venue") or r.venue) for t in txs]
    if any(not v for v in venues):
        return "lieu_inconnu"
    if any(_has(v, rules.off_market_wording) for v in venues):
        return "hors_marche"
    if not all(_has(v, rules.on_market_wording) for v in venues):
        return "lieu_non_reconnu"
    if r.price is None or r.volume is None or not (r.price > 0 and r.volume > 0):
        return "montant_inconnu"
    cur = r.currency or r.quote_currency
    if cur != "EUR":
        return "devise"
    if r.price * r.volume < rule.min_amount_eur:
        return "sous_seuil"
    return OK


# --- placement on the panel ----------------------------------------------------------------------------------------


def _paris(ts: datetime) -> datetime:
    return (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).astimezone(PARIS)


@dataclass
class Placed:
    ts: I64
    cols: I64
    roles: list[str]
    counts: dict[str, int] = field(default_factory=dict)


def place(p: Panel, valid: Bool, data: InsiderData, rule: CellRule) -> Placed:
    """Retained declarations of a cell rule on the panel: (calendar row, column, role) and the placement counts."""
    rules = data.rules
    col = {x: k for k, x in enumerate(p.isins)}
    day_num = p.days.astype("datetime64[D]").astype(np.int64)
    own: dict[int, tuple[I64, I64]] = {}
    ts: list[int] = []
    cols: list[int] = []
    roles: list[str] = []
    counts: Counter[str] = Counter()
    for r in data.rows:
        if screen(r, rules, rule) != OK:
            continue
        k = col.get(str(r.isin))
        if k is None:
            counts["hors_panneau"] += 1
            continue
        if not bool(p.is_xpar[k]):
            counts["hors_paris"] += 1
            continue
        if k not in own:
            rows_k = np.nonzero(valid[:, k])[0].astype(np.int64)
            own[k] = (rows_k, day_num[rows_k])
        rows_k, days_k = own[k]
        local = _paris(r.published_at)
        dn = int(np.datetime64(local.date(), "D").astype(np.int64))
        after = local.time() > rules.close_cutoff
        i = int(np.searchsorted(days_k, dn, side="right" if after else "left"))
        if i >= days_k.size or int(days_k[i]) - dn > rules.max_gap_days:
            counts["sans_seance_proche"] += 1
            continue
        t = int(rows_k[i])
        close = float(p.close[t, k])
        ratio = float(r.price or 0.0) / close if close > 0 else math.inf
        if not (math.isfinite(ratio) and 1.0 / rules.max_price_ratio <= ratio <= rules.max_price_ratio):
            counts["prix_incoherent"] += 1
            continue
        counts["placees"] += 1
        ts.append(t)
        cols.append(k)
        roles.append(str(r.role))
    return Placed(np.array(ts, dtype=np.int64), np.array(cols, dtype=np.int64), roles, dict(counts))


def _events(placed: Placed, f: Features, rule: CellRule) -> tuple[I64, I64]:
    """Own-session events of a cell: clusters, then one per value every ``dedup_sessions``, then the shift."""
    pk = f.pk
    by: dict[int, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    for t, c, role in zip(placed.ts.tolist(), placed.cols.tolist(), placed.roles, strict=True):
        by[c][int(pk.pos[t, c])].add(role)
    out_pos: list[int] = []
    out_col: list[int] = []
    for c, sessions in sorted(by.items()):
        pos = sorted(sessions)
        if rule.cluster_min_roles > 0:
            keep = []
            for q in pos:
                seen: set[str] = set()
                for q2 in pos:
                    if q - rule.cluster_window_sessions < q2 <= q:
                        seen |= sessions[q2]
                if len(seen) >= rule.cluster_min_roles:
                    keep.append(q)
            pos = keep
        nxt = -1
        for q in pos:
            if q < nxt:
                continue
            nxt = q + rule.dedup_sessions
            q2 = q + rule.shift_sessions
            if q2 < int(pk.n_bars[c]):
                out_pos.append(q2)
                out_col.append(c)
    cols = np.array(out_col, dtype=np.int64)
    rows = pk.order[np.array(out_pos, dtype=np.int64), cols] if cols.size else np.zeros(0, dtype=np.int64)
    return rows.astype(np.int64), cols


def t17_insider(p: Panel, f: Features, data: InsiderData | None, params: Mapping[str, Any]) -> Bool:
    """T17 (registration n° 5): the cell's retained declarations as events on eligible Paris bars. Without the
    declaration archive (``data`` None) nothing is known: no signal."""
    m = np.zeros(p.shape, dtype=np.bool_)
    if data is None:
        return m
    rule = CellRule.of(params)
    ts, cols = _events(place(p, f.valid, data, rule), f, rule)
    m[ts, cols] = True
    return np.asarray(m & f.elig & p.is_xpar[None, :], dtype=np.bool_)


# --- coverage (report header) and the A1 abstention -----------------------------------------------------------------


def coverage(rows: Sequence[InsiderRow], rules: InsiderRules, rule: CellRule) -> dict[str, Any]:
    """Per Paris month of publication: declarations loaded, parsed, acquisitions, executive acquisitions and those
    retained by the confirmatory rule; the exclusion funnel and the ISIN sources. Counts only."""
    months: dict[str, Counter[str]] = defaultdict(Counter)
    funnel: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    for r in rows:
        m = months[_paris(r.published_at).strftime("%Y-%m")]
        m["declarations"] += 1
        parsed = r.parse_status == "parsed"
        m["analysees"] += int(parsed)
        m["acquisitions"] += int(parsed and r.nature == "acquisition")
        m["acquisitions_dirigeants"] += int(parsed and r.nature == "acquisition" and r.role in rules.executive_roles)
        why = screen(r, rules, rule)
        m["retenues"] += int(why == OK)
        funnel[why] += 1
        sources[r.isin_source or ("sans" if not r.isin else "autre")] += 1
    n = len(rows)
    parsed_n = sum(c["analysees"] for c in months.values())
    return {
        "declarations": n,
        "taux_analyse": round(parsed_n / n, 3) if n else None,
        "retenues_regle_c1": funnel[OK],
        "entonnoir": {k: funnel[k] for k in (*REASONS, OK)},
        "origine_isin": dict(sorted(sources.items())),
        "par_mois": {k: dict(v) for k, v in sorted(months.items())},
        "mois_sans_declaration": empty_months(sorted(months)),
    }


def empty_months(present: Sequence[str]) -> list[str]:
    """Months between the first and the last loaded one with no declaration (a hole in the archive)."""
    if not present:
        return []
    y, m = (int(x) for x in present[0].split("-"))
    out, have = [], set(present)
    while f"{y:04d}-{m:02d}" <= present[-1]:
        if f"{y:04d}-{m:02d}" not in have:
            out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def a1_abstentions(cells: Mapping[str, Mapping[str, Any]], th: Mapping[str, Any]) -> dict[str, str]:
    """Confirmatory insider cells whose sample does not meet A1 (n, weeks, trades per fold): « non concluant »,
    never « pas d'avantage »."""
    a1 = th["a1"]
    out = {}
    for cid, c in cells.items():
        if c.get("tier") != "confirmatory" or c.get("declencheur") not in INSIDER_TRIGGERS:
            continue
        if (c.get("criteres") or {}).get("A1"):
            continue
        out[cid] = (f"non concluant : échantillon sous A1 (n {c.get('n', 0)} / {a1['event_min_n']}, semaines "
                    f"{c.get('semaines', 0)} / {a1['event_min_weeks']}, pli le plus petit {c.get('pli_min_n', 0)} / "
                    f"{a1['fold_min_trades']})")  # fmt: skip
    return out


NON_CONCLUSIVE = "NON CONCLUANT (A1 non atteint : aucune conclusion sur les achats de dirigeants)"


def insider_report(report: dict[str, Any], res: Mapping[str, Any], th: Mapping[str, Any],
                   cov: Mapping[str, Any] | None) -> tuple[dict[str, Any], str]:  # fmt: skip
    """The coverage in the header and the A1 abstention in the summary (title prefixed when every confirmatory
    insider cell abstains); returns the report and the French text added to the .txt."""
    if cov is None:
        return report, ""
    report["entete"]["declarations_dirigeants"] = dict(cov)
    abst = a1_abstentions(res.get("cellules") or {}, th)
    a = report["A_resume"]
    a.setdefault("verdicts_familles", {}).update(abst)
    conf = [cid for cid, c in (res.get("cellules") or {}).items()
            if c.get("tier") == "confirmatory" and c.get("declencheur") in INSIDER_TRIGGERS]  # fmt: skip
    if conf and all(cid in abst for cid in conf):
        a["titre"] = f"{NON_CONCLUSIVE} — {a['titre']}"
    lines = ["", "Déclarations des dirigeants (AMF BDIF) — couverture de l'archive",
             f"  déclarations chargées : {cov.get('declarations')} ; taux d'analyse des PDF : {cov.get('taux_analyse')}",
             f"  retenues par la règle de C1 : {cov.get('retenues_regle_c1')} ; entonnoir : {cov.get('entonnoir')}",
             f"  origine de l'ISIN : {cov.get('origine_isin')} ; placement : {cov.get('placement')}",
             f"  mois sans aucune déclaration (trou de l'archive) : {cov.get('mois_sans_declaration') or 'aucun'}",
             "  par mois (déclarations · analysées · acquisitions · acquisitions de dirigeants · retenues) :"]  # fmt: skip
    for mth, c in (cov.get("par_mois") or {}).items():
        lines.append(f"    {mth} : {c.get('declarations', 0)} · {c.get('analysees', 0)} · {c.get('acquisitions', 0)} · "
                     f"{c.get('acquisitions_dirigeants', 0)} · {c.get('retenues', 0)}")  # fmt: skip
    lines += [f"  {cid} : {v}" for cid, v in abst.items()]
    return report, "\n".join(lines) + "\n"


# --- the forward confirmation (the only replication this plan has) ----------------------------------------------------


def forward_open_from(verdict_day: date, months: int, end_margin_sessions: int) -> date:
    """First data end at which the forward period may be opened: the verdict day plus ``months`` months of
    signals, plus the time to hold the last ones (the plan's end margin, ≈ × 7/5 calendar days)."""
    y, m = verdict_day.year + (verdict_day.month - 1 + months) // 12, (verdict_day.month - 1 + months) % 12 + 1
    day = min(verdict_day.day, 28)
    return date(y, m, day) + timedelta(days=math.ceil(end_margin_sessions * 7 / 5) + 10)


def check_forward(forward: Mapping[str, Any], plan_sha256: str, ledger: Sequence[Mapping[str, str]], data_end: date,
                  shadow_signals: int) -> None:  # fmt: skip
    """The single opening of the forward period needs a ledgered verdict (the first ``run`` of this plan), at least
    ``min_months_after_verdict`` months of signals after it with their exits, and at least
    ``min_shadow_signals`` signals of the confirmatory cell recorded in the shadow series. Raises ValueError."""
    runs = sorted(r.get("timestamp", "") for r in ledger
                  if r.get("kind", "run") == "run" and r.get("prereg_sha256") == plan_sha256)  # fmt: skip
    if not runs:
        raise ValueError("confirmation vers l'avant refusée : aucun verdict de ce plan au registre")
    verdict = datetime.strptime(runs[0][:8], "%Y%m%d").date()
    lo = forward_open_from(verdict, int(forward["min_months_after_verdict"]), int(forward["end_margin_sessions"]))
    if data_end < lo:
        raise ValueError(f"confirmation vers l'avant refusée : verdict du {verdict}, données jusqu'au {lo} au moins "
                         f"({forward['min_months_after_verdict']} mois de signaux et leurs sorties)")  # fmt: skip
    if shadow_signals < int(forward["min_shadow_signals"]):
        raise ValueError(f"confirmation vers l'avant refusée : {shadow_signals} signaux en ombre < "
                         f"{forward['min_shadow_signals']} (attendre ; au-delà de {forward['max_months_after_verdict']} "
                         f"mois : non concluant, sans ouverture)")  # fmt: skip


__all__ = ["INSIDER_TRIGGERS", "CellRule", "InsiderData", "InsiderRow", "InsiderRules", "coverage",
           "insider_report", "needs_insiders", "place", "rules_from", "screen", "t17_insider"]  # fmt: skip
