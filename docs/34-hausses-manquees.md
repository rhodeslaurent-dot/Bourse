# 34 — Hausses manquées : audit de l'entonnoir, littérature, étude et enregistrement n° 3 (28/10/2026)

> Demande de l'utilisateur, 28/10/2026 : « malgré plusieurs hausses majeures de certaines actions sur les différents marchés, je n'ai pas eu de signaux d'achat. […] objectif d'investissement dynamique […] prêt à faire un aller-retour sur quelques jours. »
>
> Étapes demandées :
> 1. repérer les actions en hausse cumulée de plus de 5 % (hors bruit) ;
> 2. passer l'outil au début de la hausse ;
> 3. simuler sa réponse ;
> 4. analyser pourquoi il ne conseille pas l'achat ;
> 5. identifier les nouveaux critères ;
> 6. appliquer les changements.
>
> L'utilisateur a laissé la méthode libre.

## 34.1 Méthode retenue, et pourquoi elle diffère un peu de la demande

**Partir des hausses ne suffit pas.**
- Partir des valeurs qui ont monté puis regarder ce que disait l'outil mesure le **rappel**, P(signal | hausse).
- Ce qui décide d'un achat, c'est la **précision**, P(hausse | signal), comparée au **taux de base** : la part des séances ordinaires suivies d'une hausse.
- Sur une petite valeur volatile, +5 % en dix séances arrive par simple bruit.
- Un critère qui « attrape » toutes les hausses peut donc aussi déclencher sur toutes les baisses.
- L'étude mesure les deux, avec la même règle.

**Partie A — hausses** (rappel et « pourquoi pas ») :
- Définition d'une hausse : montée d'au moins max(5 % ; 2 σ·√durée).
  - σ est la volatilité quotidienne des 20 séances qui précèdent le creux.
  - La hausse est repérée par un zigzag des clôtures, avec un seuil de retournement de max(3 % ; 1,5 ATR).
  - Le zigzag n'est connu qu'après coup : il sert d'**étiquette**, jamais de critère.
- L'outil est rejoué sur chaque séance, de deux séances avant le creux à cinq séances après.
- Le rejeu applique les **règles de production** :
  - chaque détecteur avec ses paramètres réels, et les zones ;
  - l'admission (prix maximal R4, structure R5) ;
  - l'ordre limite au prix maximal valable `entry.expiry_sessions` ;
  - le score (blocs T, R, M) ;
  - le portail de la place, avec un régime rejoué : indice contre MM50, largeur, volatilité ;
  - le gel avant publication (R14), quand l'historique des résultats est chargé.
- Le rejeu est fait deux fois :
  - avec la **politique** actuelle (d3/d4 désactivés par l'ADR-014, d2/d7 en surveillance par l'ADR-013) ;
  - avec **tous les détecteurs actifs**, ce qui mesure le coût de cette politique.
- Pour chaque hausse, l'étude retient l'étape la plus avancée atteinte : aucun signal, politique, sans objectif, stop trop large, configuration dégradée, score, portail, résultats imminents, ordre jamais exécuté, ordre exécuté.
- Pour un ordre exécuté : part de la hausse capturée et R net, en CTO comme en PEA.

**Partie B — contrôle** (précision et lift) :
- Toutes les séances de 120 valeurs tirées au hasard, prises **sans regarder la suite**.
- Étiquette « hausse exploitable » (triple barrière, López de Prado 2018) : depuis l'ouverture suivante, le cours monte de max(5 % ; 2 σ·√10) avant de toucher le stop à −2 ATR, dans les 10 séances.
- Résultats produits :
  - taux de base par classe de volatilité ;
  - précision et lift de chaque détecteur ;
  - R net par ordre exécuté ;
  - lift de chaque décile de 14 caractéristiques observables à la clôture, dont « jour de publication de résultats ».

**Taille d'échantillon** (intervalles de Wilson imprimés à chaque ligne) :
- **Hausses.** Une proportion à ±3 points demande environ 1 000 hausses par strate (place, taille, forme, volatilité), et ±5 points environ 400. Le défaut de 20 000 hausses tirées couvre environ 15 strates à ±3 points.
- **Contrôle.** 120 valeurs × environ 2 300 séances ≈ 280 000 séances, ce qui rend le taux de base très précis.
- **Précision d'un détecteur.** Elle demande au moins 400 déclenchements pour ±5 points ; d7, qui se déclenche sur environ 0,4 % des séances, en a plus de 1 000.
- **R net moyen.** L'écart-type est d'environ 1 R par trade : ±0,1 R demande environ 400 ordres exécutés. En dessous, le rapport donne l'intervalle, jamais une conclusion.

