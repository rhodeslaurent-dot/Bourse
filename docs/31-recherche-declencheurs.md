# 31 — Recherche de déclencheurs à espérance positive (banc pré-enregistré)

> Demande de l'utilisateur du 26/10/2026 : « analyser toutes les données récoltées et identifier, par des simulations, les déclencheurs qui isolent des actions à résultat positif par trade ; il n'y a pas de règle absolue, mais certains modèles peuvent être isolés et convertis en algorithmes exploités par le logiciel ». Plan arbitré après trois propositions de spécialistes (entrées, sorties, statistiques) ; développé le 26/10/2026. **Aucun résultat ne change un paramètre ni un détecteur sans ADR.**

## 31.1 Pourquoi un banc, et pas une réponse tout de suite

- Le backtest n° 2 (docs/27, 2018-10-26 → 2026-10-24, 825 valeurs, 8 631 trades) a montré qu'**aucun détecteur de production n'a d'espérance positive au coût complet** (d7 −0,14 R, t −2,9 ; contrôle d7 IC [−0,19 ; −0,04]). Le schéma est partout le même : ~25 % de gagnants, perdant médian arrêté vers −1,07 R, gagnant moyen +2,3 R.
- Les données de marché sont **sur votre PC** (PostgreSQL) ; l'environnement de développement n'y a pas accès. Le banc est donc un **programme** que vous lancez chez vous ; il écrit un **rapport agrégé** (statistiques seulement : aucun cours, aucun nom de valeur — les données EODHD ne doivent pas être redistribuées) qu'une seconde série d'agents analysera.
- Chercher « ce qui marche » sur 8 ans de données avec des centaines de variantes produit **toujours** des faux positifs. Le plan est donc **pré-enregistré** : cellules, seuils, hypothèses chiffrées et graine sont figés dans `research/prereg/2026-10-27_reg1.yaml` avant de voir le moindre résultat ; leur empreinte sha256 est inscrite à chaque exécution dans `data/research/ledger.csv`. Le modifier crée un **nouvel enregistrement**, jamais une retouche silencieuse.

## 31.2 Ce qui est testé

### Définitions communes (calculées à la clôture de t avec des données ≤ t ; entrée jamais avant l'ouverture de t+1)

- **Éligible** le jour t : une barre ce jour-là (pas de report), volume > 0, pas de séance figée, clôture ≥ `universe.min_price`, **médiane 60 séances des capitaux échangés ≥ `universe.min_adv_eur_pea`** (500 k€, change via `fx_rates`, table de repli du plan sinon). Liquidité point-in-time, **pas** sans biais de survie.
- Rang de force relative recalculé chaque jour **par groupe de places** (P0 / P1 / P2) parmi les seules valeurs éligibles (même formule que `features_daily`).
- Régime T13 : indice de la place (CAC 40 pour Paris, STOXX 600 ailleurs) au-dessus de sa moyenne 200 séances **et** largeur (part des éligibles au-dessus de leur MM50) ≥ `regime.breadth_green`.
- Entrée **E_open(x)** : ordre limite à `p_max = clôture_t + x · ATR14_t`, exécuté à l'ouverture de t+1 si elle est sous `p_max`, sinon à `p_max` si la séance traverse ; valable 1 séance (événements) ou 3 (momentum) ; une ouverture sous le niveau d'invalidation annule l'ordre. Les signaux non exécutés sont suivis (table de sélection adverse).
- Sortie de référence **X3(h)** : stop catastrophe à `3 · σ20 · √h` sous le prix exécuté + sortie à la clôture de la h-ième séance. Unité de mesure **R_ref = rendement net / (2 · σ20 · √h)** ; mesure jumelle **€ pour 300 € de budget de risque** (taille calculée sur le stop glissé, CLAUDE.md règle 7).

### Niveau confirmatoire (8 hypothèses, Holm à 5 %, unilatéral)

