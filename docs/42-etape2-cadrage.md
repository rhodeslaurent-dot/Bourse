# 42 — Étape 2 : cadrage après la mesure de l'étape 1 (17/11/2026)

> Note de décision, sans code. L'étape 2 du plan de l'utilisateur (analyse multi-agent, ADR-020) a été conçue le 15/11, avant toute mesure. La mesure de l'étape 1 (docs/40 §40.3 bis) change ce qu'elle peut apporter. Cette note pose les options ; rien n'est codé avant le choix de l'utilisateur.

## 42.1 Ce que l'étape 1 a établi

- **Une seule source tient contre son témoin** (la même valeur 60 séances avant et après) : la **vente de dirigeant** — l'action fait environ 1,7 point de moins sur 20 séances que la même valeur ensuite (t 3,5) ; contraste achats − ventes +1,9 % (t 7,3). C'est une **information de prudence**, pas un déclencheur : effet faible, avant frais, plus net en marché calme et haussier.
- **Aucun détecteur d'entrée n'a d'espérance positive** au coût complet (rejeu n° 10 du 16/11 : d7 −0,25 R sur 3 674 trades, d2 −0,35 R).
- **Lettres et signaux Momentum Capital, surprises de résultats, achats de dirigeants, rachats d'actions, positions courtes : non retenus.**

Conséquence : l'ADR-020 prévoyait qu'une IA analyste, un contradicteur et un arbitre proposent des niveaux (entrée, stop, objectifs, taille) **sur les idées du jour**. Or ces idées viennent toutes de sources qui ne battent pas leur témoin. Choisir de bons niveaux sur une idée sans avantage mesuré ne crée pas d'avantage : au mieux, cela réduit les pertes (entrées mal placées, frais).

## 42.2 Trois options

| Option | Ce que fait l'IA | Coût IA estimé | Ce qui est mesuré | Risque |
|---|---|---|---|---|
| **A — Étape 2 telle que prévue** (ADR-020) | analyste, contradicteur, arbitre sur ~10 idées par jour ; niveaux proposés face à Python | 20 à 25 $ par mois | les trois auteurs de niveaux, à 1, 5, 20 séances | mesurer longtemps des propositions qui partent toutes d'idées négatives |
| **B — Étape 2 « défense »** | revue du soir de chaque **position** avec les faits mesurés (vente de dirigeant récente, communiqué de résultats, tendance de l'indice) ; proposition de stop ou d'allègement, attaquée par un contradicteur ; sur les idées, rédaction et contradiction seulement, **sans niveaux** tant qu'aucune source d'entrée n'est validée | 3 à 8 $ par mois | chaque proposition de défense contre « ne rien faire » | peu de cas (2 positions aujourd'hui) : mesure lente |
| **C — Banc de sortie d'abord** | aucune nouvelle IA ; pré-enregistrement de 2 ou 3 règles de sortie (stop suiveur, allègement à +1 R) puis rejeu au coût complet sur d7 — piste du rejeu n° 10 : 54 % des trades passent par +1 R, 41 % des perdants y étaient passés | 0 $ | espérance nette par règle, ≥ 300 cas, t ≥ 3 | aucune règle ne passe : on le saura |

## 42.3 Recommandation : B et C en parallèle

- **B** sert dès maintenant : il s'appuie sur le seul fait qui tient (la vente de dirigeant) et protège ce qui est déjà en portefeuille, à faible coût.
- **C** cherche un avantage là où les données le suggèrent (la sortie, pas l'entrée), sans IA ni dépense.
- **A** reste ouvert : il repart le jour où une source d'entrée (ou une règle de sortie) passe la mesure.

## 42.4 Lots si B + C est retenu

- **D1 — prudence vente de dirigeant** (Python seul) : mention « vente de dirigeant dans les 20 dernières séances » sur la fiche et dans la revue du portefeuille, avec la mesure qui la justifie ; jamais un ordre, jamais un blocage.
- **D2 — revue IA du soir des positions** : analyste (Sonnet 5.5) et contradicteur sur chaque position ouverte ; proposition de stop ou d'allègement passée au contrôle de vraisemblance, quantité recalculée par Python (règle 7), instantané de décision (règle 10) ; suivi contre « ne rien faire ».
- **C1 — banc de sortie** : plan pré-enregistré (règles, période, seuils de lecture) avant tout rejeu ; rejeu au coût complet ; verdict selon la règle.

## 42.5 À décider par l'utilisateur

1. Option : A, B, C, ou B + C (recommandé).
2. La vente de dirigeant comme simple mention de prudence (D1) : oui ou non.
