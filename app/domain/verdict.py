"""Graduated verdict (docs/37 §37.2, lot L0 — built, OFF by default, awaiting the user's decisions D9–D15). Pure.

The engine's facts about one evaluation (action, « BLOQUÉ : … » / « WATCH : … » reasons, caps, « à vérifier »,
admission, whether the card was sized as speculative, a measured edge) give one level of a closed list, the reasons
that made it so and the size multiplier of the card. Python decides; the LLM never sets a level (CLAUDE.md rule 3).

- ``ecartee`` (grey): an absolute rule only — risk caps and exposure (rule 7), circuit breaker, change rate missing
  (no size), announcements a stop does not cover (R14, kept by the user's decision C of 29/10/2026), stop impossible
  or setup invalidated, no quantity within the risk budget, a run-up without published information (docs/26, docs/30),
  a detector the user keeps in watch only (ADR-013, unless lifted);
- ``a_surveiller`` (blue): no order today — a limit waiting for a pullback, a trigger not crossed, the price above the
  limit of its plage, R4 not met on the first target, no exploitable target, data « à vérifier » (rule 2);
- ``speculative`` (amber): only elements of appreciation (score, confidence, high risk, restricted gate, media run-up,
  unfavourable measured history, late entry after a letter) or a speculative detector — executable, labelled, at
  ``verdict.speculative_size_multiplier``;
- ``a_considerer`` (green): today's BUY without any block;
- ``conviction_forte`` (dark green): an ``a_considerer`` card whose source has a **measured** positive edge in an
  open market gate — none today (backtest n° 2 and the research plans found no positive edge).

Anything unrecognised fails safe: an unknown « BLOQUÉ » is absolute, an unknown « WATCH » means no order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.admission import Admission, Status, gross_reward_risk, p_max_rr, structure_intact
from app.domain.admission import Setup as SetupKind
from app.domain.entry_policies import NO_ORDER_FR, PolicyParams, SetupView, plan


class Level(StrEnum):
    CONVICTION_FORTE = "conviction_forte"
    A_CONSIDERER = "a_considerer"
    SPECULATIVE = "speculative"
    A_SURVEILLER = "a_surveiller"
    ECARTEE = "ecartee"


LEVEL_ORDER: dict[str, int] = {lv.value: i for i, lv in enumerate(Level)}
ORDERABLE = (Level.CONVICTION_FORTE, Level.A_CONSIDERER, Level.SPECULATIVE)


class Category(StrEnum):
    ABSOLUTE = "regle_absolue"  # → écartée
    NO_ORDER = "sans_ordre"  # → à surveiller
    APPRECIATION = "appreciation"  # → spéculative (executable, labelled, reduced size)


@dataclass(frozen=True)
class ReasonRule:
    code: str
    category: Category
    needles: tuple[str, ...]  # substrings of the engine's reason (first matching rule wins: absolute rules first)
    meaning: str  # what the reason means for the verdict (French, shown on the card and by the preview)
    r4_first_target: bool = False  # R4 not met on the first target: D12 may use the last exploitable target


RULES: tuple[ReasonRule, ...] = (
    # --- absolute rules (écartée) — checked first --------------------------------------------------------------
    ReasonRule(
        "plafond",
        Category.ABSOLUTE,
        (
            "BLOQUÉ : risque ouvert",
            "BLOQUÉ : risque de la ligne",
            "BLOQUÉ : renfort",
            "BLOQUÉ : montant engagé",
            "BLOQUÉ : grappe corrélée",
            "BLOQUÉ : risque courant",
            "BLOQUÉ : risque secteur",
            "positions dans le secteur",
            "BLOQUÉ : risque facteur",
            "BLOQUÉ : régime rouge",
            "BLOQUÉ : exposition",
            "(risk.positions_max)",
            "BLOQUÉ : couverture SRD",
            "BLOQUÉ : levier SRD",
            "BLOQUÉ : coût SRD",
        ),
        "plafond de risque ou d'exposition (règle 7)",
    ),  # fmt: skip
    ReasonRule(
        "portail_restreint_plus",
        Category.ABSOLUTE,
        ("portail restreint +",),
        "portail restreint + : régime rouge ou risque conjoncturel élevé, dérogation seulement (R10, R21)",
    ),
    ReasonRule("coupe_circuit", Category.ABSOLUTE, ("coupe-circuit",), "coupe-circuit ou sa mesure indisponible"),
    ReasonRule("change", Category.ABSOLUTE, ("taux de change",), "taux de change indisponible : taille incalculable"),
    ReasonRule(
        "r14",
        Category.ABSOLUTE,
        ("(R14)",),
        "annonce non couverte par un stop (R14, gel maintenu par la décision C du 29/10/2026)",
    ),
    ReasonRule(
        "stop_impossible",
        Category.ABSOLUTE,
        (
            "BLOQUÉ : stop au-dessus",
            "invalidation atteinte",
            "configuration dégradée",
            "objectif sous le stop",
            "invalidation de la lettre franchie",
        ),
        "stop impossible ou configuration invalidée",
    ),  # fmt: skip
    ReasonRule(
        "taille_nulle",
        Category.ABSOLUTE,
        ("aucune taille possible", "aucune quantité possible", "budget de risque insuffisant", "taille nulle"),
        "aucune quantité possible dans le budget de risque après frais (règle 7)",
    ),
    ReasonRule(
        "emballement",
        Category.ABSOLUTE,
        ("BLOQUÉ : emballement",),
        "emballement sans information publiée (docs/26, docs/30) : protection contre la manipulation",
    ),
    ReasonRule("surveillance_seule", Category.ABSOLUTE, ("(ADR-013)",), "détecteur en surveillance seule (ADR-013)"),
    # --- no order today (à surveiller) ---------------------------------------------------------------------------
    ReasonRule(
        "limite_en_attente",
        Category.NO_ORDER,
        ("en attente d'un repli",),
        "limite en attente d'un repli : R4 sur l'objectif 1 place la limite sous le cours",
        r4_first_target=True,
    ),
    ReasonRule(
        "ratio_au_declencheur",
        Category.NO_ORDER,
        ("au déclencheur",),
        "ratio gain/risque < 2 au déclencheur vers l'objectif 1 (R4)",
        r4_first_target=True,
    ),
    ReasonRule(
        "objectif_trop_proche",
        Category.NO_ORDER,
        ("objectif trop proche",),
        "objectif 1 trop proche pour le risque (R4)",
        r4_first_target=True,
    ),
    ReasonRule(
        "au_dessus_de_la_plage",
        Category.NO_ORDER,
        ("au-dessus de la limite de la plage",),
        "cours au-dessus de la limite de la plage : ne pas courir après le prix",
    ),
    ReasonRule(
        "declencheur_non_franchi",
        Category.NO_ORDER,
        ("déclencheur non franchi",),
        "déclencheur pas encore franchi : ordre après une clôture au-dessus",
    ),
    ReasonRule(
        "plage_sans_ordre",
        Category.NO_ORDER,
        ("entrée à plage P2",),
        "entrée à plage P2 impossible sur cette configuration",
    ),
    ReasonRule("sans_objectif", Category.NO_ORDER, ("pas d'objectif exploitable",), "pas d'objectif exploitable"),
    ReasonRule("non_analysable", Category.NO_ORDER, ("non analysable",), "non analysable : données insuffisantes"),
    # --- elements of appreciation (spéculative) ------------------------------------------------------------------
    ReasonRule("score", Category.APPRECIATION, ("ratio d'admission",), "score sous le seuil d'achat"),
    ReasonRule("confiance", Category.APPRECIATION, ("WATCH : confiance",), "peu d'éléments concordants (confiance)"),
    ReasonRule(
        "risque_eleve",
        Category.APPRECIATION,
        ("risque élevé", "stop trop large"),
        "risque élevé (stop lointain, liquidité, petite capitalisation ou devise)",
    ),
    ReasonRule(
        "portail_restreint", Category.APPRECIATION, ("portail restreint",), "contexte de marché restreint (R10)"
    ),
    ReasonRule(
        "hausse_mediatique", Category.APPRECIATION, ("hausse médiatique",), "hausse après une citation des médias"
    ),
    ReasonRule(
        "historique_defavorable",
        Category.APPRECIATION,
        ("historique défavorable",),
        "historique comparable défavorable (mesuré)",
    ),
    ReasonRule(
        "entree_tardive",
        Category.APPRECIATION,
        ("drift depuis l'ouverture",),
        "entrée tardive après une lettre (D5)",
    ),
)
UNKNOWN_BLOCK = ReasonRule("autre_blocage", Category.ABSOLUTE, (), "blocage non répertorié (prudence)")
UNKNOWN_WAIT = ReasonRule("autre_attente", Category.NO_ORDER, (), "condition non répertoriée (prudence)")
APPRECIATION_NOTE = ReasonRule("appreciation", Category.APPRECIATION, (), "élément d'appréciation")
RULE_BY_CODE: dict[str, ReasonRule] = {r.code: r for r in RULES}


@dataclass(frozen=True)
class Judged:
    """One reason of the engine, classified."""

    rule: ReasonRule
    text: str  # the engine's reason without its « BLOQUÉ : » / « WATCH : » prefix

    @property
    def code(self) -> str:
        return self.rule.code

    @property
    def category(self) -> Category:
        return self.rule.category

    @property
    def why(self) -> str:
        """« what it means : the engine's words », without saying it twice."""
        return self.text if self.rule.meaning in self.text else f"{self.rule.meaning} : {self.text}"