**Données et sceaux :**
- **Périmètre.** Places européennes (groupes P0/P1/P2), 2017 → fin des données.
- **New York, Londres et Zurich** restent **non lues** tant que l'enregistrement n° 2 n'a pas tourné : ce sont ses données de test.
- **Avant 2017.** La commande refuse toute date antérieure. Cet historique n'est pas encore chargé et il est réservé à la réplication de l'enregistrement n° 3 (§34.5).
- **Registre.** Chaque lecture est inscrite au registre de la recherche (`data/research/ledger.csv`, ligne `etude_hausses_exploratoire`).
- **Statut.** L'étude est **exploratoire** : elle ne change aucune règle. Une règle ne change qu'au terme d'un enregistrement pré-déclaré (docs/31).

**Ce qui n'est pas rejoué** (écrit en tête du rapport) :
- lettres, nouvelles, fondamentaux ;
- plafonds de portefeuille, coupe-circuit, niveau de risque du jour ;
- d8, qui demande le radar des forums du jour.

Hypothèses favorables à l'outil :
- le portail suppose les conditions des lettres remplies, mais le rapport mesure aussi l'effet contraire sur Paris (§34.2, point 3) ;
- la capitalisation et l'éligibilité PEA sont celles d'aujourd'hui ;
- les valeurs radiées européennes sont absentes (biais de survie).

## 34.2 Ce que dit déjà le code (audit de l'entonnoir, certain)

1. **En production, l'outil ne peut presque plus conseiller d'achat sur un signal de cours.**
   - d3 et d4 sont désactivés (ADR-014) ; d2 et d7 sont en surveillance seulement (ADR-013).
   - Ces décisions viennent de mesures : espérance négative au coût complet (d7 : brut +0,041 R, frais 0,178 R, net −0,137 R ; n 2 716).
   - Restent d8, qui exige un emballement repéré par le radar des forums, et les configurations des lettres.
   - L'absence d'achat sur les hausses récentes est donc **le résultat attendu de la politique actuelle**, pas une panne.
2. **Fin de séance et ordre limite.**
   - Un détecteur voit la configuration à la clôture et pose une limite au prix maximal : pivot + 1 à 2 %, ou plus haut + x %.
   - Une hausse qui part en écart d'ouverture, ou d'une traite, ne revient pas à la limite : l'ordre n'est jamais exécuté.
   - Le rapport mesure la part de la hausse encore à venir après la première clôture haussière.
3. **Portail de Paris (R10, option C).**
   - Sur Euronext Paris seulement, le portail n'est « ouvert » que si une lettre récente, de 5 séances au plus :
     - voit le CAC au-dessus de l'obstacle publié ;
     - et ne conseille pas d'attendre.
   - Sinon, chaque achat doit atteindre un ratio net ≥ 2,5 et un score ≥ 75.
   - L'étude chiffre la part des ordres qui resteraient dans ce cas.
4. **Coûts en R, qui décident des allers-retours courts.** Un aller-retour coûte, selon le compte (hors écart acheteur-vendeur) :

   | Compte et univers | Aller-retour | Poids avec un stop à 5 % |
   |---|---|---|
   | CTO Saxo, zone euro hors France | 0,16 % | ≈ 0,03 R |
   | CTO, grandes valeurs françaises (TTF 0,4 %) | 0,56 % | ≈ 0,11 R |
   | CTO, Espagne au-delà de 1 Md€ (ITF 0,2 %) | 0,36 % | ≈ 0,07 R |
   | CTO, États-Unis (change 0,25 % par sens) | ≈ 0,66 % | ≈ 0,13 R |
   | CTO, Royaume-Uni (timbre 0,5 % + change) | ≈ 1,16 % | ≈ 0,23 R |
   | PEA BoursoBank Découverte (0,5 % par ordre) | ≈ 1,0 à 1,2 % | ≈ 0,2 à 0,25 R |

   Un aller-retour de quelques jours dans le PEA Découverte part donc avec 0,2 R de retard. Le CTO sur une valeur de la zone euro hors France est 6 fois moins cher.
5. **Résultats d'entreprises.**
   - Le moteur gèle toute entrée avant une publication (R14), avec raison : un stop ne protège pas d'un écart.
   - Une date de résultats inconnue enlève 4 points au bloc R du score.
   - L'outil n'avait **aucun historique** des publications : il ne pouvait ni étudier, ni exploiter la dérive qui suit une publication, l'effet de court terme le mieux documenté (§34.3).
