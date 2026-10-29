"""Deterministic analysis of a value recommended by a letter (docs/20 §20.10, report of 29/10/2026). Pure.

Every value a letter recommends (« achat » or « à surveiller », ISIN resolved) is analysed by the tool itself:
one builder turns the levels written in the letter (closed list of names, ``letter_levels``) and the stored bars
into a :class:`~app.domain.detectors.Setup` that the engine admits, costs and sizes like any other. Nothing is
invented: a level is either quoted from the letter or computed here from stored prices.

- **Entry mode** (closed list :data:`ENTRY_MODES`): ``achat`` (the letter's buy price, a limit), ``support``
  (pullback to the letter's support), ``declencheur`` (breakout: entry = trigger × (1 + ``trigger_buffer_pct``),
  technical maximum = trigger × (1 + ``entry_max_pct_above_trigger``), obstacle = trigger), ``marche`` (levels
  but no entry level — « ouverture haussière », trendline broken: entry = price at analysis) and ``outil`` (no
  level at all: as ``marche``, or the setup of a detector firing on the value, tagged « lettre + détecteur »).
- **Two stops, both kept**: the letter's invalidation (quoted) and the tool's structure stop (R1, the detectors'
  rule: under the nearest support zone × (1 − wick), else the lowest low of ``stop_lookback_low`` sessions −
  ``buffer_atr_mult`` × ATR14). ``stop_policy`` picks one; the default keeps the letter's when it is within
  ``risk.stop.max_distance_adr`` × ADR20 of the entry, the structure stop otherwise.
- **Targets**: the letter's (``objectif``, ``objectif_2``, ``objectif_3``) in order, then the detected
  resistances above the last one; without a letter target, the exploitable levels (resistances, analysts' mean
  target, 52-week high) as the detectors do (R4: never an R multiple).
- :func:`checklist` and :func:`verdict` read the engine's outcome and return the card's control list and one of
  :data:`VERDICTS`, with the reason verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.admission import Setup as SetupKind
from app.domain.detectors import DetectorContext, Setup, structure_stop
from app.domain.indicators import Bar, DailyFeatures
from app.domain.letter_levels import recalculable
from app.domain.stops import place_stop
from app.domain.zones import DetectedZone, exploitable_targets, resistances_above, supports_below

ENTRY_MODES = ("achat", "support", "declencheur", "marche", "outil")
STOP_POLICIES = ("letter_only", "structure_only", "letter_within_max_else_structure")
VERDICTS = (
    "entrer_maintenant",
    "fiche_fixing",
    "alerte_declencheur",
    "attendre_repli",
    "bloque",
    "rejet",
    "non_analysable",
)
# why a value could not be analysed (closed list, counted in the letters funnel)
NOT_ANALYSABLE = (
    "referentiel",
    "inactive",
    "historique",
    "indicateurs",
    "niveaux",
    "stop",
    "change",
    "moteur",
    "erreur",
)
CHECK_KEYS = ("valeur", "historique", "cours", "structure", "objectif", "ratio", "stop", "r5", "macro", "resultats",
              "taille", "analyse")  # fmt: skip
VERIFY_KEYS = ("cours", "structure")  # a failed item among these makes the proposal « à vérifier »
LETTER_TARGET_KEYS = ("objectif", "objectif_2", "objectif_3")

# markers of the engine's gate lines read by :func:`verdict` (kept here so that both sides use the same words)
TRIGGER_WAIT = "déclencheur non franchi"
UNDER_TRIGGER = "au déclencheur"
WAITING_LIMIT = "en attente d'un repli"
INVALIDATED = "invalidation de la lettre franchie"
REJECT_MARKERS = (
    INVALIDATED,
    "objectif trop proche",
    "pas d'objectif exploitable",
    "stop trop large",
    "configuration dégradée",
    "non testable",
)


@dataclass(frozen=True)
class LetterParams:
    """``letters`` group of params.yaml (dated), with ``risk.stop.max_distance_adr`` and ``stop_lookback_low``."""

    stop_policy: str = "letter_within_max_else_structure"
    trigger_buffer_pct: float = 0.003
    entry_max_pct_above_trigger: float = 0.01
    trigger_alert_pct: float = 0.03
    min_history_sessions: int = 60
    stop_lookback_low: int = 10
    max_stop_distance_adr: float = 2.5
    analyse_every_recommendation: bool = True


@dataclass(frozen=True)
class LetterRecommendation:
    source: str
    item_id: int | None
    direction: str  # buy | watch
    levels: dict[str, float]  # canonical names (letter_levels.KEYS), numbers written in the letter


@dataclass(frozen=True)
class NotAnalysable:
    code: str  # one of NOT_ANALYSABLE
    reason: str


@dataclass
class LetterSetup:
    mode: str  # one of ENTRY_MODES
    setup: Setup
    stop_lettre: float | None
    stop_structure: float | None
    stop_structure_basis: str | None  # zone | structure | the borrowed detector's basis
    stop_retenu: str  # lettre | structure
    max_stop_distance: float  # fraction of the entry (max_distance_adr × ADR20)
    targets_lettre: list[float]
    targets_outil: list[float]
    trigger: float | None
    level_low_origin: str
    level_low_structural: bool  # False: no structure level under the entry, the stop stands for it
    detector: str | None = None
    notes: list[str] = field(default_factory=list)

    def analysis(self) -> dict[str, Any]:
        """What the tool computed (``details.analyse_outil``): every number from stored bars or the letter."""
        st = self.setup
        entry = st.entry_level

        def dist(stop: float | None) -> float | None:
            return round((entry - stop) / entry, 4) if stop is not None and entry > 0 else None

        return {
            "mode": self.mode,
            "detecteur": self.detector,
            "type": st.kind.value,
            "entree": round(entry, 4),
            "p_max_technique": round(st.p_max_technical, 4),
            "declencheur": self.trigger,
            "stop_lettre": self.stop_lettre,
            "distance_stop_lettre": dist(self.stop_lettre),
            "stop_structure": round(self.stop_structure, 4) if self.stop_structure is not None else None,
            "base_stop_structure": self.stop_structure_basis,
            "distance_stop_structure": dist(self.stop_structure),
            "stop_retenu": self.stop_retenu,
            "stop": round(st.stop, 4),
            "distance_max": round(self.max_stop_distance, 4),
            "objectifs_lettre": list(self.targets_lettre),
            "objectifs_outil": [round(t, 4) for t in self.targets_outil],
            "bas_de_structure": round(st.level_low, 4),
            "origine_bas_de_structure": self.level_low_origin,
            "structure_enregistree": self.level_low_structural,
            "notes": list(self.notes),
        }


def _structure_stop(
    entry: float, bars: list[Bar], atr: float, ctx: DetectorContext, lookback: int, max_dist: float
) -> tuple[float | None, str | None]:
    """R1 under the entry: the detectors' rule when the recent lows are under the entry; for a limit far under the
    recent lows, the nearest support zone under the entry; None when no structure lies under the entry."""
    lows = [b.low for b in bars[-max(1, lookback) :]]
    if lows and min(lows) - ctx.stop_buffer_atr_mult * atr < entry:
        stop, basis, _ = structure_stop(entry, min(lows), atr, ctx, max_dist)
        if stop < entry:
            return stop, basis
    zones = [z for z in supports_below(entry, ctx.zones) if z.high < entry]
    if zones:
        return place_stop(zones[0].zone, ctx.wick_buffer_pct), "zone"
    return None, None


def _choose_stop(
    policy: str, letter: float | None, structure: float | None, entry: float, max_abs: float
) -> tuple[float | None, str]:
    letter_ok = letter is not None and entry - letter <= max_abs
    if policy == "letter_only":
        order = [(letter, "lettre"), (structure, "structure")]
    elif policy == "structure_only":
        order = [(structure, "structure"), (letter, "lettre")]
    else:  # letter_within_max_else_structure (default, also for an unknown value)
        order = [(letter if letter_ok else None, "lettre"), (structure, "structure"), (letter, "lettre")]
    for stop, which in order:
        if stop is not None:
            return stop, which
    return None, "lettre"


def _nearest_support(price: float, stop: float, zones: list[DetectedZone]) -> DetectedZone | None:
    return next((z for z in supports_below(price, zones) if z.low > stop), None)


def build(
    rec: LetterRecommendation,
    bars: list[Bar],
    features: DailyFeatures,
    ctx: DetectorContext,
    params: LetterParams,
    price: float,
    detector_setups: list[Setup] | None = None,
) -> LetterSetup | NotAnalysable:
    """The letter's setup as the tool computes it, or why it cannot be analysed. ``price`` is the price at analysis
    (quote at reception, else the last stored close); ``detector_setups`` the setups of the detectors that fired on
    the value and may be borrowed (watch-only detectors already removed by the caller)."""
    if len(bars) < params.min_history_sessions:
        return NotAnalysable("historique", f"{len(bars)} séances d'historique, {params.min_history_sessions} exigées")
    atr, adr = features.atr14, features.adr20
    if not atr or not adr or price <= 0:
        return NotAnalysable("indicateurs", "amplitude (ATR14, ADR20) ou cours indisponible : prix plats ou trou")
    lv = {k: float(v) for k, v in (rec.levels or {}).items() if isinstance(v, int | float)}
    if not params.analyse_every_recommendation and not recalculable(lv):
        return NotAnalysable(
            "niveaux",
            "niveaux non recalculables (achat, support ou déclencheur, stop et objectif exigés) : "
            "analyse de chaque recommandation désactivée (letters.analyse_every_recommendation)",
        )
    notes: list[str] = []
    buf, above = params.trigger_buffer_pct, params.entry_max_pct_above_trigger
    trigger: float | None = None
    borrowed: Setup | None = None
    if lv.get("achat") is not None:
        mode, kind, entry = "achat", SetupKind.PULLBACK, lv["achat"]
        p_max_tech = entry * (1 + above)
    elif lv.get("support") is not None:
        mode, kind, entry = "support", SetupKind.PULLBACK, lv["support"]
        p_max_tech = entry * (1 + above)
    elif lv.get("declencheur") is not None:
        trigger = lv["declencheur"]
        mode, kind, entry = "declencheur", SetupKind.BREAKOUT, trigger * (1 + buf)
        p_max_tech = trigger * (1 + above)
    elif lv:
        mode, kind, entry = "marche", SetupKind.PULLBACK, price
        p_max_tech = price * (1 + above)
    else:
        mode = "outil"
        borrowed = detector_setups[0] if detector_setups else None
        if borrowed is not None:
            kind, entry, p_max_tech = borrowed.kind, borrowed.entry_level, borrowed.p_max_technical
            notes.append(f"configuration du détecteur {borrowed.detector} reprise (lettre + détecteur)")
        else:
            kind, entry, p_max_tech = SetupKind.PULLBACK, price, price * (1 + above)
            notes.append("aucun niveau écrit dans la lettre : entrée, stop et objectifs calculés par l'outil")

    max_frac = params.max_stop_distance_adr * adr
    max_abs = max_frac * entry
    stop_lettre = lv.get("stop")
    s_struct: float | None
    s_basis: str | None
    if borrowed is not None:
        s_struct, s_basis = borrowed.stop, borrowed.stop_basis
    else:
        s_struct, s_basis = _structure_stop(entry, bars, atr, ctx, params.stop_lookback_low, max_abs)
    usable_letter = stop_lettre if stop_lettre is not None and stop_lettre < entry else None
    if stop_lettre is not None and usable_letter is None:
        notes.append(f"invalidation de la lettre ({stop_lettre:.4g}) au-dessus de l'entrée ({entry:.4g}) : ignorée")
    stop, retenu = _choose_stop(params.stop_policy, usable_letter, s_struct, entry, max_abs)
    if stop is None:
        return NotAnalysable(
            "stop", "aucun stop possible : ni invalidation publiée sous l'entrée, ni structure (zone, plus bas récent)"
        )
    if retenu == "structure" and usable_letter is not None:
        notes.append(
            f"stop de structure de l'outil {stop:.4g} (invalidation de la lettre à {usable_letter:.4g}, "
            f"{'trop large' if entry - usable_letter > max_abs else 'non retenue'} : "
            f"−{(entry - usable_letter) / entry:.1%})"
        )
    warnings: list[str] = list(borrowed.warnings) if borrowed is not None else []
    if entry - stop > max_abs and not any("stop trop large" in w for w in warnings):
        warnings.append(f"stop trop large ({(entry - stop) / entry:.1%} sous l'entrée)")
    if stop_lettre is not None and price <= stop_lettre:
        warnings.append(f"{INVALIDATED} (cours {price:.4g} ≤ {stop_lettre:.4g})")

    # R5 level: the letter's support or trigger, else the nearest detected support zone above the stop
    structural = True
    if mode == "support" or (mode == "achat" and lv.get("support") is not None):
        level_low, origin = lv["support"], "support publié par la lettre"
    elif mode == "declencheur" and trigger is not None:
        level_low, origin = trigger, "déclencheur publié par la lettre (obstacle à tenir en clôture)"
    elif borrowed is not None:
        level_low, origin = borrowed.level_low, f"niveau du détecteur {borrowed.detector}"
    else:
        zone = _nearest_support(min(price, entry), stop, ctx.zones)
        if zone is not None:
            level_low, origin = zone.low, f"zone de support détectée {zone.low:.4g}–{zone.high:.4g}"
        else:
            level_low, origin, structural = stop, "aucune zone de support détectée au-dessus du stop", False

    # targets: the letter's first (R4: the published target is the exploitable one), then the tool's
    letter_t: list[float] = []
    for key in LETTER_TARGET_KEYS:
        t = lv.get(key)
        if t is None:
            continue
        if t > entry:
            letter_t.append(t)
        else:
            notes.append(f"objectif de la lettre {t:.4g} déjà sous l'entrée ({entry:.4g}) : non exploitable")
    if not letter_t and lv.get("resistance") is not None and lv["resistance"] > entry:
        letter_t = [lv["resistance"]]
        notes.append("résistance publiée par la lettre retenue comme premier objectif")
    if borrowed is not None:
        tool_t = list(borrowed.targets)
    elif letter_t:
        tool_t = [t for t in exploitable_targets(entry, ctx.zones, []) if t > letter_t[-1]][:2]
    else:
        extra = list(ctx.extra_targets) + ([features.high_52w] if features.high_52w else [])
        tool_t = [t for t in exploitable_targets(p_max_tech, ctx.zones, extra) if t > p_max_tech * 1.005][:3]
    if letter_t:
        obstacle = next((z for z in resistances_above(p_max_tech, ctx.zones) if z.low < letter_t[0]), None)
        if obstacle is not None:
            notes.append(
                f"obstacle avant l'objectif 1 : zone {obstacle.low:.4g}–{obstacle.high:.4g} "
                f"({obstacle.touches} contacts)"
            )
    setup = Setup(
        "external",
        kind,
        entry,
        p_max_tech,
        stop,
        "lettre" if retenu == "lettre" else (s_basis or "structure"),
        level_low,
        letter_t + tool_t,
        borrowed.horizon if borrowed is not None else "swing_2_6w",
        {
            "lettre": rec.source,
            "item_id": rec.item_id,
            "niveaux": lv,
            "mode": mode,
            "objectifs_lettre": letter_t,
            "stop_lettre": stop_lettre,
            "stop_structure": s_struct,
            "stop_retenu": retenu,
            **({"detecteur": borrowed.detector} if borrowed is not None else {}),
        },
        warnings,
    )
    return LetterSetup(
        mode,
        setup,
        stop_lettre,
        s_struct,
        s_basis,
        retenu,
        max_frac,
        letter_t,
        tool_t,
        trigger,
        origin,
        structural,
        borrowed.detector if borrowed is not None else None,
        notes,
    )


def crossed(setup: Setup, price: float) -> bool:
    """A breakout is crossed once the price is at or above its obstacle (R5 level); a pullback always is."""
    return setup.kind != SetupKind.BREAKOUT or price >= setup.level_low


def tool_view(
    price: float, f: DailyFeatures, zones: list[DetectedZone], rs_rank: float | None, trigger: float | None
) -> dict[str, Any]:
    """« Ce que voit l'outil »: stored facts and simple ratios between stored numbers, nothing else."""

    def zone(z: DetectedZone | None) -> dict[str, Any] | None:
        return None if z is None else {"bas": round(z.low, 4), "haut": round(z.high, 4), "contacts": z.touches}

    sup = supports_below(price, zones)
    res = resistances_above(price, zones)
    out: dict[str, Any] = {
        "cours": price,
        "zone_support": zone(sup[0] if sup else None),
        "zone_resistance": zone(res[0] if res else None),
        "mm50": round(f.mm50, 4) if f.mm50 else None,
        "mm200": round(f.mm200, 4) if f.mm200 else None,
        "au_dessus_mm50": bool(f.mm50 and price > f.mm50) if f.mm50 else None,
        "au_dessus_mm200": bool(f.mm200 and price > f.mm200) if f.mm200 else None,
        "mm50_sur_mm200": bool(f.mm50 > f.mm200) if f.mm50 and f.mm200 else None,
        "force_relative": rs_rank,
        "atr14": round(f.atr14, 4) if f.atr14 else None,
        "adr20": round(f.adr20, 4) if f.adr20 else None,
        "plus_haut_52s": round(f.high_52w, 4) if f.high_52w else None,
    }
    if trigger is not None and price > 0:
        out["distance_declencheur_pct"] = round(trigger / price - 1, 4)
        out["distance_declencheur_atr"] = round((trigger - price) / f.atr14, 2) if f.atr14 else None
    return out


