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

## 34.5 Enregistrement n° 3 : déclencheurs d'information (résultats)

> Plan `research/prereg/2026-10-28_reg3.yaml`, empreinte sha256 `7242061336be8fcff1572d65374690fa0c951d5e0bb931cda90ee21eaa8466fd`.
> - **Gelé le 28/10/2026**, avant tout chargement de l'historique des publications dans la base et avant toute lecture d'un résultat lié à ces publications.
> - Hypothèses tirées de la littérature (§34.3), pas des données. Graine 20261029.
> - Seuils A1–A13, inférence, paramètres (`config/params.yaml`) et hypothèses chiffrées repris à l'identique du n° 2.

**Placement des publications.**
- Une publication est placée sur sa **séance de réaction** J0 :
  - le jour même, si elle tombe avant l'ouverture, en séance ou à une heure inconnue ;
  - la séance suivante de la valeur, si elle tombe après la clôture.
- Si deux sources se contredisent sur l'heure, les deux séances sont marquées, et comptées.
- Une valeur n'est « connue » qu'à moins de 100 jours d'une publication chargée. Ailleurs, l'absence de publication veut dire « inconnu », jamais « pas de publication », et aucun déclencheur ne s'y déclenche.

**Les trois déclencheurs.**
- **t14 — dérive après publication** (Brandt et al. 2008 ; Garfinkel & Sokobin 2006). Il se déclenche sur une séance de réaction J0 quand trois conditions sont réunies :
  - rendement anormal d'au moins 2 écarts-types des 20 rendements précédant J0 (clôture J−1 → clôture J0, moins l'indice de la place : CAC 40 pour Paris, STOXX 600 ailleurs) ;
  - volume de J0 au moins égal à 2 fois la médiane des 50 séances précédentes ;
  - clôture dans la moitié haute de la séance.
- **t15 — prime d'avant publication** (Barber et al. 2013) : achat 5 séances avant la séance de réaction, vente à la clôture de la séance qui la précède. **Hypothèse signalée dans le rapport :** la date de publication est connue 5 séances à l'avance, grâce au calendrier financier publié par la plupart des émetteurs.
- **t16 — contrôle** (Chan 2003) : le même choc que t14, mais sans publication à ± 2 séances. Un retournement est attendu. Si t16 ≈ t14, ce n'est pas la publication qui fait la hausse.

**Cellules** (10 au total, Holm sur 2 pour les confirmatoires) :

| Cellule | Rôle | Contenu | Sortie |
|---|---|---|---|
| C1 | confirmatoire | t14 sans filtre de surprise | 10 séances |
| C2 | confirmatoire | t14 sans filtre de surprise | 20 séances |
| X1 | exploratoire | placebo (valeurs et dates tirées au hasard), **lu avant C1 / C2** | 10 et 20 séances |
| X2 | exploratoire | t14 avec surprise positive exigée (BPA publié > attendu ; inconnu → pas de signal) | 10 séances |
| X3 | exploratoire | t15 | 4 séances |
| X4 | exploratoire | t16 | 10 séances |

- **Entrée et stop :** toutes les cellules entrent à l'ouverture suivante, avec un plafond à la clôture + 1 ATR et une validité d'une séance. Elles utilisent le stop initial standard du banc (sortie X3 : 3 σ √h, puis sortie au temps).
- **Groupes et comptes :** groupes européens P0 / P1 / P2. Le verdict se fait sur le CTO ; le PEA est rapporté comme seconde vue des coûts.

**Couverture.**
- Un groupe dont moins de 50 % des observations éligibles (valeur × séance) sont « connues » est déclaré « couverture insuffisante ».
- Ses cellules liées aux résultats ne produisent alors aucun signal, et il ne compte pas dans le verdict.
- La couverture de chaque groupe et le nombre de publications placées sont imprimés en tête du rapport.