6. **Temps réel.**
   - Sans l'abonnement Saxo Euronext L1 (7 €/mois, remboursés à partir de 4 ordres/mois), les cours de séance sont différés de 15 minutes.
   - La règle 2 interdit alors toute entrée en séance : un tel signal est marqué « à vérifier », sans fiche d'ordre.

## 34.3 Ce que dit la littérature (synthèse, 28/10/2026)

Recherche documentaire faite pour cette demande ; principales sources entre parenthèses.

- **Ton banc d'essai est cohérent avec la littérature.**
  - Les anomalies publiées perdent en moyenne 58 % de leur rendement après publication (McLean & Pontiff 2016).
  - Une fois coûts et déclin comptés, l'anomalie moyenne rapporte environ 4 pb/mois (Chen & Velikov 2023).
  - Le momentum, la proximité du plus haut sur 52 semaines, le retournement de court terme et les chocs de volume sont les plus étudiés et les plus chargés en rotation. Les trouver nuls net de frais est le résultat attendu.
- **Les pistes encore plausibles sur 2 à 20 séances sont toutes déclenchées par une information :**
  1. **Dérive après publication.** La réaction du jour de publication (rendement anormal et volume) prédit la dérive des semaines suivantes (Bernard & Thomas 1989 ; Brandt et al. 2008 ; Garfinkel & Sokobin 2006). Elle est documentée en Europe (Liu, Strong & Xu 2003 ; Hung, Li & Wang 2015), mais **en net déclin** sur les grandes valeurs depuis 2006 (Martineau 2022 ; Christensen et al. 2025).
  2. **Choc « avec nouvelle » contre choc « sans nouvelle ».** Un choc accompagné d'une nouvelle se prolonge ; un choc sans nouvelle se retourne (Chan 2003 ; Savor 2012).
  3. **Achats de dirigeants.** C'est la piste dont la preuve européenne est la plus solide (Fidrmuc et al. 2006 et 2013 ; Betzer & Theissen 2009), sur un horizon d'environ un mois, en baisse depuis MAR (Grimberg & Herberger 2020).
  4. **Prime d'annonce.** Les cours montent légèrement avant une publication (Barber et al. 2013) : quelques dizaines de points de base, ce qui n'est jouable qu'à faible coût.
  5. **Relèvements d'analystes.** L'effet existe, mais les données disponibles (Yahoo, TradingView) ne sont **pas datées à l'époque** : il faudra un test vers l'avant, après archivage.
- **Écartés :**
  - short squeeze, car la preuve va dans l'autre sens (Jank et al. 2021) ;
  - entrée en indice (effet disparu : Greenwood & Sammon 2025) ;
  - rachats d'actions (effet faible en France) ;
  - cassure du range d'ouverture (non prouvée nette de coûts).
