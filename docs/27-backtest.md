# 27 — Banc de backtest au coût complet

> Proposition n° 1 de l'audit (docs/25 §25.4), confirmée par la contre-expertise quantitative du 25/10/2026. Développé en autonomie le 25/10/2026 à la demande de l'utilisateur ; **à valider** (les résultats ne changent aucune règle tant qu'un ADR ne le décide pas).

## 27.1 Objet

Passer de règles « raisonnables » à des règles **mesurées** : rejouer, séance par séance, les détecteurs de production sur l'historique stocké, au coût complet, et mesurer l'espérance nette en R, la dispersion, les sorties et les excursions (MFE / MAE) — par détecteur, statut, régime, année et période.

## 27.2 Ce qui est rejoué (identique à la production)

- détecteurs D2, D3, D4, D7 avec **leurs paramètres** de `params.yaml` ; force relative recalculée chaque séance sur la population du jour (même formule que `features_daily`) ; zones de prix recalculées les jours de signal ;
- objectif = premier objectif exploitable (jamais un multiple de R) ; admission au **prix maximal** (R4) avec les frais du compte comptant, la TTF (ISIN FR, capitalisation ≥ 1 Md€) et le change ;
- ordre **limite au prix maximal** valable `entry.expiry_sessions` séances ; stop glissé (`risk.slippage_pct`) ; stop temporel = `time_stop_sessions` du détecteur, sinon l'horizon (5 / 20 / 40 séances).

## 27.3 Hypothèses conservatrices (jamais en faveur du résultat)

- un achat limite n'est exécuté que si la séance **traverse** la limite (plus bas strictement inférieur), ou à l'ouverture si elle ouvre déjà dessous ; une ouverture sous le stop annule l'entrée ;
- stop supposé touché **après** l'exécution le jour même ; si stop et objectif sont dans la même séance, **le stop d'abord** ; gap sous le stop → sortie à l'ouverture, glissée ;
- issue inconnue (historique trop court) : **non comptée** ;
- une opération sur titres (division…) pendant la position : trade **exclu** et compté dans les notes (cours non ajustés) ;
- un seul trade à la fois par valeur et par détecteur.

## 27.4 Biais connus (affichés avec chaque résultat)

- **biais de survie** : l'univers est celui d'aujourd'hui, les sociétés radiées manquent → résultats optimistes ;
- pas de fondamentaux, de lettres ni de consensus historiques → ni bloc F, ni portail, ni score d'admission, ni objectif des analystes ;
- avant impôt, compte comptant.

## 27.5 Statistiques et usage

- par segment : nombre, R net moyen et médian, part de gagnants, **t de Student**, **intervalle de confiance à 95 % par rééchantillonnage** (graine fixe, reproductible), profit factor, durée moyenne, MFE / MAE médians, répartition des sorties ;
- **nombre d'essais** (segments testés) affiché : une règle n'est « découverte » qu'avec **t ≥ 3** (Harvey, Liu & Zhu) ; segments « apprentissage » (60 % de la période) et « contrôle » (40 %) pour juger la stabilité ;
- **amorce du classement** : tant que l'historique réel d'un détecteur est insuffisant (docs/18), l'espérance utilisée par le classement est celle du backtest en conditions de production (admis, sans avertissement), **rétrécie** n / (n + `learning.shrinkage_k`) ; affichée sur la fiche avec la mention du biais de survie ;
- aucune modification automatique d'un paramètre : un recalibrage passe par un ADR daté, puis un nouveau backtest sur une période postérieure (walk-forward).

## 27.6 Commandes et planification

- `backtest [--from AAAA-MM-JJ] [--to …] [--detectors d2_breakout,…]` : rejoue et imprime les segments « production » et « période » avec les limites ;
- job `backtest_weekly` (dimanche 10:30, 8 ans par défaut, `backtest.years`) ; tables `backtest_runs` et `backtest_trades` (migration 0018).

## 27.7 Suites prévues

Analyse MFE / MAE pour les sorties (prise partielle, stop suiveur), sensibilité de quelques paramètres avec compte des essais et Sharpe déflaté, univers point-in-time si une source des radiations devient disponible.