**Périodes.**
- **Découverte (2017-01-02 → 2026-07-31) :** elle sert entièrement de test, puisque rien n'a été choisi sur les publications. 5 plis de walk-forward.
- **Période ancienne scellée (2007-01-02 → 2015-09-30) :**
  - `research run` refuse des données commençant avant le 01/10/2015.
  - L'historique d'avant 2016 n'est chargé qu'**après le verdict** de l'exécution, et seulement s'il y a des candidats.
  - La période est ouverte **une seule fois**, pour les cellules classées CANDIDAT : `research unseal --prereg research/prereg/2026-10-28_reg3.yaml --candidates <fichier figé> --universe all --from 2006-01-01 --to 2016-01-31`.
  - Verdict : A2 complet sur la période, et même signe qu'en découverte.

**Suivi en ombre.** `app/services/shadow.py` sait suivre C1 / C2 (`plan=PLAN_REG3`). Ce suivi n'est pas encore branché dans la tâche quotidienne, car il demande une mise à jour régulière des publications.

**Limites propres à ce plan :**
- historique des publications chargé après coup (Yahoo ; EODHD hors forfait, §34.8) ;
- date de publication supposée connue à l'avance pour t15 et pour la fenêtre de t16 ;
- séance de réaction déduite de l'heure déclarée par la source ;
- placebo commun à tous les groupes.

Aucun résultat ne change un paramètre ni un détecteur sans ADR.

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
7. **Exécuter l'enregistrement n° 3.**
   - `research calibrate --prereg research/prereg/2026-10-28_reg3.yaml --run N`.
   - Puis `research run --prereg research/prereg/2026-10-28_reg3.yaml --universe all --run N`.
   - Lire d'abord le placebo X1, puis C1/C2, puis le contrôle X4.
8. **Seulement pour les cellules retenues.**
   - Charger l'historique européen ancien : `backfill --from 2005-01-01` pour l'Europe, puis `earnings-history --from 2005-01-01`.
   - Puis la réplication scellée 2007-2015, en un seul descellement.

**En une fois :** `powershell -ExecutionPolicy Bypass -File scripts\recherche_longue.ps1` enchaîne les opérations dans l'ordre :
1. backtest européen de référence ;
2. calibration puis exécution n° 2 ;
3. `earnings-history` ;
4. `moves-study` ;
5. calibration puis exécution n° 3.

Il dure plusieurs heures. Tout est journalisé dans `data/research/longue_<horodatage>.log`, et une exécution n'est lancée que si sa calibration est VALIDE.

## 34.8 Sources de données vérifiées (28/10/2026)

Recherche documentaire. EODHD est lu dans sa documentation officielle publiée sur GitHub ; les autres sites étaient bloqués par la session, leurs pages sont donc à confirmer.

