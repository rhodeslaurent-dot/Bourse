# 33 — Recherche de déclencheurs : tour 2 (analyse du 27/10/2026)

> Suite de docs/31. L'enregistrement n° 1 a été exécuté sur le PC le 26/10/2026 : calibration VALIDE (concordance 1,0), deux exécutions, aux taux BCE statiques puis réels, aux résultats quasi identiques. Titre du rapport : **AUCUN DÉCLENCHEUR RETENU**. Le tour 2 est l'analyse multi-agents du rapport demandée par l'utilisateur :
> - trois spécialistes : entrées et exécution, sorties et coûts, statistique et protocole ;
> - une critique croisée ;
> - un arbitrage ;
> - un contrôle par un gardien du protocole, dont les corrections sont intégrées ici.
>
> Chaque chiffre cité vient du rapport (`research explain`). Un ordre de grandeur non mesuré par le banc est signalé comme tel.

## 33.1 Ce que dit le banc, en clair

1. **Rien d'exploitable dans les familles testées.** Le banc avait une puissance de 98 à 100 % pour détecter un avantage de +0,10 R dans les familles F1 (momentum), F2 (court terme) et F3 (replis). L'effet minimal détectable est d'environ 0,05 R. Aucune règle de ces familles n'atteint le seuil économique. Les 8 hypothèses confirmatoires sont rejetées (Holm).
2. **Un excès statistiquement détecté, mais trop petit.**
   - Règle : proximité du plus haut sur 52 semaines + force relative ≥ 80 + régime favorable, détention 21 séances.
   - Avance sur des valeurs comparables : **+0,045 R**, IC [0,021 ; 0,076], n 1 602, Romano–Wolf p 0,030.
   - Gain net de seulement **+0,023 R** (4,8 € pour 300 € risqués), qui devient négatif avec des frais stressés (−0,028).
   - Ce gain est peut-être gonflé par le biais de survie : +0,041 R sur les valeurs encore dans l'univers (n 1 271), −0,046 R sur celles qui en sont sorties (n 331).
   - Verdict : statistiquement détecté, à répliquer.
3. **La règle la plus rentable sur le papier**, et la seule qui reste nettement positive avec des frais stressés.
   - Règle : momentum (rang de force relative ≥ 80), régime favorable, détention **63 séances**.
   - Résultat : +0,111 R, +22,9 € pour 300 €, PF 1,42, n 271. Frais 0,052 R, stressé +0,070, multiplicateur de coûts d'équilibre 3,15.
   - Elle a été remarquée parmi 195 cellules. Son avance sur les contrôles appariés n'est pas établie (+0,072, IC [−0,041 ; 0,195]). Dix valeurs portent 95 % du résultat. Elle n'a que 54 mois de signaux, sous les 60 exigés (A1 mensuel).
   - D'autres variantes proches sont faiblement positives (R +0,07 à +0,13, n 220 à 276). **Aucune n'est significative.**
4. **Choisir chaque année « la meilleure règle » perd de l'argent** : walk-forward de la famille F1, n 114, R −0,023.
5. **La table « ordres exécutés / non exécutés » est trompeuse par construction.**
   - Un ordre « non exécuté » est, par définition, un ordre dont le plus bas n'a jamais touché la limite : il est sélectionné sur son avenir.
   - L'écart entre les deux groupes (+0,06 / +0,14 ; +0,11 / +1,19) n'est donc pas un gain manqué, et ne justifie pas d'élargir le prix maximal d'entrée.
   - Le rapport imprime désormais l'effectif de chaque groupe et la moyenne sur **tous** les signaux, qui sert de référence non conditionnée.
6. **Ce qui pèse vraiment : les frais exprimés en R.**
   - d7 : brut +0,041, frais 0,178, net −0,137 (backtest n° 2, n 2 716).
   - Les détentions courtes (5 séances) battent le hasard mais restent perdantes une fois les frais payés : R −0,03 à −0,09, frais 0,06 à 0,11 R.

## 33.2 Décisions

