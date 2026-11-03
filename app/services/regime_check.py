"""Diagnostic of 03/11/2026 (review of the opportunity cards by the user's own analysis): « le régime de marché est
classé inconnu pour 100 % des cas ». Where is the regime missing? Read-only, counts and dates only, no price.

1. ``market_regime``: the rows the daily job writes, per market (EU = the European reference the snapshots read).
2. The decision snapshots of the proposals (``decision_snapshots.regime``): those the comparable history groups by.
3. The final outcomes used by the comparable history (``outcomes.samples``): their regime key.
4. The latest backtest: its trades' regime (index above / under its 200-session average, per venue).
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import BacktestRun, BacktestTrade, DecisionSnapshot, MarketRegimeRow, Proposal, Signal


def regime_check(s: Session, today: date, days: int = 60) -> list[str]:
    out = ["1. Régime enregistré par le job quotidien (market_regime) :"]
    rows = s.execute(
        select(MarketRegimeRow.market, func.count(), func.min(MarketRegimeRow.day), func.max(MarketRegimeRow.day))
        .group_by(MarketRegimeRow.market)
        .order_by(MarketRegimeRow.market)
    ).all()
    if not rows:
        out.append("   aucune ligne : le job daily_regime n'a jamais écrit de régime")
    for market, n, lo, hi in rows:
        last = s.scalar(
            select(MarketRegimeRow.regime)
            .where(MarketRegimeRow.market == market)
            .order_by(MarketRegimeRow.day.desc(), MarketRegimeRow.id.desc())
        )
        out.append(f"   {market:<6} {n} jour(s) du {lo} au {hi} · dernier : {last}")
    since = today - timedelta(days=days)
    snaps = list(
        s.execute(
            select(DecisionSnapshot.regime, Signal.detector)
            .join(Proposal, Proposal.id == DecisionSnapshot.proposal_id)
            .join(Signal, Signal.id == Proposal.signal_id)
            .where(Proposal.created_at >= since)
        )
    )
    by: Counter[tuple[str, str]] = Counter()
    for reg, det in snaps:
        by[(det, (reg or {}).get("regime") or "inconnu")] += 1
    out.append(f"2. Instantanés des propositions des {days} derniers jours ({len(snaps)}) : régime enregistré")
    for (det, reg), n in sorted(by.items()):
        out.append(f"   {det:<22} {reg:<10} {n}")
    from app.services.outcomes import samples

    smp = samples(s)
    c = Counter((x.keys.get("detector") or "?", x.keys.get("regime") or "inconnu") for x in smp)
    out.append(f"3. Résultats finals de l'historique comparable ({len(smp)}) : régime de l'instantané")
    for (det, reg), n in sorted(c.items()):
        out.append(f"   {det:<22} {reg:<10} {n}")
    run = s.scalar(select(BacktestRun).order_by(BacktestRun.id.desc()))
    if run is None:
        out.append("4. Aucun backtest enregistré")
        return out
    trades = s.execute(
        select(BacktestTrade.detector, BacktestTrade.regime, func.count())
        .where(BacktestTrade.run_id == run.id)
        .group_by(BacktestTrade.detector, BacktestTrade.regime)
    ).all()
    out.append(
        f"4. Backtest n° {run.id} ({run.start} → {run.end}, créé le {run.created_at:%d/%m/%Y}) : régime des trades"
    )
    for det, reg, n in sorted(trades, key=lambda r: (r[0], r[1] or "")):
        out.append(f"   {det:<22} {reg or 'inconnu':<10} {n}")
    return out
