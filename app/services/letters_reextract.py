"""Stored letters read again with the current extraction prompt (user request of 30/10/2026, docs/20 §20.11).

The letters study measured 99 recommendations only: the history (317 letters, mostly Momentum Capital 2025-2026,
imported with ``newsletters-history``) was read with the extraction prompt v1, which gave no levels, and hundreds of
value names were never resolved to an ISIN. The reception now reads with ``newsletter_extract`` v2 (closed list of
level names, number guard, names resolved on the referential); this service applies the same reading to the stored
letters, so that ``letters-study`` can test the arms E2, E3 and X1.

- Dry run (default): counts, characters and the **estimated** cost from ``llm.prices_usd_per_mtok`` — no call.
- Apply: the gateway call of the reception (replay cache, daily spend cap), letter by letter, oldest first; each
  letter is marked ``parsed["reextract"]`` and skipped on the next run (resumable).

Stored values are updated conservatively, never deleted: a row is matched by ISIN or normalized name; its empty ISIN
is filled, its levels replaced by the new canonical levels (only when the new reading has levels) and its direction
set (a « neutral » reading never overrides a direction, as at the reception). A row linked to a signal (hence to a
proposal and its decision snapshot, rule 10) only gets an empty ISIN or empty levels filled. A value not stored yet
is inserted as the reception stores it. No signal, proposal or watchlist line is created: the letters are past.
The letter's text is never printed."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Instrument, LlmCall, NewsletterItem, NewsletterValue
from app.domain.letter_levels import canonical_levels
from app.domain.names import core_name, norm, resolve
from app.domain.timeutil import PARIS, from_store
from app.llm.gateway import LlmSettings, LlmUnavailable, load_prompt

log = logging.getLogger("bourse.letters_reextract")

PROMPT = "newsletter_extract"
BODY_CHARS = 12000  # characters of the letter given to the reader, as at the reception (newsletters.ingest_mail)
MAX_TOKENS = 2048  # output cap of the reception's call
# Order of magnitude used for the cost shown *before* spending (display only, never a business figure): about
# 3.5 characters per token for a French text. The cost actually spent is the gateway's, from the API's token counts.
CHARS_PER_TOKEN = 3.5
# a failure that belongs to the letter (the same answer would come back): marked and skipped, the run goes on;
# any other one (daily or monthly cap, missing key, unknown price, API error) stops the run, to resume later
_LETTER_FAILURES = ("tronquée", "non conforme", "refusée", "JSON invalide")


# --- selection ----------------------------------------------------------------------------------------------------
def period_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """Paris days ``start`` to ``end`` included, as UTC instants (letters are dated by their reception)."""
    lo = datetime.combine(start, datetime.min.time(), tzinfo=PARIS)
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=PARIS)
    return lo.astimezone(UTC), hi.astimezone(UTC)


def select_items(s: Session, start: date, end: date, sources: list[str] | None = None) -> list[NewsletterItem]:
    lo, hi = period_bounds(start, end)
    q = select(NewsletterItem).where(NewsletterItem.received_at >= lo, NewsletterItem.received_at < hi)
    if sources:
        q = q.where(NewsletterItem.source.in_(sources))
    return list(s.scalars(q.order_by(NewsletterItem.received_at, NewsletterItem.id)))


def state(item: NewsletterItem, version: str) -> str:
    """« todo », « done » (re-read with this prompt version), « failed » (re-read failed for this letter) or
    « current » (read with this prompt version at the reception already)."""
    mark = (item.parsed or {}).get("reextract") or {}
    if mark.get("version") == version:
        return "failed" if mark.get("status") == "echec" else "done"
    if item.parsed_by == "llm" and item.llm_prompt_version == version:
        return "current"
    return "todo"


# --- estimate (dry run) -------------------------------------------------------------------------------------------
def _n(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


@dataclass
class Plan:
    letters: int
    states: dict[str, int]
    todo_ids: list[int]
    chars_read: int  # characters given to the reader (≤ BODY_CHARS per letter)
    chars_total: int
    version: str
    model: str
    price: dict[str, float] | None
    tokens_in: int
    tokens_out_per_letter: float
    out_basis: str
    cost: float | None
    cost_max: float | None
    daily_cap: float
    spent_today: float

    def lines(self, max_cost: float) -> list[str]:
        n = len(self.todo_ids)
        st = self.states
        out = [
            f"{self.letters} lettre(s) dans la période · à relire : {n} · déjà relues avec la consigne "
            f"v{self.version} : {st.get('done', 0)} · en échec lors d'une relecture : {st.get('failed', 0)} · "
            f"lues avec la consigne v{self.version} dès la réception : {st.get('current', 0)}",
            f"caractères lus : {_n(self.chars_read)} (≤ {_n(BODY_CHARS)} par lettre ; {_n(self.chars_total)} au total)",
            f"jetons estimés : ≈ {_n(self.tokens_in)} en entrée (≈ {CHARS_PER_TOKEN:g} caractères par jeton, consigne "
            f"comprise) ; en sortie ≈ {_n(self.tokens_out_per_letter)} par lettre ({self.out_basis})",
        ]
        if self.price is None or self.cost is None or self.cost_max is None:
            out.append(f"coût non estimable : prix inconnu pour {self.model} (llm.prices_usd_per_mtok)")
        else:
            out.append(
                f"coût estimé : ≈ {self.cost:.3f} $ (au plus ≈ {self.cost_max:.3f} $ si chaque réponse atteint "
                f"{MAX_TOKENS} jetons) · modèle {self.model} à {self.price['input']:g} $ / "
                f"{self.price['output']:g} $ par million de jetons (params.yaml › llm.prices_usd_per_mtok)"
            )
            left = max(self.daily_cap - self.spent_today, 0.0)
            out.append(
                f"plafond journalier LLM : {self.daily_cap:.2f} $ (dépensé aujourd'hui : {self.spent_today:.2f} $) · "
                f"plafond de la commande : --max-cost-usd {max_cost:.2f} $"
            )
            if n and self.cost_max > min(left, max_cost):
                per = self.cost_max / n
                fit = int(min(left, max_cost) / per) if per > 0 else n
                out.append(
                    f"le plafond sera atteint avant la fin : ≈ {fit} lettre(s) par exécution ; relancer ensuite, "
                    "la reprise est automatique"
                )
        out.append("aperçu seulement, aucun appel à l'IA : relancer avec --apply")
        return out


def _out_tokens(s: Session, model: str) -> tuple[float, str]:
    """Mean output tokens of the paid extractions already logged (same model first), else the output cap."""
    for m in (model, None):
        q = select(func.count(), func.avg(LlmCall.output_tokens)).where(
            LlmCall.purpose == PROMPT, LlmCall.cached.is_(False), LlmCall.output_tokens > 0
        )
        if m is not None:
            q = q.where(LlmCall.model == m)
        n, avg = s.execute(q).one()
        if n and avg:
            who = f"{m}" if m else "tous modèles"
            return float(avg), f"moyenne de {n} extraction(s) payée(s) enregistrée(s), {who}"
    return float(MAX_TOKENS), f"aucune extraction payée enregistrée : plafond de {MAX_TOKENS} jetons"


def letter_tokens_in(body: str, system: str) -> int:
    return math.ceil((len(system) + min(len(body), BODY_CHARS)) / CHARS_PER_TOKEN)


def letter_cost_max(body: str, system: str, price: dict[str, float]) -> float:
    """Upper estimate of one letter's call: estimated input, output at the cap."""
    return (letter_tokens_in(body, system) * price["input"] + MAX_TOKENS * price["output"]) / 1e6