def strip_prefix(reason: str) -> str:
    for prefix in ("BLOQUÉ : ", "WATCH : "):
        if reason.startswith(prefix):
            return reason[len(prefix) :]
    return reason


def classify_reason(reason: str, caps: tuple[str, ...] = ()) -> Judged:
    """The rule of one engine reason; a reason listed among the caps (``details.blocages_plafonds``) is a cap."""
    text = strip_prefix(reason)
    if reason in caps:
        return Judged(RULE_BY_CODE["plafond"], text)
    for r in RULES:
        if any(n in reason for n in r.needles):
            return Judged(r, text)
    return Judged(UNKNOWN_BLOCK if reason.startswith("BLOQUÉ") else UNKNOWN_WAIT, text)


@dataclass(frozen=True)
class VerdictParams:
    """``verdict`` group of params.yaml (dated, optional; docs/37 L0)."""

    enabled: bool
    speculative_detectors: frozenset[str]
    speculative_size_multiplier: float
    speculative_last_target: bool
    speculative_d7_entry: str  # "plage" (D15, plan n° 4 policy P2) | "limite" (R4, D12 when allowed)
    speculative_stop_atr: float  # D15: stop k × ATR14 under the entry
    plage_cap_atr: float  # D15: limit = seuil × (1 + cap × ATR14 / close) (plan n° 4, confirm_cap_atr)
    plage_validity_sessions: int  # D15: validity of the plage (plan n° 4, validity)
    time_exit_sessions: int  # D15: exit at the close of the n-th session (plan n° 4, cell C1: X8 at 20 sessions)
    notify_speculative: bool


