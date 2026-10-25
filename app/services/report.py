# ruff: noqa: E501 — long French report lines
"""Report of the day in the project's 10 rubrics (docs/09 §9.2, framework §1.5), built from stored
data only. Each rubric carries its nature (faits / sources externes / analyse / incertitudes) so the
reader separates facts from opinion. Archived as built in ``daily_reports`` (never corrected)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import (
    DailyReport,
    LetterReview,
    MarketRegimeRow,
    NewsItem,
    PriceEod,
    WatchlistRow,
)
from app.domain.timeutil import PARIS
from app.services.instruments import name_of

RUBRICS = [
    "Synthèse macro du jour et catalyseurs",
    "État des marchés",
    "Analyse du portefeuille",
    "Opportunités d'achat prioritaires",
    "Positions à vendre ou à alléger",
    "Watchlist",
    "Valeurs à éviter",
    "Plan d'action du jour",
    "Ordres manuels potentiels",
    "Risques globaux du jour",
]


def _index_line(s: Session, isin: str, label: str, day: date) -> str | None:
    rows = list(
        s.scalars(
            select(PriceEod).where(PriceEod.isin == isin, PriceEod.day <= day).order_by(PriceEod.day.desc()).limit(6)
        )
    )
    if not rows:
        return None
    c = rows[0]
    d1 = c.close / rows[1].close - 1 if len(rows) > 1 else None
    d5 = c.close / rows[-1].close - 1 if len(rows) == 6 else None
    return (
        f"{label} {c.close:.2f} ({c.day:%d/%m})"
        + (f", {d1:+.2%} sur la séance" if d1 is not None else "")
        + (f", {d5:+.2%} sur 5 séances" if d5 is not None else "")
    )


def build(s: Session, config: LoadedConfig, day: date, now: datetime | None = None) -> dict[str, Any]:
    from app.services.engine import todays_ranked
    from app.services.macro import LABELS, market_context, upcoming_events
    from app.services.portfolio_review import review_lines, synthesis

    now = now or datetime.now(UTC)
    ctxm = market_context(s, config, day)
    lines = review_lines(s, config, day, now)
    syn = synthesis(s, config, day, now, lines)
    props = todays_ranked(s, day)
    r: list[dict[str, Any]] = []

    # 1 — macro
    items = [f"{LABELS.get(k, k)} : {v.value:.2f} ({v.day:%d/%m}, {v.source})" for k, v in ctxm.values.items()]
    lr = s.scalar(select(LetterReview).where(LetterReview.status == "ok").order_by(LetterReview.id.desc()))
    ext = [f"Revue des lettres du {lr.day:%d/%m} : {lr.synthesis}"] if lr and lr.synthesis else []
    r.append({"titre": RUBRICS[0], "faits": items, "sources_externes": ext, "analyse": ctxm.explanations})
    # 2 — markets
    idx = (config.params.regime or {}).get("index_isins") or {}
    mk = [x for x in (_index_line(s, isin, k.upper(), day) for k, isin in idx.items()) if x]
    reg = s.scalar(select(MarketRegimeRow).order_by(MarketRegimeRow.id.desc()))
    r.append(
        {
            "titre": RUBRICS[1],
            "faits": mk + ([f"Régime {reg.regime} au {reg.day:%d/%m}"] if reg else []),
            "analyse": [f"Risque conjoncturel {ctxm.level} · portail {ctxm.gate} ({ctxm.gate_reason})"]
            + _rotation_lines(s, day),
        }
    )
    # 3 — portfolio
    pf = [
        f"{ln['nom']} ({ln['compte']}) : {ln.get('quantite')} titres, PRU {ln.get('pru')}, cours {ln.get('cours', '—')}, "
        f"stop {ln.get('stop') or '—'}, {ln.get('r', '—')} R → {ln['verdict']}"
        for ln in lines
    ]
    from app.services.weak_signals import ideas as radar_ideas
    from app.services.weak_signals import latest as radar_latest

    for ln in lines:  # docs/26: a weak signal on a held value is a caution (never a sale by itself)
        h = radar_latest(s, ln["isin"], day)
        if h is not None:
            advice = "rappel du stop" if h.label == "emballement" else "ne pas renforcer sans nouvelle"
            pf.append(f"Radar — {ln['nom']} : {h.text} ({advice})")
    rk = syn["risque"]
    r.append(
        {
            "titre": RUBRICS[2],
            "faits": pf or ["aucune position ouverte"],
            "analyse": [
                f"Risque ouvert {rk['convention_b_eur']} € / plafond {rk['plafond_eur']} € ; gains exposés {rk['convention_a_eur']} €"
            ]
            + rk["alertes"],
        }
    )
    # 4 — buys
    buys = []
    for p in props:
        if p.action != "BUY" or p.rank is None:
            continue
        d = p.details or {}
        acc = (d.get("comptes") or {}).get(p.account_recommended or "", {})
        blocked = [b for b in (p.gates or {}).get("blocked", []) if b.startswith("BLOQUÉ")]
        buys.append(
            f"#{p.rank} {name_of(p.isin)} — {p.account_recommended}, limite {d.get('p_max'):.4g}, stop {p.stop:.4g}, "
            f"objectif {(p.targets or ['—'])[0]}, gain net {(acc.get('gain_net_pct') or 0):+.1%}, ratio net {acc.get('ratio_net')}"
            + (f" — {blocked[0]}" if blocked else "")
        )
    r.append(
        {
            "titre": RUBRICS[3],
            "faits": buys[:8] or ["aucun achat admis aujourd'hui : l'absence d'opportunité est un résultat normal"],
        }
    )
    # 5 — sells / reduce
    sells = [
        f"{ln['nom']} : {ln['verdict']} — {'; '.join(ln.get('signaux') or [])}"
        for ln in lines
        if ln["verdict"] in ("VENDRE", "ALLÉGER")
    ]
    r.append({"titre": RUBRICS[4], "faits": sells or ["aucune sortie proposée"]})
    # 6 — watchlist
    watch = [
        f"#{p.rank or '—'} {name_of(p.isin)} : {((p.gates or {}).get('blocked') or ['à surveiller'])[0]}"
        for p in props
        if p.action == "WATCH"
    ]
    for h in radar_ideas(s, config, day - timedelta(days=1), {p.isin for p in props}):
        watch.append(f"Radar — idée à examiner : {name_of(h.isin)} — {h.text} (jamais un achat en l'état, docs/26)")
    n_wl = len(list(s.scalars(select(WatchlistRow.id).where(WatchlistRow.active.is_(True)))))
    r.append({"titre": RUBRICS[5], "faits": watch[:10] + [f"{n_wl} valeur(s) en watchlist active"]})
    # 7 — to avoid
    since = now - timedelta(days=2)
    neg = []
    for n in s.scalars(select(NewsItem).where(NewsItem.fetched_at >= since)):
        c = n.classification or {}
        if c.get("direction") == "negatif" and c.get("magnitude") in ("forte", "moyenne"):
            neg.append(f"{', '.join(name_of(i) for i in (n.isins or [])) or 'marché'} : {n.title}")
    from app.db.models import WeakSignalHit

    for h in s.scalars(select(WeakSignalHit).where(WeakSignalHit.day == day - timedelta(days=1))):
        if h.label == "emballement":  # docs/26 §26.4: attention and volume without any published information
            neg.append(f"Radar — {name_of(h.isin)} : {h.text}")
    avoid = [
        f"{name_of(p.isin)} : {((p.gates or {}).get('blocked') or ['écartée'])[0]}"
        for p in props
        if p.action == "NO_TRADE"
    ]
    r.append({"titre": RUBRICS[6], "faits": (neg[:6] + avoid[:6]) or ["rien à signaler"]})
    # 8 — action plan
    plan = [
        f"Poser l'ordre limite {name_of(p.isin)} à {(p.details or {}).get('p_max'):.4g}"
        for p in props
        if p.action == "BUY"
        and p.rank
        and not [b for b in (p.gates or {}).get("blocked", []) if b.startswith("BLOQUÉ")]
    ][:4]
    plan += [f"Stop {ln['nom']} → {ln['stop_propose']} ({ln['stop_raison']})" for ln in lines if ln.get("stop_change")]
    plan += [f"{ln['verdict']} {ln['nom']}" for ln in lines if ln["verdict"] in ("VENDRE", "ALLÉGER")]
    r.append({"titre": RUBRICS[7], "faits": plan or ["aucune action : rester discipliné"]})
    # 9 — manual orders
    orders = []
    for p in props:
        if (
            p.action == "BUY"
            and p.rank
            and not [b for b in (p.gates or {}).get("blocked", []) if b.startswith("BLOQUÉ")]
        ):
            acc = ((p.details or {}).get("comptes") or {}).get(p.account_recommended or "", {})
            orders.append({"sens": "achat", "valeur": name_of(p.isin), "isin": p.isin, "compte": p.account_recommended,
                           "type": "limite (jour)", "quantite": acc.get("titres"), "limite": (p.details or {}).get("p_max"),
                           "stop": p.stop})  # fmt: skip
    for ln in lines:
        if ln["verdict"] in ("VENDRE", "ALLÉGER"):
            q = ln["quantite"] if ln["verdict"] == "VENDRE" else int(ln["quantite"] * (ln.get("alleger_pct") or 0.5))
            orders.append({"sens": "vente", "valeur": ln["nom"], "isin": ln["isin"], "compte": ln["compte"],
                           "type": "au marché ou limite", "quantite": q, "limite": None, "stop": None})  # fmt: skip
        elif ln.get("stop_change"):
            orders.append({"sens": "modifier le stop", "valeur": ln["nom"], "isin": ln["isin"], "compte": ln["compte"],
                           "type": "stop", "quantite": ln["quantite"], "limite": None, "stop": ln["stop_propose"]})  # fmt: skip
    r.append({"titre": RUBRICS[8], "ordres": orders, "faits": [] if orders else ["aucun ordre à saisir"]})
    # 10 — global risks
    ev = [f"{e.currency} {e.title} — {e.ts:%d/%m %H:%M} UTC" for e in upcoming_events(s, now, days=3)]
    risks = [f"{k} : {v}" for k, v in ctxm.components.items()]
    if ctxm.srd_mode == "restreint":
        risks.append("Brent au-dessus du seuil : SRD en mode restreint (R12)")
    devises = {k: v for k, v in syn["exposition"]["devise"].items() if k != "EUR"}
    if devises:
        risks.append("Exposition hors euro : " + ", ".join(f"{k} {v:.0f} €" for k, v in devises.items()))
    r.append({"titre": RUBRICS[9], "faits": ev or ["aucune annonce majeure à 3 jours"], "analyse": risks})
    return {
        "jour": day.isoformat(),
        "construit_le": now.isoformat(timespec="seconds"),
        "rubriques": r,
        "synthese_portefeuille": syn,
    }


def _rotation_lines(s: Session, day: date) -> list[str]:
    from app.services.rotation import etf_ranking, sector_strength

    sec = list(sector_strength(s, day).items())[:3]
    etf = etf_ranking(s, day, 3)
    out = []
    if sec:
        out.append("Secteurs les plus forts : " + ", ".join(f"{k} (rang médian {v['median_rang']})" for k, v in sec))
    if etf:
        out.append("ETF en tête : " + ", ".join(f"{e['nom']} (rang {e['rang']:.0f})" for e in etf))
    return out


def store(s: Session, day: date, content: dict[str, Any]) -> DailyReport:
    row = s.scalar(select(DailyReport).where(DailyReport.day == day))
    if row is None:
        row = DailyReport(day=day, ts=datetime.now(UTC), content=content)
        s.add(row)
    s.flush()
    return row


def as_text(content: dict[str, Any]) -> str:
    out = [f"Rapport du {content['jour']}"]
    for i, rb in enumerate(content["rubriques"], 1):
        out.append(f"\n{i}. {rb['titre']}")
        out += [f"  - {x}" for x in rb.get("faits") or []]
        out += [f"  · {x}" for x in rb.get("sources_externes") or []]
        out += [f"  » {x}" for x in rb.get("analyse") or []]
        for o in rb.get("ordres") or []:
            out.append(
                f"  - {o['sens']} {o['valeur']} ({o['compte']}) {o['type']} qté {o['quantite']} limite {o['limite']} stop {o['stop']}"
            )
    out.append("\nAucun ordre n'est passé par l'outil. Faits / sources externes (·) / analyse (») séparés.")
    return "\n".join(out)


def telegram_lines(content: dict[str, Any]) -> list[str]:
    rb = content["rubriques"]
    out = [x for x in rb[1].get("faits", [])[:2]]
    out += [
        f"Achats : {len([x for x in rb[3]['faits'] if x.startswith('#')])}",
        *[x[:120] for x in rb[3]["faits"][:3] if x.startswith("#")],
    ]
    sells = [x for x in rb[4]["faits"] if "aucune" not in x]
    if sells:
        out.append(f"Sorties : {len(sells)}")
    out += [x[:120] for x in rb[9].get("faits", [])[:2]]
    out.append("Rapport complet : page Rapport")
    return out[:11]


def today_paris() -> date:
    return datetime.now(PARIS).date()
