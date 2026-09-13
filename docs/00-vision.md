# 00 — Vision, objectifs, périmètre

## 0.1 Point de départ (ce qui existe et fonctionne)

Aujourd'hui, la gestion quotidienne repose sur un projet Claude (« Investissement Bourse ») qui applique chaque jour une mécanique stable :

- une grille de **scoring sur 100** (Catalyseurs & news 0–30 ; Technique/momentum 0–25 ; Fondamentaux 0–20 ; Risque/volatilité/agenda 0–15 ; Contexte secteur/macro 0–10) ;
- des règles d'interprétation : **BUY** ≥ 75 avec risque faible/moyen, catalyseur clair et entrée cohérente ; **WATCH** 60–74 ; **SELL/REDUCE** sur thèse invalidée, stop cassé, risque excessif ou meilleure opportunité ; **HOLD** ; **NO TRADE** ;
- une gestion du risque : **0,75 % du capital pilote par trade** (300 € pour 40 000 €), taille = risque / distance entrée–stop, 5 à 15 positions, exposition 60–80 %, cash conservé en cas d'incertitude, mention explicite du cash additionnel (jusqu'à 90 000 €) pour les opportunités exceptionnelles ;
- un **rapport quotidien en 10 rubriques** (macro, marchés, portefeuille, BUY, ventes/allègements, watchlist, valeurs à éviter, plan d'action, ordres manuels, risques globaux) ;
- des sources de signaux : **newsletters Zonebourse (~10h Paris)** et **lettre Momentum de Capital (~13h Paris)**.

Cette mécanique est conservée. L'outil l'industrialise et l'enrichit ; il ne la remplace pas. Les horizons du projet (intraday, quelques jours, swing 2–6 semaines, moyen terme) sont conservés dans la nomenclature (`days`, `swing_2_6w`, `medium`) ; l'intraday pur n'est pas ciblé en v1 (incompatible avec 1 h/jour), mais un D1 sorti le jour même reste possible.

## 0.2 Les deux problèmes à résoudre

1. **Rareté des opportunités sur le CAC 40** par périodes → élargir l'univers à **toutes les places éligibles au PEA** (Euronext Paris/Amsterdam/Bruxelles/Lisbonne/Milan/Oslo/Dublin, Xetra, Madrid, Vienne, Nasdaq Nordic, Varsovie en option) avec un filtre de liquidité strict (voir docs/05).
2. **Signaux trop tardifs** : les newsletters arrivent à 10h et 13h, alors que le gap haussier s'est souvent formé à 9h00 et est en partie absorbé → l'outil doit **détecter lui-même** les opportunités dès la pré-ouverture (communiqués 7h–9h) et dans les premières minutes après 9h00, et proposer des **points d'entrée sur les valeurs momentum déjà sous surveillance**. Les newsletters deviennent une source de **confirmation et de watchlist**, plus le seul déclencheur.

## 0.3 Objectifs (dans l'ordre)

| # | Objectif | Comment l'outil y contribue | Indicateur de succès (mesuré par l'outil) |
|---|---|---|---|
| 1 | Maximiser les gains | Détection précoce, univers élargi, sélection par score, entrées sur pullback des leaders momentum | Espérance par trade (R), profit factor, performance vs CAC 40 / STOXX 600, **gain de précocité** (écart entre le prix d'entrée obtenu et le prix au moment de la newsletter) |
| 2 | Minimiser les pertes | Stops systématiques, taille par le risque, filtre de régime, coupe-circuit, interdiction de trade avant publication | Perte moyenne en R ≤ 1,1 R, drawdown max, % de trades sans stop = 0 |
| 3 | ≤ 1 h de travail manuel/jour | Disponibilité de l'utilisateur intégrée au moteur (modes disponible / réunion / absent), écran « 3 décisions du jour », entrées préparées la veille (ordres à plage de déclenchement), fiches d'ordre prêtes à saisir, déclaration des exécutions en 2 clics | Temps déclaré par créneau (saisie optionnelle), nombre d'actions manuelles/jour, propositions par jour ≤ 3 |
| 4 | Utilisable à distance | Web mobile-first derrière Cloudflare Access + bot Telegram ; PC pro sans droits admin = navigateur uniquement | Disponibilité ≥ 99 % en heures de marché, temps de chargement mobile < 2 s |
| 5 | Pas de passage d'ordre automatique | Connecteurs courtiers en lecture seule, fiches d'ordre manuelles | Test automatisé « aucun endpoint de trading » |
| 6 | Gestion des risques par les stops | Stop initial obligatoire sur toute proposition, suivi du stop, alerte de franchissement, rappel de saisie du stop chez le courtier | 100 % des positions ont un stop enregistré et un stop « courtier » confirmé |
| 7 | SRD pour l'efficacité de trésorerie, PEA pour la fiscalité | Routage de chaque opportunité vers le compte le plus adapté (docs/08), suivi du calendrier de liquidation, coût du SRD intégré au calcul | Coût SRD/an, part des gains réalisés en PEA, taux de couverture jamais < seuil |

## 0.4 Périmètre fonctionnel, par priorité d'évolution

1. **P1 — Aide à l'investissement et identification d'opportunités** (docs/04 à 09) : univers, données, détecteurs, scoring, risque, comparaison des comptes, fiches d'ordre, rapports, alertes. **C'est le MVP** — livré en deux temps (docs/15) : d'abord le portefeuille opérationnel, les stops et le risque, puis les entrées préparées (pullbacks et cassures, ordres J+1 à plage de déclenchement) ; la détection d'ouverture (gaps) n'arrive qu'après validation des flux temps réel, et seulement en mode « disponible ».
2. **P2 — KPI de suivi des gains et pertes** (docs/10) : import des positions/exécutions, P&L réalisé/latent par compte, stratégie, source, période ; coûts (courtage, CRD, TTF) ; comparaison aux indices.
3. **P3 — KPI de suivi fiscal** (docs/11) : PMP, plus/moins-values CTO, moins-values reportables, estimation d'impôt paramétrée, PEA (ancienneté, versements, 5 ans), fait générateur SRD.
4. **P4 — Analyse et retour d'expérience** (docs/12) : journal, R-multiples, MAE/MFE, expectancy par détecteur/source/heure, post-mortem des signaux non pris, revue hebdomadaire assistée par LLM.

Hors périmètre (v1) : produits dérivés, crypto, vente à découvert, passage d'ordre, multi-utilisateurs, hébergement cloud.

## 0.5 Utilisateur et contraintes

- Un seul utilisateur, à La Réunion (UTC+4, pas d'heure d'été). Paris = UTC+2 (été) / UTC+1 (hiver) → l'ouverture de 9h00 Paris tombe à **11h00 (été) / 12h00 (hiver) heure Réunion**, la clôture 17h30 Paris à **19h30 / 20h30**. Wall Street ouvre à 15h30 Paris = 17h30/18h30 Réunion.
- Travail en journée ; PC professionnel **sans droits administrateur** → accès uniquement par navigateur (et sous réserve du proxy d'entreprise), smartphone personnel pour Telegram.
- Hébergement sur le PC personnel Windows (Docker Desktop déjà utilisé, PostgreSQL et n8n déjà en place), allumé toute la journée. Pas de VPS (coût, bugs vécus).
- Comptes : **PEA chez BoursoBank**, **compte-titres avec SRD chez Saxo Banque**.
- Budget : abonnements données/newsletters acceptés sans plafond strict si la valeur est démontrée (docs/04 justifie chaque ligne).
- Développement : Claude Code, par phases, avec tests.

## 0.6 Ce que l'outil n'est pas

- Pas un robot de trading : il n'exécute rien, il n'a pas accès aux ordres.
- Pas un oracle : il sépare **faits** (données sourcées), **sources externes** (newsletters, analystes), **analyse** (calculs), **opinion** (rédaction LLM) et **incertitudes** (données manquantes, publication imminente).
- Pas un backtester institutionnel : la validation se fait par **historisation de tous les signaux** et réconciliation à J+5/J+20 (docs/12), pas par optimisation de paramètres.
- Pas un outil qui pousse à investir : 5 positions et 60 % d'exposition sont des repères, jamais des objectifs ; l'absence de proposition est un résultat normal.
