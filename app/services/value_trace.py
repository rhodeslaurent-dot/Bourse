"""18/11/2026 — « why are NVIDIA and Take-Two not proposed, nor in the watchlist? ». One value followed through each
gate of the tool, from stored facts only: is it known, in the universe (and why not), its last close and relative
strength, the watchlist and why not, its signals and proposals, and the news and events attached to it.

Read by ``gates-coverage --value <ISIN|ticker|name>`` (an allowlisted command of the PC relay). Read only."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Instrument


def find(s: Session, query: str) -> list[Instrument]:
    """By ISIN, then by ticker (``NVDA``, ``NVDA.US``, ``NASDAQ:NVDA``), then by name (at most 5)."""
    q = query.strip()
    if len(q) == 12 and q[:2].isalpha():
        inst = s.get(Instrument, q.upper())
        if inst is not None:
            return [inst]
    t = q.upper()
    rows = list(
        s.scalars(
            select(Instrument).where(
                or_(
                    func.upper(Instrument.ticker_local) == t,
                    func.upper(Instrument.ticker_eodhd) == t,
                    func.upper(Instrument.ticker_eodhd).like(f"{t}.%"),
                    func.upper(Instrument.ticker_tv) == t,
                    func.upper(Instrument.ticker_tv).like(f"%:{t}"),
                )
            )
        )
    )
    if rows:
        return rows[:5]
    return list(s.scalars(select(Instrument).where(func.upper(Instrument.name).like(f"%{t}%")).limit(5)))


def _eur(x: float | None) -> str:
    return f"{x / 1e9:.1f} Md€" if x and x >= 1e9 else (f"{x / 1e6:.0f} M€" if x else "inconnue")


def trace(s: Session, config: Any, query: str, today: date) -> list[str]:
    from app.db.models import MarketEvent, NewsItem, PriceEod, Proposal, Signal, WatchlistRow
    from app.domain.timeutil import from_store
    from app.services.market_data import latest_features

    found = find(s, query)
    if not found:
        return [
            f"« {query} » : aucune valeur connue de l'outil (ni ISIN, ni ticker, ni nom) — hors des places chargées"
        ]
    out: list[str] = []
    since = datetime.combine(today - timedelta(days=30), datetime.min.time(), tzinfo=UTC)
    for inst in found:
        out.append(f"{inst.name} — {inst.isin} · {inst.ticker_eodhd or '?'} · place {inst.mic} · {inst.kind}"
                   + ("" if inst.active else " · inactive"))  # fmt: skip
        reasons = "; ".join(inst.universe_reasons or []) or "—"
        out.append(f"  univers : {'OUI' if inst.in_universe else 'NON'}"
                   + ("" if inst.in_universe else f" — motifs : {reasons}")
                   + f" · capitalisation {_eur(inst.market_cap_eur)} · volume moyen 20 j {_eur(inst.adv_eur_20)}"
                   + f" · secteur {inst.sector or 'inconnu'}")  # fmt: skip
        last = s.execute(
            select(PriceEod.day, PriceEod.close)
            .where(PriceEod.isin == inst.isin)
            .order_by(PriceEod.day.desc())
            .limit(1)
        ).first()
        n = s.scalar(select(func.count()).select_from(PriceEod).where(PriceEod.isin == inst.isin)) or 0
        tail = f", dernière {last[0]:%d/%m/%Y} à {last[1]:g} {inst.currency}" if last else ""
        out.append(f"  cours : {n} séance(s) stockée(s){tail}")
        f = latest_features(s, [inst.isin]).get(inst.isin)
        if f is not None:
            above = []
            if f.mm50 and last:
                above.append("au-dessus de la MM50" if last[1] > f.mm50 else "sous la MM50")
            if f.mm200 and last:
                above.append("au-dessus de la MM200" if last[1] > f.mm200 else "sous la MM200")
            out.append(f"  force relative : rang {f.rs_rank if f.rs_rank is not None else '—'} / 100 le {f.day:%d/%m}"
                       + (f" · {', '.join(above)}" if above else ""))  # fmt: skip
        else:
            out.append("  force relative : non calculée (valeur hors univers ou historique insuffisant)")
        u = config.params.universe or {}
        rs_min, wl_max = u.get("momentum_watchlist_rs_rank_min", 80), u.get("momentum_watchlist_max", 100)
        wl = list(s.scalars(select(WatchlistRow).where(WatchlistRow.isin == inst.isin, WatchlistRow.active.is_(True))))
        if wl:
            out.append("  liste de surveillance : OUI — " + ", ".join(f"{w.reason} ({w.source})" for w in wl))
        else:
            floor = s.scalar(select(func.min(WatchlistRow.rs_rank)).where(WatchlistRow.reason == "momentum",
                                                                        WatchlistRow.active.is_(True)))  # fmt: skip
            out.append(f"  liste de surveillance : NON — elle prend d'office les {wl_max} plus fortes de l'univers "
                       f"(rang ≥ {rs_min}, au-dessus des moyennes ; la dernière admise a le rang {floor or '—'}), "
                       "les valeurs des lettres et celles ajoutées à la main")  # fmt: skip
        q_sig = select(Signal).where(Signal.isin == inst.isin, Signal.ts >= since, Signal.deleted_at.is_(None))
        sigs = list(s.scalars(q_sig.order_by(Signal.ts.desc())))
        out.append(f"  signaux (30 j) : {len(sigs)}" + (" — " + ", ".join(
            f"{x.detector} le {from_store(x.ts):%d/%m}" for x in sigs[:4]) if sigs else
            " — aucun détecteur (cassure, repli, écart + catalyseur…) ne s'est déclenché"))  # fmt: skip
        props = list(s.scalars(select(Proposal).where(Proposal.isin == inst.isin, Proposal.created_at >= since)
                               .order_by(Proposal.created_at.desc())))  # fmt: skip
        for p in props[:3]:
            blocked = [b for b in ((p.gates or {}).get("blocked") or [])][:2]
            out.append(f"  proposition du {from_store(p.created_at):%d/%m} : {p.action}"
                       + (f" — {'; '.join(blocked)}" if blocked else ""))  # fmt: skip
        if not props:
            out.append(
                "  propositions (30 j) : aucune — une proposition naît d'un signal, jamais d'une actualité seule"
            )
        news_since = datetime.now(UTC) - timedelta(days=14)
        news = [n for n in s.scalars(select(NewsItem).where(NewsItem.fetched_at >= news_since))
                if inst.isin in (n.isins or [])]  # fmt: skip
        out.append(f"  actualités rattachées (14 j) : {len(news)}"
                   + (" — dernière : " + news[-1].title[:90] if news else ""))  # fmt: skip
        evs: dict[str, int] = {
            str(k): int(v)
            for k, v in s.execute(
                select(MarketEvent.family, func.count())
                .where(MarketEvent.isin == inst.isin, MarketEvent.event_at >= news_since)
                .group_by(MarketEvent.family)
            ).all()
        }
        out.append("  événements (14 j) : " + (", ".join(f"{k} {v}" for k, v in sorted(evs.items())) or "aucun"))
    return out
