"""docs/37 lot L0 — graduated verdict (pure domain): reasons classified in a closed list, the level of a card, D12
(last target), D15 (the plan n° 4 P2 order for a d7 card), the texts a speculative card shows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.domain.admission import Setup as SetupKind
from app.domain.admission import Status
from app.domain.entry_policies import PolicyParams, SetupView, plan
from app.domain.reevaluation import ReevalInput, ReevalStatus, classify
from app.domain.verdict import (
    Category,
    Edge,
    Level,
    PlageP2,
    VerdictFacts,
    VerdictParams,
    classify_reason,
    edge_text,
    grade,
    last_target_admits,
    plage_admission,
    r4_first_target_only,
    size_label,
    speculative_plage,
    split_appreciation,
)

VP = VerdictParams(
    enabled=True,
    speculative_detectors=frozenset({"d2_breakout", "d7_trend_quality"}),
    speculative_size_multiplier=0.5,
    speculative_last_target=True,
    speculative_d7_entry="plage",
    speculative_stop_atr=2.5,
    plage_cap_atr=0.5,
    plage_validity_sessions=3,
    time_exit_sessions=20,
    notify_speculative=False,
)
CAP = "BLOQUÉ : risque ouvert après entrée 1200 € > plafond 900 € (risk.max_open_risk_pct_by_gate)"
R14 = "BLOQUÉ : USD Federal Funds Rate dans les 30 h — perte au 75e centile 2,1 R ; gel de l'entrée (R14)"
WAIT = "WATCH : refusé — P_max 98.2 dépassé de 1.10% — limite posée en attente d'un repli"
SCORE = "WATCH : ratio d'admission 0.62 < 0.75 (scoring)"
ADR13 = (
    "WATCH : espérance historique négative au coût complet (backtest n° 2, docs/27) — surveillance seulement (ADR-013)"
)


@pytest.mark.parametrize(
    ("reason", "code", "category"),
    [
        (CAP, "plafond", Category.ABSOLUTE),
        ("BLOQUÉ : grappe corrélée (2 ligne(s) ρ ≥ 0.6) — risque 640 € > 1.5 R", "plafond", Category.ABSOLUTE),
        ("BLOQUÉ : portail restreint + (risque conjoncturel élevé ou régime rouge)", "portail_restreint_plus",
         Category.ABSOLUTE),
        ("BLOQUÉ : coupe-circuit : −4.2 R sur la semaine ≤ −3 R", "coupe_circuit", Category.ABSOLUTE),
        ("WATCH : coupe-circuit indisponible (mesure en échec)", "coupe_circuit", Category.ABSOLUTE),
        ("BLOQUÉ : taux de change GBP indisponible (fx_refresh)", "change", Category.ABSOLUTE),
        (R14, "r14", Category.ABSOLUTE),
        ("BLOQUÉ : publication de résultats le 04/11 — un stop ne protège pas d'un gap ; x (R14)", "r14",
         Category.ABSOLUTE),
        ("configuration dégradée : cours 95 au niveau du stop 96 ou dessous — invalidation atteinte (R5)",
         "stop_impossible", Category.ABSOLUTE),
        ("WATCH : aucune taille possible sur les comptes (budget de risque, frais)", "taille_nulle",
         Category.ABSOLUTE),
        ("WATCH : risque élevé — aucune quantité possible à la taille × 0.5", "taille_nulle", Category.ABSOLUTE),
        ("BLOQUÉ : emballement non corroboré — attention et volume anormaux", "emballement", Category.ABSOLUTE),
        (ADR13, "surveillance_seule", Category.ABSOLUTE),
        (WAIT, "limite_en_attente", Category.NO_ORDER),
        ("WATCH : ratio net < 2 au déclencheur (gain 1 / risque 1 = 1.00) — aucune limite en attente (R4)",
         "ratio_au_declencheur", Category.NO_ORDER),
        ("WATCH : objectif trop proche — le prix maximal (95) tombe au niveau du stop (96) ou dessous",
         "objectif_trop_proche", Category.NO_ORDER),
        ("WATCH : cours au-dessus de la limite de la plage — cours 101 > limite 100.2", "au_dessus_de_la_plage",
         Category.NO_ORDER),
        ("WATCH : déclencheur non franchi — cours 99 sous 100", "declencheur_non_franchi", Category.NO_ORDER),
        ("WATCH : pas d'objectif exploitable (aucune résistance)", "sans_objectif", Category.NO_ORDER),
        ("WATCH : non analysable — valeur absente du référentiel", "non_analysable", Category.NO_ORDER),
        (SCORE, "score", Category.APPRECIATION),
        ("WATCH : confiance 2/5 < 3", "confiance", Category.APPRECIATION),
        ("WATCH : risque élevé (distance du stop, liquidité, petite capitalisation, devise)", "risque_eleve",
         Category.APPRECIATION),
        ("WATCH : stop trop large (6.1% sous l'entrée)", "risque_eleve", Category.APPRECIATION),
        ("WATCH : portail restreint — ratio net de frais 1,90 (≥ 2,5 exigé) et score 61,00 (≥ 75 exigé) (R10)",
         "portail_restreint", Category.APPRECIATION),
        ("WATCH : hausse médiatique +4.0% depuis la citation", "hausse_mediatique", Category.APPRECIATION),
        ("BLOQUÉ : historique défavorable (64 cas comparables, espérance rétrécie −0.21 R) → WATCH seulement",
         "historique_defavorable", Category.APPRECIATION),
        ("BLOQUÉ : drift depuis l'ouverture +3.0% ≥ 2% — aucun BUY sur signal externe seul", "entree_tardive",
         Category.APPRECIATION),
        ("BLOQUÉ : règle inconnue", "autre_blocage", Category.ABSOLUTE),  # fails safe
        ("WATCH : condition inconnue", "autre_attente", Category.NO_ORDER),  # fails safe
    ],
)  # fmt: skip
def test_every_engine_reason_has_one_category(reason, code, category):
    j = classify_reason(reason)
    assert (j.code, j.category) == (code, category)
    assert not j.text.startswith(("BLOQUÉ : ", "WATCH : "))


def test_a_reason_listed_among_the_caps_is_a_cap():
    assert classify_reason("BLOQUÉ : quelque chose", ("BLOQUÉ : quelque chose",)).code == "plafond"


def _facts(**kw) -> VerdictFacts:
    base = {"action": "BUY", "blocked": (), "admitted": True, "gate": "restreint"}
    return VerdictFacts(**{**base, **kw})


def test_today_buy_is_a_considerer_and_a_measured_edge_in_an_open_gate_is_conviction():
    v = grade(_facts(), VP)
    assert v.level == Level.A_CONSIDERER and v.size_multiplier == 1.0 and v.orderable
    assert "non mesuré" in v.main
    assert grade(_facts(edge_positive=True, gate="ouvert"), VP).level == Level.CONVICTION_FORTE
    assert grade(_facts(edge_positive=True, gate="restreint"), VP).level == Level.A_CONSIDERER
    assert grade(_facts(edge_positive=False, gate="ouvert"), VP).level == Level.A_CONSIDERER  # today: none


def test_only_appreciation_is_speculative_at_the_reduced_size():
    v = grade(_facts(action="WATCH", blocked=(SCORE, "WATCH : confiance 2/5 < 3")), VP)
    assert v.level == Level.SPECULATIVE and v.size_multiplier == 0.5
    assert v.appreciation == ("ratio d'admission 0.62 < 0.75 (scoring)", "confiance 2/5 < 3")
    assert v.codes == ("score", "confiance")
    # a speculative detector without any reason is speculative too
    assert grade(_facts(speculative=True), VP).level == Level.SPECULATIVE
    # a stored speculative BUY: its elements of appreciation, without their prefix, keep their codes
    stored = grade(_facts(speculative=True, appreciation=("confiance 2/5 < 3", "texte libre")), VP)
    assert stored.level == Level.SPECULATIVE and stored.codes == ("confiance", "appreciation")


def test_absolute_rules_win_over_everything():
    for b in (CAP, R14):
        v = grade(_facts(blocked=(SCORE, b), speculative=True), VP)
        assert v.level == Level.ECARTEE and v.size_multiplier == 0.0 and not v.orderable
    assert grade(_facts(action="NO_TRADE"), VP).level == Level.ECARTEE
    assert grade(_facts(price_status="unavailable"), VP).level == Level.ECARTEE
    assert grade(_facts(blocked=(ADR13,)), VP).level == Level.ECARTEE
    # ADR-013 lifted (a speculative detector under the scale): judged on its other reasons
    lifted = grade(_facts(blocked=(ADR13, SCORE), speculative=True, lifted=("surveillance_seule",)), VP)
    assert lifted.level == Level.SPECULATIVE


def test_no_order_today_is_a_surveiller_and_rule_2_keeps_its_base():
    assert grade(_facts(action="WATCH", blocked=(WAIT, SCORE), admitted=False), VP).level == Level.A_SURVEILLER
    v = grade(_facts(speculative=True, to_verify=True, price_status="stale"), VP)
    assert v.level == Level.A_SURVEILLER and v.base == Level.SPECULATIVE and v.size_multiplier == 0.5
    assert v.main.startswith("à vérifier")
    assert grade(_facts(action="WATCH", blocked=(SCORE,), admitted=False), VP).level == Level.A_SURVEILLER


def test_r4_first_target_only_and_split():
    assert r4_first_target_only((WAIT, SCORE))
    assert not r4_first_target_only((WAIT, CAP))
    assert not r4_first_target_only((WAIT, "WATCH : déclencheur non franchi — cours 99"))
    assert not r4_first_target_only((SCORE,))
    keep, appr = split_appreciation([SCORE, CAP])
    assert keep == [CAP] and appr == ["ratio d'admission 0.62 < 0.75 (scoring)"]


def test_last_target_admits():
    # R4 on target 1 = 104: (104 + 2·96)/3 = 98.67 < price 100 ; on the last target 115: 102 ≥ 100
    assert last_target_admits(100.0, 100.0, SetupKind.PULLBACK, 115.0, 96.0, 0.0, 2.0, None)
    assert not last_target_admits(100.0, 100.0, SetupKind.PULLBACK, 104.0, 96.0, 0.0, 2.0, None)
    assert not last_target_admits(100.0, 100.0, SetupKind.PULLBACK, 115.0, 96.0, 0.0, 2.0, 99.5)  # technical cap
    # a breakout under its seuil is tested at the seuil
    assert not last_target_admits(99.0, 103.0, SetupKind.BREAKOUT, 115.0, 96.0, 0.0, 2.0, None)


def _pp() -> PolicyParams:
    return PolicyParams(0.002, 2.0, 3, confirm_sessions=VP.plage_validity_sessions, confirm_max_atr=VP.plage_cap_atr)


def test_d15_plage_is_the_plan_4_policy_p2():
    from app.domain.research.entry_policies import tick_size as research_tick

    close, high, atr, trig, stop, targets = 108.10, 108.54, 1.90, 108.757, 105.17, (115.16,)
    pl = speculative_plage(close, high, atr, trig, stop, targets, _pp(), 2.5, 20)
    assert isinstance(pl, PlageP2)
    order = plan("P2", SetupView(close, high, atr, None, None, trig, trig, stop, targets, 0.0), _pp())
    assert not isinstance(order, str)
    assert (pl.seuil, pl.limite, pl.validite_seances) == (order.level, order.max_price, order.valid)
    # the research's vectorised definition (policy_orders, P2): level = max(trigger, high) + 1 tick, cap 0.5 ATR
    base = np.array([max(trig, high)])
    lv = float((base + research_tick(base))[0])
    assert pl.seuil == pytest.approx(lv) and pl.limite == pytest.approx(lv * (1 + 0.5 * atr / close))
    assert pl.stop == pytest.approx(pl.seuil - 2.5 * atr) and pl.stop_structurel == stop
    assert pl.sortie_seances == 20 and pl.validite_seances == 3 and pl.cap_atr == 0.5
    # no order when the seuil reaches the first target, nor without ATR
    assert speculative_plage(close, high, atr, trig, stop, (108.70,), _pp(), 2.5, 20) == (
        "niveau d'entrée au-dessus de l'objectif"
    )
    assert speculative_plage(close, high, None, trig, stop, targets, _pp(), 2.5, 20) == "ATR inconnue"


def test_plage_admission_has_no_r4_but_never_chases_the_price():
    adm = plage_admission(108.76, 115.16, 104.0, SetupKind.PULLBACK, 105.6, 109.7)
    assert adm.status == Status.ADMITTED and adm.p_max == 109.7  # ratio (115.16−108.76)/(108.76−104) < 2: no gate
    assert plage_admission(110.0, 115.16, 104.0, SetupKind.PULLBACK, 105.6, 109.7).status == Status.REFUSED
    deg = plage_admission(105.0, 115.16, 104.0, SetupKind.PULLBACK, 105.6, 109.7)
    assert deg.status == Status.DEGRADED and "support perdu" in deg.reasons[0]
    assert plage_admission(108.0, None, 104.0, SetupKind.PULLBACK, 105.6, 109.7).status == Status.NOT_TESTABLE


def test_texts_of_a_speculative_card():
    assert edge_text(None) == "Spéculative — espérance historique non mesurée"
    e = Edge(-0.137, 2716, "backtest, entrée et sorties de production", -0.23, -0.05)
    txt = edge_text(e, plage=True)
    assert "−0,14 R au coût complet" in txt and "n 2716" in txt and "IC 95 % [−0,23 ; −0,05]" in txt
    assert "plans n° 4 et 6" in txt
    assert (size_label(0.5), size_label(0.25), size_label(0.75), size_label(0.6)) == ("½", "¼", "¾", "× 0,6")


def test_intraday_reeval_of_a_plage_p2_keeps_its_own_limit():
    now = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
    inp = dict(price=109.2, data_status="realtime", market_timestamp=now - timedelta(minutes=1), now=now,
               target=115.16, stop=104.0, setup=SetupKind.BREAKOUT, level_low=105.6, fee_fraction=0.004,
               min_reward_risk=2.0, p_max=109.7, trigger=108.76, max_market_age_minutes=5)  # fmt: skip
    # with R4 the target 115.16 and the stop 104 cap the limit at ≈ 107.3: « limite dépassée »
    assert classify(ReevalInput(**inp)).status == ReevalStatus.ABOVE_PMAX
    r = classify(ReevalInput(**inp, r4=False))
    assert r.status == ReevalStatus.TRIGGER_CROSSED and r.actionable
    assert classify(ReevalInput(**{**inp, "price": 109.9}, r4=False)).status == ReevalStatus.ABOVE_PMAX
    assert classify(ReevalInput(**{**inp, "price": 108.5}, r4=False)).status == ReevalStatus.WAITING