@dataclass(frozen=True)
class VerdictFacts:
    action: str  # BUY | WATCH | NO_TRADE as the engine computed it
    blocked: tuple[str, ...]  # « BLOQUÉ : … » / « WATCH : … »
    caps: tuple[str, ...] = ()  # details.blocages_plafonds (R21 covers these only)
    appreciation: tuple[str, ...] = ()  # elements of appreciation already moved off the blocks (a speculative card)
    to_verify: bool = False  # rule 2 (stale quote, portfolio state, a letter's failed check)
    price_status: str = "eod"
    admitted: bool = False  # an order is prepared (admission passed, a size exists)
    speculative: bool = False  # sized as speculative (speculative detector, or re-evaluated)
    edge_positive: bool = False  # a measured positive edge (favourable comparable history, backtest CI above 0)
    gate: str | None = None  # market gate of the venue (R10)
    lifted: tuple[str, ...] = ()  # reason codes that no longer apply (ADR-013 for a speculative detector, R4…)


@dataclass(frozen=True)
class Verdict:
    level: Level
    base: Level  # the level before « à vérifier » (rule 2): what the card is once its data is confirmed
    reasons: tuple[str, ...]  # what made it so, deciding reasons first (French)
    appreciation: tuple[str, ...]  # elements of appreciation shown on the card
    codes: tuple[str, ...]
    size_multiplier: float  # 1 for « à considérer » and above, the speculative multiplier, 0 without an order

    @property
    def main(self) -> str:
        return self.reasons[0] if self.reasons else ""

    @property
    def orderable(self) -> bool:
        return self.level in ORDERABLE

    def as_dict(self) -> dict[str, object]:
        return {
            "niveau": self.level.value,
            "niveau_hors_verification": self.base.value,
            "raisons": list(self.reasons),
            "appreciation": list(self.appreciation),
            "codes": list(self.codes),
            "taille": self.size_multiplier,
        }