def _spent_today(s: Session, now: datetime) -> float:
    """Spent since midnight Paris, as ``ClaudeGateway.budget_ok`` counts it."""
    day0 = now.astimezone(PARIS).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    return float(
        s.scalar(
            select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0)).where(LlmCall.ts >= day0, LlmCall.cached.is_(False))
        )
        or 0.0
    )


def plan(
    s: Session,
    items: list[NewsletterItem],
    settings: LlmSettings,
    now: datetime | None = None,
    retry_failed: bool = False,
) -> Plan:
    version, system = load_prompt(settings.prompts_dir, PROMPT)
    model = settings.classify_model  # the model ``gateway.parse`` uses when none is given (the reception's)
    states: dict[str, int] = {}
    todo: list[NewsletterItem] = []
    for it in items:
        st = state(it, version)
        states[st] = states.get(st, 0) + 1
        if st == "todo" or (st == "failed" and retry_failed):
            todo.append(it)
    chars = sum(min(len(it.body_text or ""), BODY_CHARS) for it in todo)
    tin = sum(letter_tokens_in(it.body_text or "", system) for it in todo)
    tout, basis = _out_tokens(s, model)
    price = settings.prices.get(model)
    cost = cost_max = None
    if price:
        cost = (tin * price["input"] + tout * len(todo) * price["output"]) / 1e6
        cost_max = (tin * price["input"] + MAX_TOKENS * len(todo) * price["output"]) / 1e6
    return Plan(
        letters=len(items),
        states=states,
        todo_ids=[it.id for it in todo],
        chars_read=chars,
        chars_total=sum(len(it.body_text or "") for it in todo),
        version=version,
        model=model,
        price=dict(price) if price else None,
        tokens_in=tin,
        tokens_out_per_letter=tout,
        out_basis=basis,
        cost=cost,
        cost_max=cost_max,
        daily_cap=settings.daily_budget_usd,
        spent_today=_spent_today(s, now or datetime.now(UTC)),
    )