# --- control list and verdict -------------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckItem:
    key: str  # one of CHECK_KEYS
    item: str
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"cle": self.key, "item": self.item, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class ControlFacts:
    isin: str
    sessions: int
    min_sessions: int
    price: float
    price_status: str  # realtime | delayed | eod | stale | unavailable
    price_fresh: bool  # realtime and within data.buy_intraday_max_market_age_minutes (rule 2)
    max_age_minutes: int
    level_low: float
    level_low_origin: str
    level_low_structural: bool
    target: float | None
    admission: str | None
    p_max: float | None
    min_reward_risk: float
    entry: float
    stop: float
    max_stop_distance: float
    blocked: tuple[str, ...]
    earnings_in_horizon: str | None  # date of a publication inside the horizon, if any
    shares: int | None


def checklist(c: ControlFacts) -> list[CheckItem]:
    def has(*keys: str) -> str | None:
        return next((b for b in c.blocked if any(k in b for k in keys)), None)

    out = [
        CheckItem("valeur", "valeur résolue", True, c.isin),
        CheckItem(
            "historique",
            f"historique ≥ {c.min_sessions} séances",
            c.sessions >= c.min_sessions,
            f"{c.sessions} séances",
        ),
    ]
    if c.price_status == "eod":
        cours_ok, cours = True, f"clôture officielle {c.price:.4g} : ordre pour la séance suivante"
    elif c.price_fresh:
        cours_ok, cours = True, f"temps réel {c.price:.4g}, âge de marché ≤ {c.max_age_minutes} min"
    else:
        cours_ok, cours = False, f"cours {c.price:.4g} {c.price_status} : pas de fiche d'ordre en séance (règle 2)"
    out.append(CheckItem("cours", "cours frais (statut, âge de marché)", cours_ok, cours))
    out.append(
        CheckItem(
            "structure",
            "niveau de structure enregistré",
            c.level_low_structural,
            f"{c.level_low:.4g} — {c.level_low_origin}",
        )
    )
    out.append(
        CheckItem(
            "objectif",
            "objectif exploitable",
            c.target is not None and c.target > c.entry,
            f"objectif 1 {c.target:.4g}" if c.target is not None else "aucun objectif au-dessus de l'entrée",
        )
    )
    under = has(UNDER_TRIGGER, "objectif trop proche")
    ratio_ok = c.admission in ("admis", "refuse") and under is None
    if under is not None:
        ratio = under.removeprefix("WATCH : ")
    elif c.p_max is not None:
        ratio = f"prix maximal {c.p_max:.4g} (ratio gain/risque ≥ {c.min_reward_risk:g} net de frais, R4)"
    else:
        ratio = "non calculable"
    out.append(CheckItem("ratio", f"ratio net ≥ {c.min_reward_risk:g}", ratio_ok, ratio))
    dist = (c.entry - c.stop) / c.entry if c.entry > 0 else 0.0
    out.append(
        CheckItem(
            "stop",
            "stop ≤ distance maximale",
            dist <= c.max_stop_distance + 1e-9,
            f"stop {c.stop:.4g} à {dist:.1%} sous l'entrée (maximum {c.max_stop_distance:.1%})",
        )
    )
    lost = has("configuration dégradée", INVALIDATED)
    out.append(
        CheckItem(
            "r5", "structure intacte (R5)", lost is None, lost.removeprefix("WATCH : ") if lost else "structure tenue"
        )
    )
    macro = has("dans les 30 h")
    out.append(
        CheckItem("macro", "annonce macro dans les 30 h", macro is None, macro or "aucune annonce majeure bloquante")
    )
    earn = has("publication de résultats")
    out.append(
        CheckItem(
            "resultats",
            "résultats avant l'horizon",
            earn is None and c.earnings_in_horizon is None,
            earn or (f"publication le {c.earnings_in_horizon}" if c.earnings_in_horizon else "aucune connue"),
        )
    )
    size = has("aucune taille possible")
    out.append(
        CheckItem(
            "taille",
            "taille possible",
            bool(c.shares) and size is None,
            f"{c.shares} titres" if c.shares else (size or "aucune quantité calculée"),
        )
    )
    return out