def grade(f: VerdictFacts, vp: VerdictParams) -> Verdict:
    """The level of one evaluation (module docstring), with its reasons and the size multiplier of the card."""
    judged = [classify_reason(b, f.caps) for b in f.blocked]
    for a in f.appreciation:  # stored without their prefix: classified again, an element of appreciation anyway
        j = classify_reason(a if a.startswith(("WATCH : ", "BLOQUÉ : ")) else f"WATCH : {a}")
        judged.append(j if j.category == Category.APPRECIATION else Judged(APPRECIATION_NOTE, strip_prefix(a)))
    judged = [j for j in judged if j.code not in f.lifted]
    absolute = [j for j in judged if j.category == Category.ABSOLUTE]
    no_order = [j for j in judged if j.category == Category.NO_ORDER]
    appr = [j for j in judged if j.category == Category.APPRECIATION]
    reasons: list[str]
    if f.action == "NO_TRADE" or absolute or f.price_status == "unavailable":
        base = Level.ECARTEE
        reasons = [j.why for j in absolute]
        if f.price_status == "unavailable":
            reasons.append("cours indisponible (règle 2)")
        reasons = reasons or ["pas d'opération : configuration invalidée"]
        deciding = absolute
    elif no_order or not f.admitted:
        base = Level.A_SURVEILLER
        reasons = [j.why for j in no_order] or ["aucun ordre préparé"]
        deciding = no_order
    elif appr or f.speculative:
        base = Level.SPECULATIVE
        reasons = [j.why for j in appr] or ["détecteur spéculatif (verdict.speculative_detectors)"]
        deciding = appr
    elif f.action != "BUY":
        base, reasons, deciding = Level.A_SURVEILLER, ["pas d'achat proposé"], []
    elif f.edge_positive and f.gate == "ouvert":
        base, deciding = Level.CONVICTION_FORTE, []
        reasons = ["avantage mesuré positif, portail ouvert, ratio ≥ 2 sur l'objectif 1"]
    else:
        base, deciding = Level.A_CONSIDERER, []
        reasons = ["configuration valide, risque borné, ratio ≥ 2 sur l'objectif 1 ; avantage non mesuré positif"]
    level = base
    if base in ORDERABLE and (f.to_verify or f.price_status == "stale"):
        level = Level.A_SURVEILLER
        reasons = ["à vérifier : cours non frais ou contrôle en échec, pas de fiche d'ordre (règle 2)", *reasons]
    if base in (Level.CONVICTION_FORTE, Level.A_CONSIDERER):
        size = 1.0
    elif base == Level.SPECULATIVE:
        size = vp.speculative_size_multiplier
    else:
        size = 0.0
    return Verdict(
        level,
        base,
        tuple(reasons),
        tuple(j.text for j in appr),
        tuple(dict.fromkeys(j.code for j in (deciding or judged))),
        size,
    )