- **Résultats (BPA publié et estimé).**
  - Ton forfait EODHD « EOD+Intraday — All World Extended » n'inclut **ni** l'API calendrier, **ni** les fondamentaux : `earnings-history` recevra un 403 et basculera sur Yahoo, comme prévu.
  - Option payante : « Calendar Feed » à +19,99 $/mois. Elle n'est pas datée à l'époque pour l'Europe (mise à jour « la semaine suivante ») : chaque donnée doit garder son `received_at`.
  - Yahoo (`yfinance` 1.7, lecture de pages HTML depuis l'été 2025) est fragile et peut répondre 429. La couverture européenne sera lue dans la sortie `coverage` d'`earnings-history`.
- **Achats de dirigeants.**
  - France : pas d'API AMF ; les déclarations BDIF sont des PDF, avec un flux RSS. Il faut un archivage quotidien par l'outil, avec la date de publication comme date de disponibilité.
  - Pas d'export utilisable trouvé pour BaFin (12 mois glissants), l'AFM, la CNMV, l'Italie ou le Royaume-Uni.
  - Le jeu de données data.gouv n'est plus à jour depuis 2019.
  - **Archivage codé le 29/10/2026** (tâche `insider_archive`, commande `insider-archive`, table `insider_declarations`). Source : la liste JSON publique que le site BDIF appelle lui-même, sans clé — `https://bdif.amf-france.org/back/api/v1/informations?TypesInformation=DD&DateDebut=<ISO UTC>&DateFin=<ISO UTC>&From=<rang>&Size=<n>`. Chaque élément de `result` donne `numero` (`2026DD…`), `datePublication` (avec fuseau), `dateInformation`, `societes[].raisonSociale` et `societes[].jeton` (`RS…`), `documents[].path`. Le PDF se lit à `https://bdif.amf-france.org/back/api/v1/documents/<path>`. La liste ne donne **aucun chiffre** : fonction, nature, date, prix unitaire, volume et lieu sont dans le PDF (formulaire AMF). Ils sont lus avec `pypdf` (dépendance ajoutée le 29/10/2026) ; sans lui, seule l'adresse serait gardée. Pagination limitée à 10 000 éléments, d'où des fenêtres de 31 jours. Conservation annoncée par la BDIF : 5 ans. Flux RSS général : `…/back/api/v1/rss?lang=fr` (non utilisé). **À confirmer en direct** : adresses et champs relevés dans des codes publics qui les utilisent, le site étant bloqué par la session ; `robots.txt` non lu. Date de disponibilité retenue : `datePublication`. Nom du déclarant jamais conservé, seulement sa fonction.
- **Objectifs et recommandations d'analystes :** aucune source abordable avec un historique européen daté. Seul un **archivage vers l'avant** est fiable.
- **Temps réel.**
  - Saxo Level 1 : environ 7 €/mois pour Euronext, 7 € pour Xetra (grille Saxo France à confirmer).
  - L'abonnement **ne suffit pas**. Il faut aussi passer la session OpenAPI du niveau `OrdersOnly` à `FullTradingAndChat`. Ce changement peut déconnecter SaxoTraderGO ou le faire passer en différé.
  - Malgré son nom, ce niveau ne passe aucun ordre. La règle 1 reste tenue : aucun endpoint d'ordre, test `test_no_trading_endpoints`. Le changement demande quand même un **ADR** avant d'être codé.
  - EODHD propose depuis le 26/08/2026 un flux WebSocket européen (`eu`). Sa source est Cboe Europe : ce n'est pas le marché principal, il n'a pas de fixing et son volume est partiel. Son accès avec ton forfait est à tester. Il pourrait servir de second avis affiché avec sa source, jamais mélangé au périmètre principal (`data.perimeter_consistency_required`).

## 34.9 Décisions à prendre

1. ~~Abonnement Saxo Euronext L1~~ — **décidé le 28/10/2026 : pas avant la reprise des investissements.** Il sera pris quand l'outil sera finalisé et que l'utilisateur passera au moins 4 ordres par mois. D'ici là, les entrées repérées en séance restent « à vérifier ». Un ADR (niveau de session OpenAPI) sera rédigé à ce moment-là.
2. **Compte des allers-retours courts.** Recommandation : les faire en CTO Saxo sur les valeurs de la zone euro hors France, et pas dans le PEA Découverte (0,2 R de frais par aller-retour). Le moteur choisit déjà le compte au meilleur ratio net ; c'est une règle de conduite à confirmer.
3. **Après le verdict n° 3 :**
   - Si C1 ou C2 passent : un détecteur « réaction aux résultats » en production, actionnable sans phase de surveillance, et un ADR.
   - Sinon : archivage quotidien des consensus d'analystes, et collecte des déclarations de dirigeants (AMF BDIF), pour un enregistrement n° 4 vers l'avant.

## 34.12 Enregistrement n° 5 : achats des dirigeants

> Plan `research/prereg/2026-10-30_reg5.yaml`, empreinte sha256 `5d1e4a1df0e194dad8cc6176b2356737192267b6be81639fc4a87bc9ee512d3d`.
> - **Gelé le 29/10/2026 au soir par l'assistant**, sur délégation de l'utilisateur du 29/10/2026 au soir, **avant tout chargement de l'historique des déclarations** (`insider-archive --from 2021-11-01` est lancé ensuite sur le PC) et avant toute lecture d'un résultat lié à ces déclarations.
> - L'empreinte est inscrite au registre (`data/research/ledger.csv`) à chaque calibration et exécution ; un test (`tests/unit/test_research_insiders.py`) vérifie que le fichier a toujours cette empreinte. Le modifier crée un nouvel enregistrement.
> - Hypothèse tirée de la littérature, pas des données : après un achat en bourse d'un dirigeant d'un émetteur français (MAR art. 19), rendement anormal positif sur 10 à 20 séances (Fidrmuc, Goergen & Renneboog 2006 ; Betzer & Theissen 2009 ; Fidrmuc, Korczak & Korczak 2013), plus faible depuis MAR 2016 (Grimberg & Herberger 2020). Graine 20261030.
> - Seuils A1–A13, inférence et hypothèses communes repris à l'identique du n° 3.

**Déclencheur t17** (`app/domain/research/insider_triggers.py`). Une déclaration de la table `insider_declarations` est retenue quand toutes ces conditions sont réunies :
- PDF analysé (`parse_status = parsed`) ;
- ISIN de la déclaration, sinon celui de la correspondance enregistrée par nom ;
- nature « acquisition » (« cession » pour le contrôle des ventes ; « mixte » jamais) ;
- fonction du déclarant dans la liste fermée des dirigeants exécutifs, sous-liste de `amf_bdif.ROLES` : PDG, directeur général, directeur général délégué, président du directoire, membre du directoire. Président du conseil, conseil de surveillance, administrateur, gérant, directeur financier, président, autre dirigeant et personne liée ne comptent que dans X5 ;
- chaque transaction porte sur une action, sans libellé exclu (options, actions gratuites, souscriptions, exercices, plans d'épargne, prêts, donations…) et sur une plateforme de négociation (hors plateforme, lieu inconnu ou non reconnu → exclue) ;
- prix et volume lus, en euros (devise écrite, ou absente pour une valeur cotée en euros), et prix × volume ≥ 10 000 €. **Inconnu → exclue et comptée, jamais estimée.**

**Placement.**
- Le signal tombe sur la première séance de la valeur à partir de la publication AMF (`market_timestamp`) : le jour même si elle est publiée au plus tard à 17:30 (heure de Paris), sinon la séance suivante (après la clôture, week-end, jour sans cotation de la valeur).
- Une séance à plus de 7 jours de la publication n'est pas un placement (suspension, valeur pas encore cotée).
- Un prix lu à plus d'un facteur 2 de la clôture de la séance est une erreur de lecture : exclu, compté.
- Un signal par valeur toutes les 20 séances (le premier, avant l'éligibilité, sans regarder le résultat).
- Valeurs cotées à Paris seulement (groupe PA du plan, jugé sur le CAC 40), éligibles ce jour-là.
- Le signal est connu à la clôture de sa séance ; l'ordre ne travaille qu'à partir de l'ouverture suivante : l'entrée est toujours postérieure à la publication.

**Cellules** (11 au total, Holm sur 2 pour les confirmatoires) :

| Cellule | Rôle | Contenu | Sortie |
|---|---|---|---|
| C1 | confirmatoire | achat d'un dirigeant exécutif ≥ 10 000 € | 20 séances |
| C2 | confirmatoire | le même | 10 séances |
| X1 | exploratoire | placebo (valeurs et dates de Paris tirées au hasard, taux 0,002 fixé avant toute donnée), **lu avant C1 / C2** | 10 et 20 séances |
| X2 | contrôle | **cessions** des dirigeants exécutifs, mêmes filtres (attendu ≤ 0) | 20 séances |
| X3 | contrôle | les signaux de C1 décalés de 20 séances (attendu ≈ 0) | 20 séances |
| X4 | exploratoire | achats groupés : au moins 2 fonctions exécutives distinctes en 10 séances (les noms n'étant jamais conservés, deux personnes de même fonction comptent pour une) | 20 séances |
| X5 | exploratoire | achats de toute fonction, mêmes filtres | 20 séances |

- **Entrée et stop :** ouverture suivante plafonnée à la clôture + 1 ATR, validité d'une séance (E_open(1)), stop initial standard du banc (X3 : 3 σ √h), sortie au temps.
- **Comptes :** verdict sur le CTO ; le PEA est la seconde vue des coûts.
- Les cellules exploratoires sont descriptives : sans année d'apprentissage avant le premier pli, aucune ne peut être CANDIDAT ni SURVEILLANCE.

**Couverture et abstention.**
- L'en-tête du rapport et le `.txt` impriment la couverture de l'archive : déclarations chargées par mois, taux d'analyse des PDF, achats, achats de dirigeants, déclarations retenues, l'entonnoir des exclusions, l'origine de l'ISIN, le placement et les mois sans aucune déclaration (trou de l'archive).
- Une cellule confirmatoire sous A1 (n ≥ 300, ≥ 100 semaines, ≥ 40 trades par pli) est déclarée **« non concluant »**, jamais « pas d'avantage ». Quand C1 et C2 le sont toutes deux, le titre commence par « NON CONCLUANT ».

**Période et limites.**
- **Découverte (2021-11-01 → 2026-10-29) :** la BDIF annonce 5 ans de conservation ; tout est test, puisque rien n'a été choisi sur ces données. 5 plis d'un an.
- **France seule.** La table des instruments est celle d'aujourd'hui : un émetteur radié ou renommé depuis est « hors panneau » (biais de survie, compté).
- **Aucune période scellée n'existe** pour ces données. Le verdict de ce plan peut au mieux fonder un **suivi en ombre à 50 % du risque par trade**, jamais un détecteur actionnable.
- **Confirmation vers l'avant** (clé `forward_confirmation` du plan) :
  - signaux publiés à partir du 2026-10-30, jamais lus par `research run` ;
  - ouverture **unique** par `research unseal`, avec le fichier de candidats figé ;
  - refusée tant que les données n'atteignent pas le verdict (première exécution `run` du plan au registre) + 12 mois de signaux + leurs sorties (65 séances), et tant que le suivi en ombre n'a pas enregistré 100 signaux de C1 ;
  - au-delà de 36 mois sans ces 100 signaux : « non concluant », sans ouverture ;
  - verdict avec les mêmes seuils que les descellements des n° 1 à 3 : A2 complet (R moyen ≥ +0,10, ≥ +30 € pour 300 € de risque, facteur de profit ≥ 1,15, % du notionnel > 0) et même signe qu'en découverte ;
  - confirmé → ADR de mise en production à 50 % de `risk.max_risk_per_trade_pct`, avec règle de suspension ; sinon rien ne change.
- **Suivi en ombre :** `shadow_signals` enregistre C1 chaque matin depuis le gel (`app/services/shadow.py`, plan `PLAN_REG5`), sur les colonnes parisiennes de P0. Une déclaration analysée après le passage du lendemain matin manque à la série en ombre (pas au descellement, qui relit l'archive).

**Sur le PC (une commande à la fois) :**
1. `git pull`, puis `docker compose up -d --build` (aucune migration nouvelle : table de la 0029).
2. `docker compose exec app python -m app.cli insider-archive --from 2021-11-01`
   - environ une requête par seconde : prévoir une nuit ou plus ;
   - relançable sans perte : une déclaration déjà analysée n'est pas relue.
   - Me transmettre la couverture (`insider-archive --coverage-only`).
3. `research calibrate --prereg research/prereg/2026-10-30_reg5.yaml --run N`.
4. `research run --prereg research/prereg/2026-10-30_reg5.yaml --universe all --from 2020-06-01 --run N`. Lire d'abord le placebo X1, puis C1 / C2, puis les contrôles X2 et X3.

Aucun résultat ne change un paramètre ni un détecteur sans ADR.