- **Pièges à éviter :**
  - sélection sur le résultat (d'où la partie B du §34.1) ;
  - effet MAX : les titres à très forte séance sous-performent ensuite (Bali et al. 2011 ; Walkshäusl 2014 en Europe) ;
  - tests multiples : seuil t > 3 (Harvey, Liu & Zhu 2016), Sharpe dégonflé, toutes les variantes déjà essayées comptées.

**Conséquence.** La première famille à tester est la **réaction aux résultats**, en CTO sur les univers bon marché. Elle exige l'historique des publications : d'où `earnings-history` et l'enregistrement n° 3.

## 34.4 Outils construits

- **`moves-study`** :
  - code : `app/domain/moves.py` (pur) et `app/services/moves.py` ;
  - commande : `python -m app.cli moves-study --from 2017-01-02 [--max-episodes 20000 --control-values 120]` ;
  - sortie : rapport agrégé `data/studies/hausses_<horodatage>.txt` et `.json` ;
  - sections du rapport : en bref, profil des hausses (dont « démarrées sur une publication de résultats »), entonnoir en production, entonnoir avec tous les détecteurs actifs, strates, détecteurs et délais, effet du portail de Paris, contrôle, lift des caractéristiques.
- **`earnings-history`** :
  - code : table `earnings_events` (migration 0027), `app/services/earnings.py`, `app/data/providers/earnings.py` ;
  - sources : le calendrier EODHD si le forfait l'inclut (un 403 bascule sur Yahoo), sinon Yahoo, par pages de 100 publications avec une pause d'une seconde ;
  - contenu : date, moment (avant l'ouverture, après la clôture), BPA publié et estimé ;
  - sortie : couverture par année et par groupe de places ;
  - **aucun cours lu.**
- **Cours en pseudo temps réel et réévaluation en séance** (ADR-015, docs/17 §17.11) :
  - Saxo toutes les 15 s, EODHD toutes les 15 min, plus un bouton « Rafraîchir les cours » ;
  - réévaluation des propositions ouvertes toutes les 15 s : prix d'entrée atteint, déclencheur franchi, configuration dégradée, limite dépassée ;
  - une entrée n'est actionnable qu'avec un cours en temps réel de moins de 3 min (règle 2).
- **Assistant :**
  - il retrouve la valeur citée dans une question ;
  - son dossier contient le coût complet par compte, les comptes exclus et leur raison, et l'origine de l'espérance ;
  - la fiche affiche « espérance du détecteur ».

## 34.5 Enregistrement n° 3 (déclencheurs d'information)

Le plan, gelé avant tout chargement de l'historique des publications, est décrit ici dès sa fusion (voir le journal de docs/15).

## 34.6 Changements appliqués, et ce qui ne change pas

**Appliqués le 28/10/2026 :**
- les cours en direct et la réévaluation en séance (ADR-015) ;
- l'assistant (coûts, comptes, espérance) ;
- la taxe espagnole et la taxe italienne, appliquées seulement au-delà de leur seuil de capitalisation, dans le moteur ;
- l'étude des hausses ;
- l'historique des résultats et son rejeu (R14 et score) dans l'étude ;
- le plan n° 3.

**Ne changent pas sans mesure** (docs/31) :
- La liste des détecteurs actifs, les seuils de score et le prix maximal ne bougent pas.
- Rendre d2, d3, d4 ou d7 actionnables reviendrait à acheter des signaux mesurés perdants au coût complet. Les hausses qu'ils auraient « attrapées » ne compensent pas, au contrôle, celles qu'ils auraient fait perdre. L'étude le chiffre (partie B).
- Une nouvelle famille ne devient actionnable qu'après son enregistrement. Le suivi « fantôme » des propositions non exécutées et des cellules n° 2 et n° 3 continue d'accumuler une preuve vers l'avant, sans risque.

## 34.7 Ordre des opérations sur le PC (une commande à la fois)

1. **Mettre à jour et reconstruire.**
   - `git pull`, puis `docker compose up -d --build` (migrations 0027 et 0028).
   - Puis `docker compose exec app python -m app.cli params-init --update` (bloc `live_quotes`, tâche `intraday_reeval`).
2. **Vérifier l'univers.** Faire le contrôle `universe_refresh` encore en attente (correction des ETF).
3. **Charger l'historique de New York, Londres et Zurich.**
   - `backfill --from 2016-01-01 --exchanges US,LSE,SW --missing-only` : enregistrement n° 2, inchangé.
4. **Exécuter l'enregistrement n° 2.** `research calibrate`, puis `research run`, avec `--prereg research/prereg/2026-10-28_reg2.yaml --run 2`.
5. **Charger l'historique des publications.**
   - `earnings-history --from 2016-01-01` : publications et couverture, sans aucun cours lu.
   - Me transmettre la couverture.
6. **Lancer l'étude.** `moves-study --from 2017-01-02` (de préférence la nuit), puis me transmettre le rapport `.txt`.
7. **Exécuter l'enregistrement n° 3.** Sur 2017-2026, d'abord le placebo, puis C1/C2.
8. **Seulement pour les cellules retenues.**
   - Charger l'historique européen ancien : `backfill --from 2005-01-01` pour l'Europe, puis `earnings-history --from 2005-01-01`.
   - Puis la réplication scellée 2007-2015, en un seul descellement.

## 34.8 Décisions à prendre

1. **Abonnement Saxo Euronext L1** (7 €/mois, remboursés à partir de 4 ordres/mois). Il rend actionnables les entrées repérées en séance ; sans lui, elles restent « à vérifier ».
2. **Compte des allers-retours courts.** Recommandation : les faire en CTO Saxo sur les valeurs de la zone euro hors France, et pas dans le PEA Découverte (0,2 R de frais par aller-retour). Le moteur choisit déjà le compte au meilleur ratio net ; c'est une règle de conduite à confirmer.
3. **Après le verdict n° 3 :**
   - Si C1 ou C2 passent : un détecteur « réaction aux résultats » en production, actionnable sans phase de surveillance, et un ADR.
   - Sinon : archivage quotidien des consensus d'analystes, et collecte des déclarations de dirigeants (AMF BDIF), pour un enregistrement n° 4 vers l'avant.