# --- one letter ---------------------------------------------------------------------------------------------------
@dataclass
class _Read:
    """A value of the new reading, after the number guard, the closed list of level names and name resolution."""

    name: str
    isin: str | None
    direction: str
    levels: dict[str, float]


@dataclass
class LetterResult:
    values: int = 0
    with_levels: int = 0
    isin_filled: int = 0
    resolved_now: list[str] = field(default_factory=list)  # « name → ISIN » newly resolved
    updated: int = 0
    inserted: int = 0
    kept_linked: int = 0  # rows linked to a proposal whose levels or direction differ: left as they were
    rejected: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)


def _read_values(s: Session, ext: Any, body: str, names: dict[str, str], res: LetterResult) -> list[_Read]:
    """The reception's treatment of the reader's answer (newsletters.ingest_mail): an ISIN given by the reader is kept
    only if it is in the referential, otherwise the name is resolved; a level is kept only if its number is written in
    the letter (rejected ones listed), then put under the closed list of names; values merged by ISIN or name."""
    from app.services.newsletters import _number_in_text

    merged: dict[str, _Read] = {}
    for lv in ext.values:
        isin = (lv.isin if lv.isin and s.get(Instrument, lv.isin) is not None else None) or resolve(lv.name, names)
        verified = {k: v for k, v in lv.levels.items() if _number_in_text(v, body)}
        res.rejected += [f"{lv.name}:{k}={v}" for k, v in lv.levels.items() if k not in verified]
        levels, dropped = canonical_levels(verified)
        res.unknown += [f"{lv.name}:{k}" for k in dropped]
        key = isin or norm(lv.name)
        base = merged.get(key)
        if base is None:
            merged[key] = _Read(lv.name, isin, lv.direction, levels)
        else:
            base.direction = lv.direction if lv.direction != "neutral" else base.direction
            for k, v in levels.items():
                base.levels.setdefault(k, v)
    return list(merged.values())


def _match(rows: list[NewsletterValue], v: _Read, used: set[int]) -> NewsletterValue | None:
    """The stored row of this value: same ISIN first, then same normalized name (full or core) on a row whose ISIN is
    empty or the same — never a row of another value."""
    free = [r for r in rows if r.id not in used]
    if v.isin:
        for r in free:
            if r.isin == v.isin:
                return r
    keys = {k for k in (norm(v.name), core_name(v.name)) if k}
    for r in free:
        same_name = bool(keys & {k for k in (norm(r.name or ""), core_name(r.name or "")) if k})
        if r.isin and v.isin and r.isin != v.isin:
            if same_name and r.signal_id is not None:
                return r  # a linked row read again under another ISIN: kept as it is, never doubled (review 30/10)
            continue
        if same_name:
            return r
    return None


def _market_fields(s: Session, row: NewsletterValue, received_at: datetime, calendars: Any) -> None:
    """Official open of the letter's session, previous close and gap, as the reception stores them for a letter
    imported after the fact (no quote at receipt). Only empty fields are filled."""
    from app.domain.indicators import gap_open as gap_fn
    from app.services.newsletters import _eod, _prev_close, session_day_for

    if not row.isin or row.p_open is not None or row.close_prev is not None:
        return
    day = session_day_for(from_store(received_at), calendars)  # SQLite drops the offset: stored instants are UTC
    eod = _eod(s, row.isin, day)
    row.p_open = eod.open if eod else None
    row.close_prev = _prev_close(s, row.isin, day)
    if row.p_open and row.close_prev:
        row.gap_open = gap_fn(row.p_open, row.close_prev)