def r4_first_target_only(blocked: tuple[str, ...], caps: tuple[str, ...] = ()) -> bool:
    """Every reason that prevents an order is R4 on the first target (no absolute rule, nothing else to wait for):
    the case where D12 may try the last exploitable target."""
    judged = [classify_reason(b, caps) for b in blocked]
    no_order = [j for j in judged if j.category == Category.NO_ORDER]
    return (
        bool(no_order)
        and all(j.rule.r4_first_target for j in no_order)
        and not any(j.category == Category.ABSOLUTE for j in judged)
    )


def has_appreciation(blocked: tuple[str, ...], caps: tuple[str, ...] = ()) -> bool:
    return any(classify_reason(b, caps).category == Category.APPRECIATION for b in blocked)


def split_appreciation(blocked: list[str], caps: tuple[str, ...] = ()) -> tuple[list[str], list[str]]:
    """(reasons that stay blocks, elements of appreciation without their prefix) — a speculative BUY keeps no block."""
    keep, appr = [], []
    for b in blocked:
        j = classify_reason(b, caps)
        if j.category == Category.APPRECIATION:
            appr.append(j.text)
        else:
            keep.append(b)
    return keep, appr


# --- D12: the last exploitable target --------------------------------------------------------------------------


def last_target_admits(
    price: float,
    entry_level: float,
    kind: SetupKind,
    last_target: float,
    stop: float,
    fee_fraction: float,
    min_reward_risk: float,
    p_max_technical: float | None,
) -> bool:
    """R4 on the last exploitable target (D12): the maximum price is above the stop and reaches the price of the
    analysis — or the seuil of a breakout not crossed yet (its order fills there, never under it)."""
    pm = p_max_rr(last_target, stop, fee_fraction, min_reward_risk)
    if p_max_technical is not None:
        pm = min(pm, p_max_technical)
    need = entry_level if kind == SetupKind.BREAKOUT and price < entry_level else price
    return pm > stop and pm >= need


# --- D15: the speculative d7 entry (plan n° 4, policy P2) -----------------------------------------------------


@dataclass(frozen=True)
class PlageP2:
    """Buy stop-limit on the confirmed crossing (plan n° 4, policy P2, ``entry_policies.plan``): seuil = max(the
    detector's trigger, the signal day's high) + 1 tick, limit = seuil × (1 + cap × ATR14 / close), valid N sessions;
    an opening at or under the setup's structural stop before the fill cancels it. Stop k × ATR14 under the fill (exit
    X8 of the bench), time exit, no fixed first target: the targets are landmarks."""

    seuil: float
    limite: float
    validite_seances: int
    stop: float  # seuil − k × ATR14: the stop after a fill at the seuil, the lowest fill of a buy-stop (sizing)
    stop_atr: float
    atr14: float
    stop_structurel: float
    sortie_seances: int
    cap_atr: float

    def as_dict(self) -> dict[str, float | int | str]:
        return {
            "politique": "P2 (plan n° 4) : stop d'achat à plage sur le franchissement confirmé",
            "seuil": round(self.seuil, 6),
            "limite": round(self.limite, 6),
            "plafond_atr": self.cap_atr,
            "validite_seances": self.validite_seances,
            "stop": round(self.stop, 6),
            "stop_atr": self.stop_atr,
            "atr14": round(self.atr14, 6),
            "annulation_ouverture_au_stop_structurel": round(self.stop_structurel, 6),
            "sortie_au_temps_seances": self.sortie_seances,
        }