| # | Déclencheur | Sortie | Comptes |
|---|---|---|---|
| H1 | **Momentum 12-1** (rang ≥ 90e centile), le dernier jour du mois, 6 meilleurs | X3(21) | CTO, PEA |
| H2 | **Proximité du plus haut 52 semaines** (cours / plus haut ≥ 0,95, rang RS ≥ 80), premier jour après ≥ 10 séances sans | X3(21) | CTO, PEA |
| H3 | Momentum (≥ 80) **et** proximité du plus haut **et** régime T13 | X3(21) | CTO, PEA |
| H4 | **Gap de continuation** (ouverture ≥ +3 %, clôture > ouverture dans le tiers haut, volume ≥ 2 × médiane) | stop suiveur 3 ATR, 40 séances | CTO |
| H5 | **Séance de grande amplitude** (≥ 2,5 ATR, clôture dans le quart haut, volume ≥ 2) en tendance | X3(20) | CTO |
| H6 | **Repli court en tendance** (5 séances ≤ −2 σ√5) | X3(5) | CTO, SRD |
| H7 | **Choc de volume** (plus fort volume de 50 séances, ≥ 3 × médiane) | X3(20) | CTO |
| H8 | H1 limité aux valeurs **peu volatiles** (différence appariée mois par mois) | X3(21) | CTO, PEA |

### Niveau exploratoire (118 cellules d'étape 0)

| Famille | Variantes | Cellules |
|---|---|---|
| F1 momentum | momentum (seuil 80/90, h 21/63, filtre aucun / régime / basse volatilité / les deux) ; proximité du plus haut (0,90/0,95 × RS 70/80 × h × filtre) ; combinaison × filtre × h | 16 + 32 + 8 |
| F2 événements | gap (2/3/5 % × plafond 0,5/1/2 ATR × h 5/20/60) ; grande amplitude (2/2,5 ATR × volume 1,5/2 × h) ; choc de volume (× tendance × h) | 27 + 12 + 6 |
| F3 retour à la moyenne | repli de −1,5/−2/−2,5 σ√5 × h 3/5/10 | 9 |
| F5 D2 assoupli | conditions de D2 avec le plafond d'entrée 1 % (production) / 3 % / 1 ATR / E_open(0,5) × h 10/20 — **liste de surveillance au mieux** | 8 |

### Diagnostics de la chaîne de production (F0, 27 cellules, jamais candidates)

(a) décomposition du run 2 (coût en R, R perdu en stop simple / en gap, perdants détenus pendant un détachement, MAE des gagnants, MFE des perdants) ; (b) **géométrie appariée** : les trades d2/d3/d4/d7 contre 20 entrées au hasard du même jour avec les **mêmes distances au stop et à l'objectif en ATR** — si les contrôles perdent autant, la perte vient de la géométrie et des coûts, pas de l'entrée ; (c) les entrées d7 figées sous 11 variantes de sortie (stop 2/2,5/3 ATR, stop suiveur sans objectif, objectif 1,5 R / 3 R, durée 20/40, sortie conditionnelle à 5 séances, seuil de rentabilité à +1 R) ; (d) D3 inversé (retour à la moyenne) ; (e) D2 selon la distance au pivot ; (f) configurations D7/D2 écartées faute d'objectif (valeurs au plus haut) ; (g) configurations refusées par le ratio gain/risque ; (h) signaux non exécutés (gap au-dessus du prix maximal). (g) et (h) rejouent les **vrais** détecteurs (zones, admission au prix maximal) sur les seuls jours qui passent les conditions nécessaires vectorisées.

### Écarté ou reporté