| # | Décision | Nature | À valider |
|---|---|---|---|
| 1 | Désactiver d3 et d4 (règle pré-enregistrée atteinte) | ADR-014 | **oui** |
| 2 | d7 reste en surveillance seulement, sans changer ses sorties ; règle de désactivation pré-déclarée pour le verdict de l'enregistrement n° 2 | ADR-014 | **oui** |
| 3 | Aucun paramètre d'entrée ou de sortie du moteur ne change ; aucune cellule ne passe en SURVEILLANCE ; aucun seuil n'est abaissé | — | non |
| 4 | Période scellée européenne non réutilisée | ADR-014 | **oui** |
| 5 | Validité des propositions comptée en **séances de la place** (aujourd'hui : `now + (expiry_sessions + 2)` jours calendaires, `engine.py`) — divergence consignée dans docs/15 | moteur | **oui**, selon ta pratique (question 2) |
| 6 | Suivi « fantôme » des propositions non exécutées : stocké sans affichage (première ouverture, excès sur l'horizon, gap d'ouverture en ATR), analysé seulement dans un enregistrement n° 3 pré-déclaré | moteur | **oui** |
| 7 | Demi-écart acheteur-vendeur dans les coûts du moteur : différé, d'abord des relevés bid/ask Saxo, puis un ADR (touche les frais estimés de la règle 7) | moteur | plus tard |
| 8 | Mention « sorties non validées par la recherche (docs/31) » à côté des objectifs des propositions | interface | **oui** |
| 9 | Corrections de méthode du banc (faites le 27/10, voir §33.4) | banc | non |
| 10 | Enregistrement n° 2 (§33.3) : nécessite des données de Londres, Zurich et New York **avec les valeurs radiées** | données | **oui** |

## 33.3 Enregistrement n° 2 (projet)

**Principe**
- Nouveau fichier `research/prereg/<date>_reg2.yaml`, avec une nouvelle graine et une nouvelle ligne au registre.
- Rien n'est choisi sur la découverte du n° 1. Ni la découverte ni la période scellée européenne ne compte dans un verdict.
- Toute relecture de données de découverte porte une pénalité DSR avec N = 195 (toutes les cellules du plan n° 1).

**Données de test**
- Londres, Zurich et New York, signaux du 02/01/2017 à la fin des données moins 65 séances.
- Univers point-in-time **avec valeurs radiées**. C'est une condition bloquante du statut confirmatoire : sans elle, C1 et C2 deviennent exploratoires (« réplication géographique avec biais de survie »).
- Chaque place forme un groupe de rang. Les blocs du bootstrap et de Romano–Wolf (13 semaines) couvrent les trois places ensemble, car le momentum est corrélé entre places.
- **Compte CTO seulement** : le PEA ne peut pas détenir ces titres. Coûts Saxo : courtage, change `fx_fee_pct` par sens, `fx_haircut`, droit de timbre GB 0,5 %.
- Un suivi en ombre prospectif européen démarre dès l'acceptation : signaux stockés chaque jour, non affichés avant le verdict. Il sert à la règle de suspension, pas au verdict.

**Critères**
- A1 à A13 recopiés à l'identique du plan n° 1, **A1 en entier** : événementiel n ≥ 300, ≥ 100 semaines, ≥ 40 trades par pli ; mensuel ≥ 60 mois et ≥ 200 trades par emplacement.
- Puissance : la méthode du plan n° 1 (avantage planté de +0,10 et +0,20 R) ; la puissance sous un effet rétréci de moitié est imprimée à titre d'information.
- Modifications déclarées avant toute exécution, indépendantes des résultats :
  - Holm sur 2 (nombre de confirmatoires) ;
  - point et IC sur la même quantité ;
  - verdicts brut et net séparés pour les places hors euro ;
  - pas d'horizon ≤ 10 séances ni de scénario SRD. C'est une réduction de grille informée par le tour 1, déclarée comme telle.

**Cellules**

| Id | Statut | Définition |
|---|---|---|
| C1 | confirmatoire | Cellule figée à l'identique du n° 1 : momentum, rang ≥ 80, mensuel, détention 63 séances, porte de régime t13 + t11, sortie X3(63), entrée E_open(1,0 ATR), validité 3 séances. CTO. |
| C2 | confirmatoire | C1 sans porte de régime. Garde-fou contre un effet de la seule porte, choisie après coup au n° 1. |
| X1 | exploratoire, **exécuté d'abord** | Placebo : valeurs et dates tirées au hasard aux mêmes fréquences, même chaîne. Attendu : excès ≈ 0, IC contenant 0. Un biais trouvé est corrigé par ADR **avant** de lire C1 et C2. |
| X2 | exploratoire | Réplication figée de la cellule « plus haut 52 semaines » (§33.1, point 2). |
| X3 | exploratoire | Diagnostic d'entrée non conditionné : excès sur tous les signaux, part des non exécutés, gap d'ouverture ; E_open(2,0) contre E_open(1,0), apparié par signal. |
| X4 | exploratoire | Attendre un repli ? Entrée immédiate contre limite à clôture − 0,5 ATR, même validité. Sens pré-déclaré : pas mieux. |
| X5 | exploratoire, **bloqué** | d7 à risque constant, stop {production ; 3 ATR} × objectif {production ; aucun}. Bloqué tant que l'écart de base du rejeu n'est pas expliqué (§33.4). |
| X6 | exploratoire | Portefeuille : € par an, 21 contre 63 séances, sous le plafond de risque ouvert en vigueur. Aucun seuil. |
| X7 | exploratoire | d2 par ordre à plage de déclenchement (stop d'achat au-dessus du pivot), limite = pivot × (1 + `entry_max_pct_above_pivot`). |

**Issue**
- CANDIDAT sur les données de test → **suivi en ombre d'abord** (durée et critère de suspension pré-déclarés), puis ADR d'entrée en production à 50 % de `risk.max_risk_per_trade_pct`.
- Sinon REJET, ou NON CONCLUANT si la puissance est sous 0,5. L'issue la plus probable, annoncée d'avance, est l'échec.

### État au 27/10/2026 (lancement décidé par l'utilisateur)

**Données**
- `delisted-check` : EODHD fournit les valeurs radiées.
  - New York : 33 185 radiées, dont 8 295 avec ISIN, historique accessible.
  - Londres : 2 845 radiées, dont 1 475 avec ISIN, historique accessible.
  - Zurich : 167 radiées, historique à revérifier, l'exemple testé n'ayant aucune séance.
- **`delisted-load`** (codé, pas encore exécuté). Il garde les marchés principaux (jamais l'OTC à New York). Il trie ensuite par liquidité sur un instantané mensuel de toute la place, sans lire d'historique : une radiée est retenue si un instantané atteint la moitié du seuil de volume de la place. Les radiées retenues sont enregistrées comme valeurs inactives, hors univers. Une valeur sans ISIN reçoit une clé interne de 12 caractères, qui n'a jamais la forme d'un ISIN. `--history` charge ensuite leur historique.
  - Coût : un instantané par mois depuis 2017, soit environ 118 instantanés par place, que la commande `--dry-run` chiffre avant tout appel.

**Banc**
- Le plan peut déclarer ses propres groupes (`venue_groups`). Un groupe d'une seule place est jugé sur l'indice de cette place (`regime.indices_by_mic`). Le rejeu de calibration reste sur les groupes européens, car le backtest de référence est européen.
- Déclencheur placebo T00 : événements tirés au hasard au taux déclaré, ou classement mensuel aléatoire. Il est graine-fixé.
- Plan **gelé le 28/10/2026** : `research/prereg/2026-10-28_reg2.yaml` (brouillon du 27/10 accepté tel quel). Il contient C1, C2, le placebo X1 (3 cellules) et X2, sans étape 1, avec une étape 2 de 3 cellules au plus, soit 9 cellules. Il a été exécuté de bout en bout sur une base synthétique.
  - X3, X4, X6 et X7 demandent du code supplémentaire : ils sont reportés à un enregistrement suivant.
- **À faire avant de geler le plan** :
  1. Relecture du brouillon par l'utilisateur.
  2. Renommage du fichier avec sa date.
  3. Exécution de `delisted-load`, puis activation des places et chargement des historiques des valeurs actives. Aucune donnée de test n'est lue avant le gel.

## 33.4 Corrections de méthode du banc (faites le 27/10/2026)

- **Sélection adverse** : effectif par groupe (masqué sous 20), plus la moyenne sur tous les signaux.
- **Point et intervalle** : le rapport imprime aussi la moyenne des séries hebdomadaires, qui est la quantité de l'IC. Concerné : les détecteurs existants et les différences F0.
- **Excès** : la section C imprime côte à côte l'excès sur la moyenne équipondérée du marché (cellules mensuelles) et l'excès sur les contrôles appariés, celui que juge A4.
  - Pour la cellule 63 séances : +0,113 sur le marché contre +0,072 sur les contrôles appariés.
- **Libellés F0c** : « stop 3 × ATR, objectif de production conservé, R en unité de production ». La variante dite « ATR×3 » n'a jamais été mesurée sans objectif ni à risque constant.
- **Écart de base de d7.** Le rejeu donne environ −0,51 R en découverte et −0,36 R sur la période scellée, contre −0,137 R au backtest n° 2.
  - Hypothèse : le banc compte un demi-écart acheteur-vendeur (Corwin–Schultz) à l'entrée et à la sortie, que le backtest n° 2 ne compte pas. Sur des stops serrés, ce coût pèse lourd en R. La calibration ne compare que le R brut, c'est pourquoi elle est passée.
  - Le rapport imprime désormais, pour les mêmes trades, le R sans demi-écart et le R net du backtest n° 2 : la prochaine exécution tranchera.

## 33.5 Idées rejetées

- Élargir le prix maximal d'entrée sur la foi de la table « non exécutés » : la mesure est biaisée par construction.
- Entrer au marché sans prix maximal : contraire à la règle 7.
- Une confirmatoire « d7 stop ATR×3 sans objectif » : cette variante n'a jamais été mesurée.
- Supprimer les objectifs partout : effet non monotone, 1 test sur 10.
- Un pré-filtre « coût < 0,05 R » : réglage choisi après coup.
- Réutiliser la période scellée.
- Passer la cellule 63 séances en surveillance, ou abaisser A2 ou A4.

## 33.6 Questions ouvertes

1. **d4** : la règle est atteinte de justesse (borne haute −0,002). La désactivation est maintenue (point −0,397, brut −0,174, déjà en surveillance), mais l'utilisateur peut demander le recalcul avec l'estimateur corrigé avant de décider.
2. **Réplication** : une réplication sur Londres, Zurich et New York (mêmes années, momentum corrélé) suffit-elle à parler de confirmation ? D'où la règle : suivi en ombre avant toute production.
3. **Produit** : une détention de 63 séances et 6 lignes en rotation mensuelle sont-elles compatibles avec l'usage voulu (gestion active quotidienne) ?
4. **Faisabilité** : ordres à plage de déclenchement chez BoursoBank (Découverte) et chez Saxo ; ampleur réelle de l'écart acheteur-vendeur (relevés Saxo).