def apply_reading(
    s: Session,
    item: NewsletterItem,
    ext: Any,
    names: dict[str, str],
    version: str,
    now: datetime,
    calendars: Any = None,
    model: str | None = None,
    cost: float = 0.0,
    cached: bool = False,
) -> LetterResult:
    """Store one letter's new reading (``ext``: a ``NewsletterExtraction``) on its values, conservatively."""
    res = LetterResult()
    body = item.body_text or ""
    values = _read_values(s, ext, body, names, res)
    rows = list(
        s.scalars(select(NewsletterValue).where(NewsletterValue.item_id == item.id).order_by(NewsletterValue.id))
    )
    used: set[int] = set()
    for v in values:
        res.values += 1
        res.with_levels += bool(v.levels)
        row = _match(rows, v, used)
        if row is None:
            row = NewsletterValue(
                item_id=item.id, isin=v.isin, name=v.name[:128], direction=v.direction, levels=v.levels or None
            )
            s.add(row)
            _market_fields(s, row, item.received_at, calendars)
            res.inserted += 1
            if v.isin:
                res.resolved_now.append(f"{v.name} → {v.isin}")
            continue
        used.add(row.id)
        linked = row.signal_id is not None  # its proposal and decision snapshot stay as they were (rule 10)
        changed = kept = False
        if linked and row.isin and v.isin and row.isin != v.isin:
            res.kept_linked += 1
            continue
        if not row.isin and v.isin:
            row.isin = v.isin
            res.isin_filled += 1
            res.resolved_now.append(f"{v.name} → {v.isin}")
            changed = True
            if not linked:
                _market_fields(s, row, item.received_at, calendars)
        if v.levels and v.levels != (row.levels or {}):
            if not row.levels:
                row.levels = dict(v.levels)
                changed = True
            elif not linked:
                row.levels = dict(v.levels)
                changed = True
            else:
                kept = True
        if v.direction != "neutral" and v.direction != row.direction:
            if linked:
                kept = True
            else:
                row.direction = v.direction
                changed = True
        res.updated += changed
        res.kept_linked += kept
    parsed = dict(item.parsed or {})  # reassigned: the JSON column is not mutation-tracked
    before = list(parsed.get("rejected_llm_levels") or [])
    parsed["rejected_llm_levels"] = before + [r for r in res.rejected if r not in before]
    parsed["reextract"] = {
        "version": version,
        "model": model,
        "at": now.isoformat(timespec="seconds"),
        "status": "ok",
        "values": res.values,
        "with_levels": res.with_levels,
        "isin_filled": res.isin_filled,
        "updated": res.updated,
        "inserted": res.inserted,
        "kept_linked": res.kept_linked,
        "rejected_llm_levels": res.rejected,
        "unknown_level_names": res.unknown,
        "cost_usd": round(cost, 6),
        "cached": cached,
    }
    item.parsed = parsed
    s.flush()
    return res


def mark_failed(item: NewsletterItem, version: str, now: datetime, error: str) -> None:
    parsed = dict(item.parsed or {})
    parsed["reextract"] = {"version": version, "at": now.isoformat(timespec="seconds"), "status": "echec",
                           "error": error[:200]}  # fmt: skip
    item.parsed = parsed


# --- run ----------------------------------------------------------------------------------------------------------
@dataclass
class RunReport:
    version: str
    model: str
    max_cost: float
    processed: int = 0
    cached: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    values: int = 0
    with_levels: int = 0
    isin_filled: int = 0
    resolved: list[str] = field(default_factory=list)
    updated: int = 0
    inserted: int = 0
    kept_linked: int = 0
    rejected: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    cost: float = 0.0
    stopped: str | None = None
    remaining: int = 0
    resume_at: datetime | None = None

    def add(self, r: LetterResult) -> None:
        self.values += r.values
        self.with_levels += r.with_levels
        self.isin_filled += r.isin_filled
        self.resolved += r.resolved_now
        self.updated += r.updated
        self.inserted += r.inserted
        self.kept_linked += r.kept_linked
        self.rejected += r.rejected
        self.unknown += r.unknown

    def lines(self) -> list[str]:
        out = [
            f"lettres relues : {self.processed} (dont {self.cached} servie(s) par le cache, sans coût) · déjà relues, "
            f"sautées : {self.skipped} · en échec : {len(self.failed)}",
            f"valeurs lues : {self.values} · avec niveaux (écrits dans la lettre) : {self.with_levels} · "
            f"ISIN nouvellement résolus : {len(self.resolved)}",
            f"lignes mises à jour : {self.updated} · insérées : {self.inserted} · lignes liées à une proposition dont "
            f"les niveaux ou le sens lus diffèrent, laissées telles quelles : {self.kept_linked}",
            f"nombres absents du texte, rejetés : {len(self.rejected)}"
            + (f" (ex. {', '.join(self.rejected[:8])})" if self.rejected else ""),
        ]
        if self.unknown:
            out.append(f"noms de niveau hors liste fermée, ignorés : {len(self.unknown)}")
        if self.resolved:
            out.append(f"ISIN résolus (exemples) : {', '.join(self.resolved[:15])}")
        out += [f"  échec : {f}" for f in self.failed[:10]]
        out.append(f"coût dépensé : {self.cost:.4f} $ (plafond de la commande : {self.max_cost:.2f} $)")
        if self.stopped:
            when = (
                f" ; prochaine lettre reçue le {from_store(self.resume_at).astimezone(PARIS):%d/%m/%Y}"
                if self.resume_at
                else ""
            )
            out.append(f"ARRÊT : {self.stopped}")
            out.append(
                f"{self.remaining} lettre(s) restante(s){when} : relancer la même commande (demain si le plafond "
                "journalier est atteint) ; les lettres déjà relues sont sautées"
            )
        return out


