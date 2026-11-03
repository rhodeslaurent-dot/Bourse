"""Lot R1 (docs/38) — the review of the day: one decision per idea, in words, from the engine's output. Pure.

The engine's proposal carries an action (BUY / WATCH / NO_TRADE), its gates (« BLOQUÉ : … », « WATCH : … »), the
maximum entry price, the stop, the targets and, for a letter, the levels the letter wrote. This module turns them
into what the user asked for on 02/11/2026 (« je sais exactement quoi arbitrer, pourquoi et quoi faire ») :

- one **decision** from a closed list (``DECISIONS``) — never several labels for one idea;
- the **main reason** in one plain sentence (the first decisive gate, in a fixed priority);
- **what to do** in one sentence (post a limit, wait for a pullback, check the live quote, nothing);
- a small table per target: gross gain and gross gain / risk **at the current price** (arithmetic on stored values,
  never an estimate: an unknown price leaves the cells empty);
- the **verdict of the day** in one line.

A letter whose published trigger lies above the tool's maximum entry price is « inexploitable par construction »:
no price satisfies both the letter and the arithmetic (the case of 02/11/2026 the user's own review found).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

DECISIONS = ("acheter", "pari", "attendre_repli", "a_verifier", "inexploitable", "surveiller", "refus")
DECISION_FR = {
    "acheter": "ACHETER",
    "pari": "PARI SPÉCULATIF",
    "attendre_repli": "ATTENDRE UN REPLI",
    "a_verifier": "À VÉRIFIER",
    "inexploitable": "INEXPLOITABLE",
    "surveiller": "SURVEILLER",
    "refus": "REFUS",
}
DECISION_TONE = {
    "acheter": "ok",
    "pari": "warn",
    "attendre_repli": "info",
    "a_verifier": "warn",
    "inexploitable": "bad",
    "surveiller": "muted",
    "refus": "bad",
}

# the gates' key phrases → one plain sentence, in the order a reader needs them (the first found is the reason)
_REASONS: tuple[tuple[str, str], ...] = (
    ("coupe-circuit", "le coupe-circuit est déclenché : aucune nouvelle entrée"),
    ("plafond", "un plafond de risque ou d'exposition serait dépassé"),
    ("annonce", "une annonce importante tombe avant que le stop puisse protéger la ligne"),
    ("stop trop large", "le stop serait trop loin sous l'entrée"),
    ("pas d'objectif exploitable", "aucun objectif chiffrable au-dessus du cours"),
    ("objectif trop proche", "l'objectif est trop proche pour le risque pris"),
    ("ratio", "le gain possible ne paie pas assez le risque"),
    ("résultats", "des résultats tombent avant la fin de l'horizon"),
    ("surveillance seulement", "ce détecteur n'est qu'en surveillance (espérance mesurée négative)"),
    ("risque élevé", "le risque de la ligne est jugé élevé (stop, liquidité, taille ou devise)"),
    ("ratio d'admission", "le dossier est trop faible au score"),
    ("déclencheur non atteint", "le déclencheur n'est pas encore franchi"),
    ("en attente d'un repli", "le cours est au-dessus du prix maximal d'achat"),
)


def _plain(gate: str) -> str:
    g = gate.split(":", 1)[1].strip() if ":" in gate[:12] else gate.strip()
    return g[:1].lower() + g[1:] if g else g


def main_reason(blocked: Sequence[str]) -> str | None:
    """The decisive gate in plain words: an absolute block (« BLOQUÉ ») first, then the fixed priority of
    ``_REASONS``; an unknown gate is given as written (never dropped)."""
    gates = [str(b) for b in blocked if str(b).strip()]
    if not gates:
        return None
    absolute = [g for g in gates if g.startswith("BLOQUÉ")]
    pool = absolute or gates
    low = [g.lower() for g in pool]
    # « ratio d'admission » (the score) must not be read as the reward / risk « ratio »
    for key, sentence in _REASONS:
        for g, lg in zip(pool, low, strict=True):
            if key == "ratio" and "ratio d'admission" in lg:
                continue
            if key in lg:
                if absolute:
                    return f"{sentence} ({_plain(g)})"
                return sentence + _detail(key, g)
    return _plain(pool[0])


def _detail(key: str, gate: str) -> str:
    """The figures of a gate, kept with its plain sentence (« 56 % des points, 80 % requis », « 9.4% sous
    l'entrée »); nothing when the gate carries none."""
    if key == "ratio d'admission":
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*<\s*(\d+(?:[.,]\d+)?)", gate)
        if m:
            got, need = (float(x.replace(",", ".")) for x in m.groups())
            return f" ({got:.0%} des points, {need:.0%} requis)"
        return ""
    if key == "en attente d'un repli":
        return ""
    m = re.search(r"\(([^()]*\d[^()]*)\)", gate)
    return f" ({m.group(1).strip()})" if m else ""


@dataclass(frozen=True)
class Idea:
    """What the review needs of one proposal (all stored values; None = unknown)."""

    action: str  # BUY | WATCH | NO_TRADE
    blocked: tuple[str, ...] = ()
    to_verify: bool = False
    price: float | None = None  # last quote of the analysis (its own status decides ``to_verify``)
    p_max: float | None = None  # maximum entry price (limit)
    stop: float | None = None
    targets: tuple[float, ...] = ()
    letter_trigger: float | None = None  # the trigger the letter wrote (« déclencheur »)
    shares: int | None = None
    plage_threshold: float | None = None  # a breakout's order à plage: its seuil
    expiry: str | None = None  # the date to type at the broker
    speculative: bool = False  # docs/37: a « spéculative » card of the graduated verdict (no measured edge)
    appreciation: tuple[str, ...] = ()  # its elements of appreciation (gates moved off the blocks)
    bet_target: float | None = None  # D12: the final target R4 was computed on (the first one did not pay)


@dataclass(frozen=True)
class IdeaVerdict:
    decision: str  # DECISIONS
    label: str
    tone: str
    reason: str
    todo: str


def fmt_price(x: float | None) -> str:
    """A price as a reader expects it: 2 decimals from 10, 3 under 10, 4 significant digits under 1 — trailing
    zeros dropped (100.05, 98.08, 1.762, 0.0452); « — » when unknown."""
    if x is None:
        return "—"
    a = abs(x)
    s = f"{x:.2f}" if a >= 10 else f"{x:.3f}" if a >= 1 else f"{x:.4g}"
    return s.rstrip("0").rstrip(".") if "." in s and "e" not in s else s


def _fmt(x: float) -> str:
    return fmt_price(x)


def idea_verdict(i: Idea) -> IdeaVerdict:
    """One decision per idea, its main reason and what to do — in this order of precedence: inexploitable by
    construction, an absolute block, a quote to check, a buy, a limit waiting for a pullback, any other watch."""
    reason = main_reason(i.blocked)

    def out(decision: str, why: str, todo: str) -> IdeaVerdict:
        return IdeaVerdict(decision, DECISION_FR[decision], DECISION_TONE[decision], why, todo)

    if i.letter_trigger is not None and i.p_max is not None and i.letter_trigger > i.p_max:
        return out(
            "inexploitable",
            f"le prix maximal d'achat ({_fmt(i.p_max)}) est sous le déclencheur publié ({_fmt(i.letter_trigger)}) : "
            "aucun prix ne respecte à la fois la lettre et le calcul",
            "Rien à faire : ce n'est pas un repli qui lèvera la contradiction",
        )
    if any(str(b).startswith("BLOQUÉ") for b in i.blocked) or i.action == "NO_TRADE":
        return out("refus", reason or "écartée par une règle", "Rien à faire")
    if i.to_verify:
        return out(
            "a_verifier",
            "le cours n'est pas assez frais pour décider (règle 2)",
            "Vérifier le cours en direct chez le courtier avant toute décision",
        )
    if i.action == "BUY" and i.p_max is not None:
        qty = f"{i.shares} titres, " if i.shares else ""
        stop = f"stop {_fmt(i.stop)}" if i.stop is not None else "stop indisponible"
        if i.plage_threshold is not None:
            todo = (
                f"Poser l'ordre à plage de déclenchement : seuil {_fmt(i.plage_threshold)}, limite {_fmt(i.p_max)} "
                f"({qty}{stop})"
            )
        else:
            todo = f"Poser l'ordre limite à {_fmt(i.p_max)} ({qty}{stop})"
        if i.expiry:
            todo += f", valable jusqu'au {i.expiry}"
        if i.speculative:  # 03/11/2026 (Elior): never « toutes les règles sont satisfaites » for a bet
            why = [main_reason([a]) or a for a in i.appreciation[:2]]
            bet = f"pari sur l'objectif final {_fmt(i.bet_target)}" if i.bet_target is not None else "carte spéculative"
            reason = bet + (" ; " + " ; ".join(why) if why else "") + " — aucun avantage validé par la mesure"
            return out("pari", reason, "Seulement si vous acceptez ce pari : " + todo[0].lower() + todo[1:])
        return out("acheter", "toutes les règles sont satisfaites", todo)
    waiting = any("en attente d'un repli" in str(b) for b in i.blocked)
    other = [b for b in i.blocked if "en attente d'un repli" not in str(b)]
    if waiting and i.p_max is not None and not any(_blocks_order(str(b)) for b in other):
        limit = f"du prix maximal d'achat ({_fmt(i.p_max)})"
        where = (
            f"le cours ({_fmt(i.price)}) est {i.price / i.p_max - 1:.1%} au-dessus {limit}"
            if i.price
            else f"le cours est au-dessus {limit}"
        )
        return out(
            "attendre_repli",
            where,
            f"Rien aujourd'hui ; une limite à {_fmt(i.p_max)} n'achèterait qu'en cas de repli",
        )
    return out("surveiller", reason or "pas d'entrée aujourd'hui", "Rien aujourd'hui")


def _blocks_order(gate: str) -> bool:
    """A watch reason that forbids even a waiting limit (the order book's rule: only « en attente d'un repli »)."""
    g = gate.lower()
    return any(k in g for k in _NO_ORDER)


_NO_ORDER = ("stop trop large", "pas d'objectif", "objectif trop proche", "surveillance seulement", "risque élevé",
             "ratio d'admission", "résultats", "déclencheur non atteint")  # fmt: skip


def target_rows(price: float | None, stop: float | None, targets: Sequence[float]) -> list[dict[str, float | None]]:
    """Per target: gross gain from the current price and gross gain / risk at that price (risk = price − stop).
    Unknown price, stop at or above the price → the cells stay empty (never estimated)."""
    rows: list[dict[str, float | None]] = []
    for t in targets:
        gain = (t / price - 1) if price and price > 0 else None
        rr = (t - price) / (price - stop) if price and stop is not None and price > stop else None
        rows.append({"objectif": t, "gain_brut": gain, "rr_brut": rr})
    return rows


def day_verdict(
    decisions: Sequence[str], circuit_tripped: bool = False, gate: str | None = None
) -> tuple[str, list[str]]:
    """The verdict of the day in one line, and up to three short reasons (counts of the other decisions)."""
    c = Counter(decisions)
    reasons: list[str] = []
    if circuit_tripped:
        reasons.append("coupe-circuit déclenché : aucune nouvelle entrée")
    if gate and gate not in ("ouvert", "open"):
        reasons.append(f"portail de marché : {gate}")
    for d, text in (("pari", "en pari spéculatif (aucun avantage validé par la mesure)"),
                    ("attendre_repli", "au-dessus de leur prix maximal (limites prêtes pour un repli)"),
                    ("a_verifier", "à vérifier (cours pas assez frais)"),
                    ("inexploitable", "inexploitable(s) par construction"),
                    ("refus", "refusée(s)"), ("surveiller", "à surveiller")):  # fmt: skip
        if c.get(d):
            reasons.append(f"{c[d]} idée(s) {text}")
    n_buy = c.get("acheter", 0)
    if n_buy and not circuit_tripped:
        head = f"{n_buy} achat(s) possible(s) aujourd'hui"
    elif not decisions:
        head = "Aucune idée aujourd'hui"
    else:
        head = "Aucun achat aujourd'hui"
    return head, reasons[:4]


__all__ = [
    "DECISIONS",
    "DECISION_FR",
    "Idea",
    "IdeaVerdict",
    "day_verdict",
    "fmt_price",
    "idea_verdict",
    "main_reason",
    "target_rows",
]