Momentum résiduel, saisonnalité, mois du dividende, attention Wikipédia (enregistrement 2, après un job d'historique des consultations sur le PC), momentum sectoriel (secteurs d'aujourd'hui = regard vers le futur), entrée à la clôture du signal (regard vers le futur), entrée « sans prix plafond » (règle 7), prise de bénéfices partielle.

## 31.3 Sorties, coûts, dividendes

- **Étape 1** (seulement pour les déclencheurs qui passent la porte de l'étape 0, 4 au plus) : X1 (durée seule), X2 (stop 2σ√h), X3, X4 (stop suiveur 3 ATR, 60 séances), X5 (deux clôtures sous la MM20), X6 (sortie à 5 séances si le gain n'a pas atteint 0,5 R), X7 (stop 2,5 ATR + objectif 2 R), X8 (stop 2,5 ATR sans objectif) — **mêmes entrées figées**, ΔR apparié contre X3.
- Règles de parcours d'une séance où stop et objectif sont tous deux touchés : **stop d'abord** (référence, prudente), heuristique OHLC, objectif d'abord (borne optimiste) ; une variante dont le signe change selon la règle est « dominée par l'ambiguïté » et rejetée.
- **Coûts** : CTO Saxo (courtage `brokers.saxo`, TTF sur les achats d'émetteurs français ≥ seuil — capitalisation d'aujourd'hui —, taxes étrangères, change) + **demi-écart acheteur-vendeur** de Corwin–Schultz par valeur et par mois (mois précédent, plancher `risk.slippage_pct`) aux deux jambes ; scénario PEA (grille BoursoBank de votre formule) pour h ≥ 20 ; scénario SRD (+ `srd.crd_daily_rate`) pour h ≤ 10 ; stress coûts × 1,5 et glissement × 2.
- **Dividendes** : déduits du facteur d'ajustement `adj_close / close` (un saut ≥ 20 % = opération sur titres → trade exclu). Stop et objectif abaissés du montant ; trois variantes du crédit : brut, net de PFU (CTO), exclu ; « fragile » si le signe change. Années au rendement implicite > 12 % ignorées (facteurs douteux, p. ex. Milan via Yahoo).
- **Survie** : multiple des taux de faillite supposés (3 %/an petites capitalisations, 0,5 %/an grandes ; OPA +25 % au même taux) qui ramène l'espérance à zéro.

## 31.4 Protocole et critères

- **Découverte** : signaux du 02/01/2017 au 31/07/2024 ; **embargo** 01/08 → 31/10/2024 ; **période scellée** du 01/11/2024 à la fin des données − 65 séances (« semi-propre » : le run 2 y a vu d2–d7), ouverte **une seule fois** pour un fichier de candidats figé ; **suivi en ombre** à partir du 27/10/2026.
- Walk-forward ancré : années de test 2020, 2021, 2022, 2023, 2024 (janvier–juillet), purge de 65 séances.
- Inférence : séries hebdomadaires (semaine du signal), t de Newey–West, erreurs groupées par mois et mois × valeur, intervalles par rééchantillonnage par blocs de 4 semaines (2 000 tirages, graine 20261027) ; **contrôle apparié** (même jour, même groupe de places, même tercile de liquidité, même tercile de volatilité, même état de tendance, mêmes entrée et sortie) ; références imbriquées univers → tendance → déclencheur.
- Tests multiples : Holm sur les 8 confirmatoires ; **Romano–Wolf** (contraignant) + plancher t ≥ 3 sur l'exploratoire ; Holm sur l'ensemble et BH (liste de surveillance) rapportés ; permutation par décalage de dates (top 30) ; PBO (CSCV, 16 blocs) par famille ; Sharpe déflaté. **Puissance** : un avantage de +0,10 / +0,20 R_ref planté dans des trades de contrôle ; puissance < 50 % → « test non concluant » au lieu de « pas d'avantage ».
- **Porte** de l'étape 0 → 1 : t hebdomadaire ≥ 2,0 **et** excès sur le contrôle > 0.
- **A1–A13** (`thresholds` du plan) : taille, effet (≥ +0,10 R et ≥ +30 € pour 300 € de risque hors échantillon, profit factor ≥ 1,15), significativité, placebo (excès ≥ +0,08 R, IC > 0, permutation ≤ 0,01), ≥ 4 plis sur 5 positifs, coûts stressés, régime, dividendes, survie (multiple ≥ 2, univers point-in-time), concentration (10 valeurs < 40 % du résultat), capacité (≤ 2 % du volume), déployabilité, ambiguïté et portefeuille (6 lignes, recul max ≤ celui des contrôles).
- Niveaux : **CANDIDAT** (A1–A13, 5 au plus, figés pour le descellement) ; **SURVEILLANCE** (A1, A2, A4, A5, A6 et q BH ≤ 0,10 → suivi en ombre seulement) ; **REJET**. Un candidat confirmé sur la période scellée n'entre en production que par **ADR**, à 50 % de `risk.max_risk_per_trade_pct`, avec règle de suspension. Détecteurs existants : borne haute de l'IC hors échantillon < 0 → ADR de désactivation ou refonte (le rapport l'imprime).
- « Rien trouvé » est un résultat : titre **AUCUN DÉCLENCHEUR RETENU**, aucun seuil n'est relâché après coup, la période scellée n'est pas réutilisée.

## 31.5 Lancer le banc sur le PC

Dans l'ordre (le conteneur `app` tourne) :

```
docker compose exec app python -m app.cli research calibrate --run 2
docker compose exec app python -m app.cli research decompose --run 2
docker compose exec app python -m app.cli research run --universe all
```

- `calibrate` rejoue chaque trade du run 2 avec le moteur vectorisé (conventions du run 2) : **VALIDE** si ≥ 99 % des trades ont le même jour et le même motif de sortie et si le R brut moyen de chaque détecteur est à ±0,02 R ; sinon **INVALIDE** (inscrit dans l'en-tête de tout rapport suivant — ne pas exploiter un rapport INVALIDE). Code de sortie 1 si INVALIDE.
- `decompose` : diagnostic (a) seul, rapide.
- `run` : l'enregistrement complet. Options : `--universe in_universe|all|pit` (`all` = toutes les actions avec la liquidité point-in-time, recommandé ; `pit` = synonyme), `--from 2016-01-04 --to AAAA-MM-JJ`, `--max-instruments 100` (essai rapide, < 1 min), `--stage 0|1|2|all`, `--run N` (backtest de référence des diagnostics F0, défaut : le dernier), `--max-f0-candidates 100000` (échantillon tiré au sort des jours rejoués par les vrais détecteurs pour F0 g/h), `--no-cache`, `--out data/research`. `--seed` n'accepte que la graine du plan ; `--workers` est réservé (calcul vectorisé dans un seul processus).
- **Durée estimée** : 10 à 20 min pour l'univers actuel (825 valeurs), 15 à 30 min pour `--universe all` (≈ 3 500 instruments dont les jamais liquides sont écartés d'emblée) ; **mémoire** ≈ 2 à 4 Go (un groupe de places à la fois). La première lecture des cours est mise en cache (`data/research/cache/*.npz`, invalidé quand le nombre de lignes de `prices_eod` change).
- `research unseal --candidates data/research/<horodatage>_candidates.yaml` : **une seule fois** par plan, uniquement avec le fichier de candidats figé écrit par `run` ; refusé sinon.
- Refus (code 2) : même plan déjà exécuté avec un autre ensemble de cellules ; descellement sans fichier ou déjà fait ; graine différente.

## 31.6 Lire le rapport

Fichiers : `data/research/<AAAAMMJJ_HHMMSS>.json` (complet) et `.txt` (lecture en français). **Partager ces deux fichiers seulement** (et `calibration_*.json`, `decomposition_*.json`) : ils ne contiennent aucun cours ni aucun nom de valeur (contrôle `assert_no_price_fields` à l'écriture) ; une cellule de moins de 20 trades est masquée.

- **En-tête** : bornes des données, univers, instruments par groupe, instruments inactifs ou sans cours depuis 30 jours, verdict de calibration, empreinte du plan, cellules enregistrées / exécutées, effet minimal détectable et puissance par famille, hypothèses (marquées), paramètres utilisés (lus dans `params.yaml`), années de dividendes écartées, limites.
- **A — Résumé** : le titre (« AUCUN DÉCLENCHEUR RETENU » ou le nombre de candidats), candidats, liste de surveillance, verdict par famille (« pas d'avantage détecté » / « test non concluant (puissance insuffisante) »), les 8 hypothèses confirmatoires.
- **B** — diagnostics du run 2 et cellules F0 ; ligne « → ADR désactivation ou refonte » pour un détecteur dont l'intervalle hors échantillon est entièrement négatif.
- **C** — étape 0 par famille : n, R moyen, excès sur le contrôle, t hebdomadaire, p Romano–Wolf, q BH, porte.
- **D** — contrôles appariés, références imbriquées (univers → tendance), sélection adverse (excès futur des signaux non exécutés), permutation, puissance.
- **E** — étape 1 : ΔR et Δ€ appariés par sortie, « retenue » ou « rejetée ».
- **F** — robustesse : par groupe de places, tercile de liquidité, taille, année, plis, règles de parcours, erreurs groupées, PBO, Sharpe déflaté.
- **G** — coûts et comptes : R brut, coût en R, stress, multiplicateur de coûts d'équilibre, PEA (étiquette PEA seulement si A2 tient au coût BoursoBank), SRD.
- **H** — survie, dividendes (brut / net / exclu, fragilité), capacité, concentration.
- **I** — classement A1–A13 de chaque cellule ; **J** — rejeu en portefeuille (6 lignes) contre les contrôles ; **K** — limites.

Repères : un R moyen positif **avant** le contrôle peut n'être que la hausse du marché ; c'est l'**excès sur le contrôle** et sa significativité corrigée (Romano–Wolf) qui isolent un modèle. Un « test non concluant » dit seulement que les données ne suffisent pas à trancher.

## 31.7 Implémentation

- `research/prereg/2026-10-27_reg1.yaml` — le plan comme donnée (cellules, seuils, hypothèses, graine).
- `app/domain/research/` (pur, typé, numpy) : `panel` (panneaux par groupe de places, séances propres de chaque valeur, indicateurs vectorisés), `triggers`, `entries` (ordres, verrouillage, contrôles appariés, excès futurs), `exits` (moteur de sortie vectorisé, **identique** à `simulate_trade` en conventions du run 2 — test sur 500 crochets aléatoires), `costs`, `stats`, `prereg`, `pipeline`, `registration`, `f0`, `classify`, `report`.
- `app/services/research.py` (base → panneaux en cache, registre, fichiers) ; commande `research` dans `app/cli.py`.
- Tests : `tests/unit/test_research.py` (avantage planté retrouvé ; marche au hasard sans candidat ; futur empoisonné sans effet sur le passé ; équivalence du moteur ; indicateurs vectorisés = scalaires ; dividendes ; coûts ; statistiques — Romano–Wolf garde le taux d'erreur ≤ 7 % sur 300 simulations nulles ; plan ; rapport ; classement) et `tests/integration/test_research.py` (base synthétique : calibration VALIDE, décomposition, enregistrement, refus).
- Aucun paramètre ajouté à `config/params.yaml` : les hypothèses de recherche vivent dans le plan pré-enregistré.

## 31.8 Écarts au plan arbitré (signalés, règle 8)

| Écart | Raison |
|---|---|
| **195 cellules enregistrées**, non 203 : le tableau §1.3 annonçait 126 cellules d'étape 0, ses lignes en font **118** (16 + 32 + 8 + 27 + 12 + 6 + 9 + 8) ; 195 = le total du §0 du plan | erreur d'addition du plan ; aucune cellule retirée |
| Contrôle apparié = **moyenne de toute la strate** du jour (instrument exclu) au lieu de 20 tirages | limite exacte des 20 tirages, sans bruit d'échantillonnage ; une seule simulation du panneau éligible par combinaison entrée/sortie |
| Stops posés sur le **prix exécuté** (`exp(−k σ √h)` pour les stops en σ) ; le niveau d'invalidation de l'ordre est la même formule appliquée à `p_max` | règle 7 (recalcul au prix exécuté) ; la forme exponentielle garde un stop positif aux longs horizons |
| E_open(1) pour T01 / T03 / C1 | non précisé par le plan ; marqué `assumption: true` |
| Verrouillage fixe de h séances par valeur (signal exécuté ou non) ; pour les déclencheurs mensuels, appliqué à la sélection des 6 | aucune dépendance au résultat |
| Variantes de dividendes : stop et objectif abaissés dans les trois, seul le crédit change | une simulation par cellule ; l'écart de chemin est marginal |
| Calibration : rejeu de chaque trade stocké **depuis son exécution** (limite = prix exécuté) ; comparaison jour et motif de sortie, prix d'entrée, R **brut** | le run 2 ne stocke pas le prix maximal ; le R net diffère par construction (demi-écart Corwin–Schultz) |
| Romano–Wolf **studentisé dans chaque rééchantillon** (Newey–West tolérant les semaines vides) | la version simple dépassait 7 % d'erreur sur données nulles ; celle-ci tient ≈ 5,6 % |
| Référence du rejeu en portefeuille (A13) = les **résultats des contrôles appariés aux mêmes dates** | même calendrier, même nombre de lignes |
| Puissance : succès si t ≥ 3 et excès ≥ +0,08 R sur les pseudo-trades ; décision à +0,20 R | Romano–Wolf à chaque réplication serait trop long |
| F0 (g)/(h) : au plus `--max-f0-candidates` jours (tirés au sort, graine fixe) | durée (≈ 2 ms par jour candidat en Python) |
| T09 : médiane des 50 séances **précédentes** ; RVOL = volume / médiane des 20 précédentes | le jour t n'entre pas dans sa propre référence |
| H8 : pas de permutation (série appariée) → A4 non évaluable, H8 ne peut pas devenir candidat | test d'apport d'un filtre, pas un déclencheur |
| Tests : marche au hasard sur 3 graines × 70 valeurs × 2 300 séances (et non 20 × 300 × 1 500), rééchantillonnages réduits | durée de la suite de tests ; le banc réel garde les valeurs du plan |
| `numpy>=2.0` déclaré dans `pyproject.toml` | déjà installé (dépendance de pandas, même version dans `uv.lock`) ; aucune nouvelle dépendance |

## 31.9 Limites (rappelées dans chaque rapport)

Survie : la table des instruments vient des listes d'aujourd'hui (le rapport compte les inactifs) ; capitalisations, TTF et secteurs d'aujourd'hui ; période scellée « semi-propre » ; rang de force relative recalculé autrement qu'en production ; avant impôt ; aucun résultat ne modifie un paramètre — toute mise en production passe par un ADR, à demi-risque, en ombre d'abord.