def to_verify(items: list[CheckItem]) -> bool:
    return any(not i.ok for i in items if i.key in VERIFY_KEYS)


@dataclass(frozen=True)
class VerdictFacts:
    action: str  # BUY | WATCH | NO_TRADE
    admission: str | None
    blocked: tuple[str, ...]
    verified: bool  # no failed item among VERIFY_KEYS
    last_target_passes: bool | None  # breakout under its trigger: would R4 pass with the letter's last target?


def verdict(v: VerdictFacts) -> tuple[str, str]:
    """(one of VERDICTS, the reason verbatim from the gates, « » when none)."""
    lines = list(v.blocked)

    def first(pred: Any) -> str | None:
        return next((b for b in lines if pred(b)), None)

    fx = first(lambda b: b.startswith("BLOQUÉ") and "taux de change" in b)
    if fx is not None:
        return "non_analysable", fx
    under = first(lambda b: UNDER_TRIGGER in b and b.startswith("WATCH"))
    if under is not None and not v.last_target_passes:
        return "rejet", under
    if v.admission in ("configuration_degradee", "non_testable"):
        return "rejet", first(lambda b: any(k in b for k in REJECT_MARKERS)) or (lines[0] if lines else "")
    rej = first(lambda b: any(k in b for k in REJECT_MARKERS))
    if rej is not None:
        return "rejet", rej
    blocked = first(lambda b: b.startswith("BLOQUÉ"))
    if blocked is not None:
        return "bloque", blocked
    if under is not None:
        return "alerte_declencheur", under
    other = first(lambda b: b.startswith("WATCH") and TRIGGER_WAIT not in b and WAITING_LIMIT not in b)
    if other is not None:
        return "bloque", other
    wait = first(lambda b: TRIGGER_WAIT in b)
    if wait is not None:
        return "alerte_declencheur", wait
    limit = first(lambda b: WAITING_LIMIT in b)
    if limit is not None:
        return "attendre_repli", limit
    if v.action == "BUY":
        return ("entrer_maintenant" if v.verified else "fiche_fixing"), ""
    return "bloque", lines[0] if lines else "aucune entrée retenue par les règles"