def _left(s: Session, ids: list[int], version: str, retry_failed: bool) -> int:
    """Letters of ``ids`` still to read."""
    wanted = ("todo", "failed") if retry_failed else ("todo",)
    items = s.scalars(select(NewsletterItem).where(NewsletterItem.id.in_(ids or [-1])))
    return sum(1 for it in items if state(it, version) in wanted)


def letter_failure(exc: LlmUnavailable) -> bool:
    return any(m in str(exc) for m in _LETTER_FAILURES)


def run(
    session: Callable[[], AbstractContextManager[Session]],
    item_ids: list[int],
    gateway: Any,
    max_cost: float,
    calendars: Any = None,
    progress: Callable[[str], None] | None = None,
    retry_failed: bool = False,
) -> RunReport:
    """Re-read ``item_ids`` in order, one transaction per letter (an interruption keeps what is done). Stops before a
    letter whose upper cost estimate would take the run past ``max_cost``, and at the first failure that is not the
    letter's own (daily or monthly cap reached, missing key, API error), saying where to resume."""
    from app.llm.schemas import NewsletterExtraction
    from app.services.news import name_index

    settings: LlmSettings = gateway.settings
    version, system = load_prompt(settings.prompts_dir, PROMPT)
    model = settings.classify_model
    price = settings.prices.get(model)
    rep = RunReport(version, model, max_cost)
    names: dict[str, str] | None = None
    for i, iid in enumerate(item_ids):
        with session() as s:
            if names is None:
                names = name_index(s)
            it = s.get(NewsletterItem, iid)
            if it is None:
                continue
            st = state(it, version)
            if st in ("done", "current") or (st == "failed" and not retry_failed):
                rep.skipped += 1
                continue
            body = it.body_text or ""
            if price and rep.cost + letter_cost_max(body, system, price) > max_cost:
                rep.stopped = (
                    f"plafond de la commande atteint (--max-cost-usd {max_cost:.2f} $ ; dépensé {rep.cost:.4f} $)"
                )
                rep.remaining, rep.resume_at = _left(s, item_ids[i:], version, retry_failed), it.received_at
                break
            now = datetime.now(UTC)
            try:
                ext, usage = gateway.parse(
                    s, "newsletter_extract", PROMPT, body[:BODY_CHARS], NewsletterExtraction, max_tokens=MAX_TOKENS
                )
            except LlmUnavailable as exc:
                if letter_failure(exc):
                    # a truncated or refused answer is billed: its upper estimate counts in the command's cap and
                    # in the daily cap (the gateway does not log it — review of 30/10/2026)
                    billed = letter_cost_max(body, system, price) if price else 0.0
                    rep.cost += billed
                    s.add(LlmCall(ts=now, model=model, prompt_version=version, prompt_hash="reextract-echec",
                                  input_tokens=letter_tokens_in(body, system), output_tokens=MAX_TOKENS,
                                  cost_usd=billed, cached=False, purpose=PROMPT))  # fmt: skip
                    mark_failed(it, version, now, str(exc))
                    rep.failed.append(f"{it.source} {from_store(it.received_at).astimezone(PARIS):%d/%m/%Y} — {exc}")
                    continue
                rep.stopped = str(exc)
                rep.remaining, rep.resume_at = _left(s, item_ids[i:], version, retry_failed), it.received_at
                break
            rep.cost += usage.cost_usd
            rep.cached += bool(usage.cached)
            res = apply_reading(s, it, ext, names, version, now, calendars, model, usage.cost_usd, usage.cached)
            rep.processed += 1
            rep.add(res)
        if progress is not None and ((i + 1) % 10 == 0 or i == len(item_ids) - 1):
            progress(
                f"{i + 1}/{len(item_ids)} · relues {rep.processed} · valeurs {rep.values} · avec niveaux "
                f"{rep.with_levels} · ISIN résolus {len(rep.resolved)} · coût {rep.cost:.4f} $"
            )
    return rep