def speculative_plage(
    close: float,
    high: float,
    atr14: float | None,
    trigger: float,
    structural_stop: float,
    targets: tuple[float, ...],
    pp: PolicyParams,
    stop_atr: float,
    time_exit_sessions: int,
) -> PlageP2 | str:
    """The P2 order of the plan n° 4 for a d7 setup (``app.domain.entry_policies.plan``, the scalar mirror of the
    research's ``policy_orders``), or why there is none (French)."""
    sv = SetupView(close, high, atr14, None, None, trigger, trigger, structural_stop, targets, 0.0)
    order = plan("P2", sv, pp)
    if isinstance(order, str):
        return NO_ORDER_FR.get(order, order)
    assert atr14 is not None  # plan() returns « atr_inconnu » otherwise
    stop = order.level - stop_atr * atr14
    if not stop > 0 or not math.isfinite(stop):
        return f"stop à {_fr(stop_atr, 'g')} ATR sous le seuil non positif"
    return PlageP2(order.level, order.max_price, order.valid, stop, stop_atr, atr14, structural_stop,
                   time_exit_sessions, pp.confirm_max_atr)  # fmt: skip


def plage_admission(
    price: float,
    target: float | None,
    stop: float | None,
    kind: SetupKind,
    level_low: float | None,
    limit: float,
) -> Admission:
    """Admission of a P2 plage (D15): no R4 on the limit — the plan n° 4 tests the policy without it, exits are by
    time — but the setup must be testable (a target above the stop), its structure intact (R5, wording of ``kind``),
    and the price never above the limit (never chase the price)."""
    if target is None or stop is None:
        missing = "objectif chiffré" if target is None else "niveau d'invalidation"
        return Admission(Status.NOT_TESTABLE, None, None, None, [f"non testable : {missing} absent"])
    if target <= stop:
        return Admission(Status.NOT_TESTABLE, None, None, None, ["non testable : objectif sous le stop"])
    adm = Admission(Status.ADMITTED, limit, None, gross_reward_risk(price, target, stop))
    if level_low is not None and not structure_intact(kind, price, level_low):
        what = "support perdu en clôture" if kind == SetupKind.PULLBACK else "franchissement rendu en clôture"
        adm.status = Status.DEGRADED
        adm.reasons.append(
            f"configuration dégradée : {what} ({price:.4g} < {level_low:.4g}) — "
            "le prix maximal est une condition nécessaire, pas un signal d'entrée"
        )
        return adm
    if price > limit:
        adm.status = Status.REFUSED
        adm.reasons.append(f"refusé — limite de la plage {limit:.4g} dépassée de {price / limit - 1:.2%}")
    return adm


# --- card texts ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Edge:
    """The measured expectancy of the card's source, as the engine loads it: the live comparable history first
    (docs/18), else the backtest at full cost (docs/27). Never estimated: None when not measured."""

    r: float | None
    n: int | None
    source: str  # « historique comparable » | « backtest au coût complet »
    ci_low: float | None = None
    ci_high: float | None = None
    positive: bool = False  # measured positive: favourable history, or a backtest interval above 0
    notes: tuple[str, ...] = field(default_factory=tuple)


def _fr(x: float, fmt: str = "+.2f") -> str:
    return format(x, fmt).replace(".", ",").replace("-", "−")


def size_label(m: float) -> str:
    """« ½ », « ¼ », « ¾ » or « × 0,6 »."""
    for v, s in ((0.5, "½"), (0.25, "¼"), (0.75, "¾"), (1.0, "pleine")):
        if abs(m - v) < 1e-9:
            return s
    return f"× {_fr(m, 'g')}"


def edge_text(edge: Edge | None, plage: bool = False) -> str:
    """« Spéculative — espérance historique … » with the measured number, or « non mesurée »."""
    if edge is None or edge.r is None:
        txt = "Spéculative — espérance historique non mesurée"
    else:
        n = f", n {edge.n}" if edge.n else ""
        ci = (
            f", IC 95 % [{_fr(edge.ci_low)} ; {_fr(edge.ci_high)}]"
            if edge.ci_low is not None and edge.ci_high is not None
            else ""
        )
        txt = f"Spéculative — espérance historique {_fr(edge.r)} R au coût complet ({edge.source}{n}{ci})"
    if plage:
        txt += (
            " ; entrée à plage et sortie au temps non confirmées par la recherche (plans n° 4 et 6, docs/34 "
            "§34.11 et §34.13)"
        )
    return txt
