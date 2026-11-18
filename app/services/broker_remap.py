"""18/11/2026 — a Saxo line had been tied to the ISIN of another company (Saxo gives no ISIN, ADR-006: the tie is
the referential's). This moves a Saxo symbol to the right ISIN — its persisted aliases, ``ticker_saxo``, and the rows
of the **Saxo accounts** carrying the wrong ISIN (orders, executions, positions, AI reviews), their references kept
(rule 9: never merged, never lost). A dry run unless ``apply``. ``suggest`` finds the right ISIN from the name Saxo
gives the line, so that no holding is ever written in the repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Account, AiPositionReview, Instrument, InstrumentAlias, Order, PositionRow, Trade


def remap(s: Session, symbol: str, new_isin: str, apply: bool = False) -> list[str]:
    from app.data.providers.saxo import parse_saxo_symbol
    from app.services.instruments import _load as instruments_map

    if parse_saxo_symbol(symbol) is None:
        return [f"symbole « {symbol} » invalide (forme attendue : TICKER:place, ex. VK:xpar)"]
    new = s.get(Instrument, new_isin.upper())
    if new is None:
        return [f"ISIN {new_isin} absent du référentiel : rien n'est changé (le vérifier, ou charger la place)"]
    old = {i.isin for i in s.scalars(select(Instrument).where(Instrument.ticker_saxo == symbol))}
    old |= {isin for isin, e in instruments_map().items() if e.get("saxo") == symbol}
    old.discard(new.isin)
    if not old:
        return [f"{symbol} n'est rattaché à aucun autre ISIN : rien à corriger (il sera reconnu comme {new.name})"]
    accounts = [a.id for a in s.scalars(select(Account).where(Account.broker == "saxo"))]
    names = {i.isin: i.name for i in s.scalars(select(Instrument).where(Instrument.isin.in_(old)))}
    out = [f"{symbol} : rattaché à tort à {', '.join(f'{names.get(i, i)} ({i})' for i in sorted(old))} → "
           f"{new.name} ({new.isin})"]  # fmt: skip
    aliases = list(s.scalars(select(InstrumentAlias).where(InstrumentAlias.provider == "saxo_uic",
                                                         InstrumentAlias.isin.in_(old))))  # fmt: skip
    rows = {
        "ordres": list(s.scalars(select(Order).where(Order.account_id.in_(accounts), Order.isin.in_(old)))),
        "exécutions": list(s.scalars(select(Trade).where(Trade.account_id.in_(accounts), Trade.isin.in_(old)))),
        "positions": list(
            s.scalars(select(PositionRow).where(PositionRow.account_id.in_(accounts), PositionRow.isin.in_(old)))
        ),  # fmt: skip
    }
    pos_ids = [p.id for p in rows["positions"]]
    reviews = (
        list(s.scalars(select(AiPositionReview).where(AiPositionReview.position_id.in_(pos_ids)))) if pos_ids else []
    )
    out.append(f"  alias Saxo : {len(aliases)} · " + " · ".join(f"{k} : {len(v)}" for k, v in rows.items())
               + f" · revues IA : {len(reviews)} (comptes Saxo seulement ; BoursoBank jamais touché)")  # fmt: skip
    if not apply:
        out.append("  simulation : rien n'est écrit — relancer avec --apply pour corriger")
        return out
    for a in aliases:
        a.isin = new.isin
    for group in rows.values():
        for r in group:
            r.isin = new.isin
    for rv in reviews:
        rv.isin = new.isin
    for i in s.scalars(select(Instrument).where(Instrument.isin.in_(old), Instrument.ticker_saxo == symbol)):
        i.ticker_saxo = None
    new.ticker_saxo = symbol
    s.flush()
    out.append("  corrigé ; config/instruments_map.yaml (sur le PC) : retirer « saxo: " + symbol
               + " » de l'ancien ISIN s'il y figure — l'alias corrigé passe de toute façon avant")  # fmt: skip
    return out


LEGAL = {"sa", "se", "nv", "ag", "plc", "inc", "corp", "corporation", "co", "ltd", "spa", "sca", "group", "groupe",
         "holding", "holdings", "the", "cie", "et", "de", "des", "la", "le", "les", "class", "a", "b"}  # fmt: skip


def name_key(name: str | None) -> str:
    """The first significant word of a company name, without accents, case or legal form (« Acme Corp » → acme)."""
    import re
    import unicodedata

    t = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    words = [w for w in re.split(r"[^a-z0-9]+", t) if w and w not in LEGAL]
    return words[0] if words else ""


def suggest(s: Session, symbol: str, saxo_name: str, current: str | None) -> tuple[str | None, str]:
    """(right ISIN or None, why) when the referential's name of ``current`` differs from Saxo's: the one value of the
    same venue whose name starts like Saxo's; nothing when none or several — never a guess."""
    from app.data.providers.saxo import parse_saxo_symbol
    from app.services.instruments import same_listing_venues

    key = name_key(saxo_name)
    inst = s.get(Instrument, current) if current else None
    if not key or (inst is not None and name_key(inst.name) == key):
        return None, "nom conforme"
    parsed = parse_saxo_symbol(symbol)
    if parsed is None:
        return None, "symbole sans place"
    venues = same_listing_venues(parsed[1])
    cands = [i for i in s.scalars(select(Instrument).where(Instrument.mic.in_(venues))) if name_key(i.name) == key]
    if len(cands) != 1:
        return (
            None,
            f"{len(cands)} valeur(s) du référentiel au nom « {saxo_name} » sur cette place : à rattacher à la main",
        )
    return cands[0].isin, f"le référentiel dit « {inst.name if inst else '—'} », Saxo dit « {saxo_name} »"
