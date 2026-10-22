# 07 — Gestion du risque, dimensionnement, stops, portes de discipline

Tout est paramétré dans `config/params.yaml › risk.*` ; toute proposition qui viole une règle est affichée `BLOQUÉ : <règle>` (jamais masquée : l'utilisateur doit voir pourquoi). Ce document est la **référence** pour toute règle de risque ; en cas de divergence avec un autre document, il prime (après `params.yaml`).

## 7.1 Capital et risque par trade (règles du projet actuel, conservées)

- `capital.capital_pilote_eur` (modifiable dans `/parametres`, historisé) ; `capital.cash_additionnel_max_eur` mobilisable pour les opportunités exceptionnelles (ratio de score ≥ `scoring.exceptional_min_ratio`, confiance ≥ 4, risque faible) — toute proposition utilisant ce cash le mentionne explicitement.
- **Base de capital** : le risque par trade et les plafonds sont calculés sur le capital pilote, pas sur la valeur liquidative du jour (`risk.capital_base: pilote`). Le capital pilote est revu manuellement, jamais recalculé automatiquement après gains ou pertes.
- **Risque par trade = 0,75 % du capital pilote** (`risk.max_risk_per_trade_pct`), soit 300 € pour 40 000 €.

## 7.2 Dimensionnement (sur le prix maximal d'entrée)

```
prix_max        = prix limite de l'ordre d'entrée (non dépassable par construction : ordre limite ou à plage)
                  ; pour une entrée au marché / à seuil simple (D1 intraday), prix_max = niveau × (1 + risk.slippage_pct)
stop_exec       = stop_initial × (1 − risk.slippage_pct)          # sortie sur stop à seuil : exécution sous le seuil
frais_estimés   = courtage aller + courtage retour + TTF éventuelle, calculés sur le notionnel de la taille
                  candidate et itérés jusqu'au point fixe (≤ 3 itérations : frais → taille → frais) ;
                  la CRD, qui dépend de la durée, entre dans le coût d'horizon (docs/08) et la comparaison
                  des comptes, pas dans le dimensionnement
R_par_action    = (prix_max − stop_exec) × taux_de_change_EUR × (1 + risk.fx_haircut si devise ≠ EUR)
                  # la marge de change augmente le R par action, donc RÉDUIT la quantité ; le budget (300 €) ne bouge pas
shares          = floor((risque_eur − frais_estimés) / R_par_action)
plafonds        : shares × prix_max ≤ risk.max_position_pct_of_capital × capital
                  shares × prix_max ≤ risk.max_position_pct_of_adv × ADV20_eur
```

Convention de glissement (`risk.slippage_pct`, 0,2 %) : elle s'applique **toujours** à la sortie sur stop à seuil (`stop_exec`) et à l'entrée **seulement** si l'ordre d'entrée ne borne pas le prix (marché, à seuil simple). Un ordre limite ou à plage de déclenchement n'a pas de glissement d'entrée : son prix limite est le prix max.

- Le **prix maximal d'entrée** est affiché sur toute fiche d'ordre ; au-delà, l'ordre ne doit pas être passé (la fiche le dit).
- Après exécution (partielle ou différente du scénario), le risque réel est **recalculé** sur le prix exécuté et la quantité détenue ; si le risque réel dépasse `risque_eur × 1,1`, l'outil propose un allègement ou un stop ajusté (jamais abaissé sous le stop structurel).
- Multiplicateurs : régime orange × 0,5 ; risque élevé (capitalisation < `universe.small_cap_threshold_eur`, gap ≥ 15 %, ADV < 1 M€) × 0,5 avec stop ≤ 1,5 ADR obligatoire.
- **Statut de donnée** : une proposition d'entrée intraday exige une cotation dont l'horodatage de marché a moins de `data.buy_intraday_max_market_age_minutes` (3 min) — en pratique du temps réel. Une donnée différée sert à détecter des candidats et préparer des scénarios (« à vérifier »), jamais à émettre une fiche d'ordre. Un ordre préparé pour J+1 (D2/D3/D4, à plage de déclenchement) est calculé sur la clôture EOD officielle (ou provisoire, marquée comme telle).
- Les exemples chiffrés de docs/09 §9.1 sont des **tests de référence** (`tests/unit/test_sizing_examples.py`).

## 7.3 Plafonds de portefeuille (calculés, pas déclaratifs)

| Plafond | Paramètre | Règle |
|---|---|---|
| Risque ouvert cumulé | `risk.max_open_risk_pct` (4 %) | `risque_courant_total + risque_réservé ≤ 4 % × capital pilote`, avec les définitions déterministes ci-dessous. Au-delà : BLOQUÉ. |
| Risque par secteur | `risk.max_sector_risk_pct` (2 %) | Même calcul par secteur (niveau 1) ; ≤ 2 positions par secteur (`risk.max_positions_per_sector`, 3 en régime vert si ratio d'admission ≥ `risk.sector_third_position_min_ratio` = 0,80) |
| Risque par facteur | `risk.max_factor_risk_pct` (3 %) | Groupes de corrélation configurables (ex. « défense », « semi-conducteurs », « luxe ») |
| Scénario de gap | `risk.gap_scenario_pct` (−10 %) | Perte simulée si toutes les positions ouvrent à −10 % sous le stop : affichée chaque soir ; alerte si > 2 × risque ouvert cumulé |
| Exposition | `risk.exposure.green/orange/red` | ≤ 80 % en vert, ≤ 50 % en orange, gel en rouge. **60 % et 5 positions sont des repères de diversification, jamais des objectifs** : l'outil n'incite jamais à investir faute de meilleurs signaux. |
| Nombre de positions | `risk.positions_max` (15) | Plafond ; pas de minimum |
| Levier SRD | `risk.srd_max_leverage` (2,0 ; absolu 2,5) | Notionnel SRD / valeur liquidative CTO ; couverture disponible ≥ 1,5 × requise (alerte) / ≥ 1,3 × (blocage) ; réserve de cash CTO ≥ 20 % |
| Devises | `risk.max_fx_exposure_pct` (30 %) | Exposition hors EUR / exposition totale |
| Place | `risk.max_exposure_per_non_paris_market_pct` (40 %) | Par place hors Paris |
| Drawdown | `risk.circuit_breaker` | Pertes **réalisées + latentes** de la semaine ≤ 3 R et du mois ≤ 6 R ; au-delà, pas de nouvelle entrée jusqu'au lundi / mois suivant, tailles × 0,5 la semaine de reprise |

Définitions (toutes en EUR, testées) :

- **Risque initial** d'une position = `qty_exécutée × (prix_exécuté − stop_exec_initial) + frais d'entrée + frais de sortie estimés`. Référence figée à l'ouverture ; sert au calcul des R réalisés, jamais aux plafonds courants.
- **Risque courant** d'une position = `max(0, qty_détenue × (prix_courant − stop_exec_courant)) + frais de sortie estimés` ; `prix_courant` = dernière cotation valide (ou clôture si hors séance) ; **borné à zéro par position** : une position dont le stop est au-dessus du prix courant… n'existe pas (le seuil est franchi), et une position gagnante avec stop remonté au-dessus du prix d'entrée contribue au plus ses frais de sortie, jamais un montant négatif qui compenserait une autre position. Quantité non couverte par un stop → risque courant = `qty_non_couverte × prix_courant` (perte totale possible, affichée en rouge).
- **Risque réservé** d'un ordre d'entrée en attente = `qty_non_exécutée × (prix_max − stop_exec_prévu) + frais`.
- **Risque cumulé** = Σ risques courants + Σ risques réservés. Le plafond de 4 % s'applique à ce total ; le scénario de gap (`gap_scenario_pct`) recalcule la perte si toutes les positions ouvrent à −10 % sous leur stop.
- **Drawdown** : (a) drawdown d'équité = équité / plus haut d'équité − 1, sur une équité TWR (versements et retraits neutralisés) ; (b) pour le coupe-circuit, pertes en R de la période (semaine / mois civils) = Σ R réalisés + Σ variation de R latent depuis le début de la période. Les deux sont affichés, le coupe-circuit n'utilise que (b).
- **Frais** : entrée déjà payée (incluse dans le PMP), sortie estimée (courtage + éventuelle CRD courue) comptée dans le risque courant.

## 7.4 Stops

- **Stop initial obligatoire** sur toute proposition ; jamais « à ajuster plus tard ».
- **Choix du niveau (explicite et testé)** : le stop est d'abord **structurel** — sous le plus bas de la structure de référence (plus bas du jour pour D1, plus bas des 10 séances pour D2, plus bas de la bougie de cassure pour D3, plus bas du repli pour D4) moins un tampon `risk.stop.buffer_atr_mult × ATR14` (0,25). Le stop de volatilité (`entrée − k × ATR14`) n'est utilisé **qu'en repli** si aucune structure n'est disponible. Si la distance du stop structurel dépasse `stop_max_adr_mult × ADR20` du détecteur (1,5 pour D1/D4, 2,5 pour D2/D3), le signal est **rejeté** (WATCH « stop trop large ») — on ne rapproche jamais un stop pour faire tenir la taille.
- **Type d'ordre de protection** : ordre **à seuil de déclenchement** (devient « au marché » au franchissement : exécution quasi garantie, prix non garanti) par défaut ; ordre **à plage de déclenchement** (devient limite : prix borné, exécution non garantie) seulement si la plage est ≥ 1 ADR et que l'utilisateur l'accepte explicitement. Les deux ne sont jamais traités comme équivalents ; la fiche d'ordre précise le type et ses limites (source : AMF, « choisir et passer un ordre de bourse »).
- **États d'exécution et de protection** (docs/09 §9.1, CLAUDE.md règle 9) : deux états indépendants suivis **par quantité** — exécution (`order_entered` → `filled_partial` → `filled`) et protection (`unprotected` → `partially_protected` → `protected`, `qty_protégée` vs `qty_détenue`). Toute quantité exécutée non protégée déclenche une **P1 immédiate dans tous les modes** (regroupée : une alerte, puis rappel toutes les 15 min tant que la quantité n'est pas couverte) ; le dashboard affiche en rouge toute quantité non protégée.
- **Suivi** : `position_monitor` compare le cours (temps réel) au stop courant ; **« seuil franchi »** (cours ≤ stop) et **« stop exécuté »** (confirmé par l'import courtier ou l'utilisateur) sont deux états distincts. Séquence unique (référence pour docs/06 S1 et docs/15 A1.7) : seuil franchi → **P1 immédiate** « seuil franchi — vérifier l'exécution chez le courtier » ; si aucune exécution confirmée après `risk.stop.unconfirmed_exit_minutes` (5 min) en séance → proposition **SELL au marché** (P1, même incident).
- **Stops inadaptés** : après une vente partielle, un stop portant sur l'ancienne quantité est signalé ; un stop expiré (validité jour chez certains courtiers) est rappelé chaque soir ; un stop rejeté par le courtier (hors seuils de réservation) doit être ressaisi.
- **Trailing** (S3, docs/06) : proposé le soir, jamais en baisse ; l'utilisateur confirme la modification chez le courtier, l'outil vérifie à l'import.
- **Gap sous le stop** : l'outil affiche la perte réelle attendue à l'ouverture et propose la sortie au marché ; le journal enregistre le slippage.
- **Ordres d'entrée préparés pour J+1** (D2/D3/D4 : « acheter si ≥ pivot ») : un ordre **à seuil de déclenchement** simple devient un ordre au marché au franchissement et **ne garantit pas le prix max** ; un ordre **limite** simple peut s'exécuter **avant** la cassure attendue si le cours est sous la limite à l'ouverture. Règle unique (reprise par docs/02 §2.3 et docs/15 A2.6) : (1) **entrée** : préparée comme ordre **seulement** avec un ordre à plage de déclenchement (seuil = niveau de cassure, limite = prix max ; `stop_limit` chez Saxo est l'équivalent) ou un ordre lié entrée + stop (« if-done ») — `accounts.<compte>.entry_order_types` / `linked_orders_if_done`, à confirmer par place ; sans l'un ni l'autre, l'entrée n'est **jamais** préparée comme ordre : elle devient une alerte « à saisir manuellement » pour le prochain créneau `disponible` ; (2) **protection** : ordre lié → acceptable dans tous les modes ; sinon, l'ordre d'entrée n'est proposé que si le planning prévoit le mode `disponible` sur la fenêtre d'exécution probable (ouverture), **ou** si l'utilisateur accepte explicitement le risque d'une exécution non protégée (case à cocher tracée) ; à défaut, alerte au lieu d'ordre. Dans tous les cas, « exécuté sans protection » est une P1 immédiate. Validité : jour (≤ 3 séances). Quand une proposition expire dans l'application, l'utilisateur est invité à **annuler l'ordre resté chez le courtier** (rappel jusqu'à confirmation) ; aucun changement d'état ou de mode côté outil n'est jamais réputé avoir annulé un ordre courtier. **« Exécuté sans protection » est une alerte P1 dans tous les modes** (docs/02 §2.3).

## 7.5 Portes de discipline (checklist bloquante avant fiche d'ordre)

Portes communes à tous les détecteurs :

1. Régime ≠ rouge (ou dérogation explicite tracée).
2. **Ratio d'admission du détecteur** ≥ `scoring.buy_min_ratio` (≥ `buy_min_ratio_orange` en orange) — dénominateur limité aux blocs applicables à la stratégie (docs/06 §6.5 : le bloc catalyseurs n'entre au dénominateur que pour D1) ; confiance ≥ `scoring.buy_min_confidence`.
3. Pas de publication de résultats dans les 48 h à venir (source docs/04 ; date inconnue → réserve affichée, confiance −1).
4. Taille calculée sur le prix max et le stop glissé, risque ≤ 300 €, plafonds de 7.3 respectés (risque courant + réservé, secteur, facteur, exposition, levier), **état du portefeuille frais** (cash, quantités, ordres en attente datant de moins de `accounts.portfolio_state_max_age_minutes` — sinon proposition « à vérifier »), cash disponible sur le compte cible (docs/08) ; sinon mention explicite du cash additionnel.
5. Coupe-circuit non déclenché.
6. Pas d'entrée sur la même valeur dans les 5 séances suivant une sortie sur stop (« revenge trade »), sauf nouveau catalyseur fort.
7. Heure : pas de nouvelle proposition intraday après 16:45 Paris (sauf ordres préparés pour J+1) ni dans les 3 premières minutes après 09:00.
8. Donnée : horodatage de marché < 3 min pour une entrée intraday ; EOD officielle ou provisoire marquée pour un ordre préparé J+1.
9. **Disponibilité** (docs/02 §2.3) : en mode `reunion`, aucune proposition exigeant une réaction rapide ; en mode `absent`, aucune proposition.

Portes **spécifiques au détecteur** (admissibilité) :

| Détecteur | Condition d'admission | Catalyseur |
|---|---|---|
| D1 gap + catalyseur | Gap, RVOL, prix ≥ OR high, catalyseur classé positif forte/moyenne | **Obligatoire** (C ≥ `scoring.d1_min_c`) |
| D2 cassure momentum | Structure (MM50 > MM200, cours > MM50), RVOL ≥ 1,5, pivot cassé, extension limitée, consolidation préalable | Facultatif (bonus C) |
| D3 expansion de range | Bougie ≥ +4 %, volume > veille, consolidation préalable, TI65 | Facultatif |
| D4 pullback sur leader | Tendance intacte (RS ≥ 80, > MM50), repli maîtrisé (3–10 %), volume en baisse, **reprise confirmée** | **Non requis** |

Chaque porte franchie/bloquée est enregistrée avec la proposition ; le journal calcule le taux de dérogations.

## 7.6 Trois lectures d'une proposition

Pour éviter de confondre « bonne valeur » et « bon moment », chaque proposition affiche trois appréciations dérivées de la grille /100 et des portes (docs/06 §6.5) :

| Lecture | Question | Calcul |
|---|---|---|
| **Attractivité de la valeur** | Mérite-t-elle d'être surveillée ? | RS, structure, fondamentaux disponibles, secteur (T hors déclencheur + F + M) → faible / moyenne / forte |
| **Qualité de l'entrée** | Le prix actuel permet-il une entrée cohérente ? | Distance au stop en ADR, extension vs MM20, R/R à l'objectif 1 ≥ 2, fraîcheur de la donnée, qualité du déclencheur → faible / moyenne / forte |
| **Admissibilité** | Le portefeuille, les données et ma disponibilité le permettent-ils ? | Portes 7.5 → OK / BLOQUÉ (+ règle) |

Une valeur attractive avec une entrée de faible qualité va en watchlist « à l'affût » avec ses niveaux ; elle n'est jamais présentée comme BUY.

## 7.7 Gestion des positions existantes (HOLD/REDUCE)

- Une position est HOLD tant qu'aucun détecteur de sortie ne se déclenche ; l'outil ne propose jamais de renforcer une position perdante.
- Avant toute proposition de vente ou d'allègement, l'outil vérifie la **quantité réellement détenue** (dernier import ou confirmation) et le stop en place.
- Renforcement possible uniquement si gain ≥ 1 R, stop de l'ensemble ≥ prix d'entrée initial, risque total de la position ≤ 300 €, risque ouvert cumulé respecté.
- Affichés par position : R en cours, distance au stop (% et ADR), jours détenus, état de protection, coût SRD cumulé, date de liquidation, quantité détenue vs quantité couverte par le stop.

## 7.8 Risques globaux (rubrique 10 du rapport)

Calculés chaque soir et rappelés dans le brief : risque ouvert cumulé et réservé, scénario de gap, exposition par compte/devise/secteur/facteur/place, levier SRD, couverture disponible, positions non protégées, positions avec résultats < 5 jours, événements macro J+1, liquidation SRD à venir, drawdown (réalisé + latent) vs coupe-circuit.

## 7.9 Règles du référentiel de gestion (acceptées le 22/10/2026, docs/22 §22.3)

Ces règles **remplacent** les passages correspondants des §7.2 à §7.5 en cas de divergence. Les paramètres sont dans `config/params.yaml`.

### 7.9.1 Prix maximal d'entrée, ratio gain/risque net (R4, R5)

```
frais_ar         = frais aller-retour estimés du compte (courtage achat + vente, TTF, taxes étrangères, change), en fraction du prix
P_max_rr         = [(objectif_1 + admission.min_reward_risk × stop) ÷ (1 + admission.min_reward_risk)] × (1 − frais_ar)
P_max            = min(P_max_technique du détecteur, P_max_rr)
```

- Avec `admission.min_reward_risk` = 2, on retrouve la formule du référentiel : [(objectif + 2 × stop) ÷ 3] × (1 − frais).
- `objectif_1` doit être **exploitable** : niveau structurel (résistance, plus haut, gap à combler) ou objectif publié par une source, et non conditionné au franchissement préalable d'un autre niveau. Un multiple de R n'est jamais un objectif exploitable.
- Aucune exception : un cours au-dessus de `P_max`, même de 0,26 %, donne « refusé — P_max dépassé de x % ». La limite reste posée en attente d'un repli.
- **Structure intacte** (règle du 19/10) : un `P_max` atteint parce que le cours a cassé la borne basse du support (clôture), ou rendu un franchissement (clôture sous la borne basse de la zone franchie), ne crée pas d'entrée. Le statut devient `configuration_degradee`. Le prix maximal est une condition nécessaire, jamais un signal.

### 7.9.2 Stop : placement, invariant, cliquet (R1, R2, R3)

1. **Placement** : si une zone de support [bas, haut] est connue (lettre ou zone détectée, docs/06 §6.7), `stop = bas × (1 − risk.stop.wick_buffer_pct)`, avec un tampon de mèche de 0,5 %. Un stop dans la zone ou à sa borne haute est refusé. Sans zone : règle structurelle du §7.4.
2. **Invariant** : un stop ne descend jamais. Seule exception : le détachement d'un dividende, où le stop est ajusté du montant détaché. Toute demande contraire (baisser un stop, annuler un stop déclenché) est refusée et journalisée.
3. **Cliquet** : sur une clôture nette au-dessus d'un obstacle identifié, `nouveau_stop = obstacle × (1 − wick_buffer_pct)`.
4. **Priorité** : l'invariant prime sur le placement, qui prime sur le cliquet. Un cliquet qui placerait le stop dans une zone de support est écarté ; un stop existant dans une zone n'est jamais abaissé pour la quitter.

### 7.9.3 Plafonds de risque (R8, R9, R21)

| Plafond | Paramètre | Règle |
|---|---|---|
| Risque ouvert (convention B, borné à zéro par ligne) | `risk.max_open_risk_pct_by_gate` {ouvert 0,05 ; restreint 0,035} | Remplace `max_open_risk_pct` : le plafond dépend de l'état du portail de marché (§7.9.4) |
| Risque par ligne | `risk.max_line_risk_pct_of_cap` (0,30) | Le risque d'une ligne ne dépasse pas 30 % du plafond en vigueur |
| Budget réservé aux signaux frais | `risk.reserved_signal_budget_pct` (0,0075) ; `risk.reserved_budget_age_sessions` (10) | Les lignes ouvertes depuis plus de 10 séances ne peuvent pas consommer cette part du plafond |
| Pondération SRD | `risk.srd_risk_weight` (1,25) | Le risque d'une ligne SRD compte pour 1,25 fois, à cause du gap |
| Convention A (indicateur) | `risk.heat_a_soft_cap_pct` (0,075) | Σ qty × (cours − stop) : gains exposés, plafond souple affiché |
| Plafond notionnel par ligne | `risk.max_position_pct_of_capital` (0,20, décision du 22/10) | Inchangé |

**Dérogation (R21)**, uniquement sur l'accord explicite de l'utilisateur :
- **Plafonds concernés** : risque ouvert, ligne, notionnel. Le risque **par trade** n'est jamais concerné.
- **Conditions** (toutes requises) :
  - rang 1 du jour ;
  - score ≥ `risk.override.min_score` (85) ;
  - confiance ≥ `risk.override.min_confidence` (4) ;
  - ratio gain/risque net ≥ `risk.override.min_net_rr` (3).
- **Borne** : plafond × `risk.override.max_multiplier` (1,5).
- **Affichage** : la fiche reste `BLOQUÉ : <règle>` avec le dépassement exact ; le bouton « Accorder une dérogation » demande un motif.
- **Traçabilité** : la dérogation est enregistrée dans `decisions` et dans l'instantané, et expire avec la proposition. Les statistiques la suivent à part.

### 7.9.4 Portail de marché et risque conjoncturel (R10, R12)

Décision du 22/10/2026 : **option C, sans blocage absolu sur un seuil de marché**.

- **Conditions publiées (option C)** : clôture de l'indice de référence au-dessus de la dernière zone d'obstacle publiée (lettres, extraite par la revue docs/20), **et** avis de la lettre sur l'indice qui ne conseille plus d'attendre. Les niveaux et avis sont datés et sourcés ; à défaut de publication récente (`market_gate.max_age_sessions`, 5), la condition est « inconnue » et compte comme non remplie.
- **Niveau de risque conjoncturel** ∈ {faible, modéré, élevé}, calculé en Python par `domain/conjuncture` :
  - régime (docs/06 §6.2) ;
  - VIX (percentile 1 an) ;
  - écart OAT-Bund ;
  - taux 10 ans US ;
  - Brent ;
  - variation de l'indice sur 5 séances.

  Chaque composante a des seuils datés dans `conjuncture.*`. Les composantes et leur source s'affichent.
- **État du portail** :

| Conditions publiées | Risque conjoncturel | Portail | Effet |
|---|---|---|---|
| remplies | faible ou modéré | **ouvert** | Tailles normales ; plafond de risque ouvert « ouvert » |
| remplies | élevé | **restreint** | Sélection exigeante ; taille × `market_gate.restricted_size_multiplier` |
| non remplies ou inconnues | faible ou modéré | **restreint** | Idem |
| non remplies ou inconnues | élevé | **restreint +** | Seules les opportunités éligibles à la dérogation (R21) sont présentées, avec l'accord de l'utilisateur |

  Dans l'état restreint : ratio net ≥ `market_gate.restricted_min_net_rr` (2,5), score ≥ `market_gate.restricted_min_score` (75), plafond de risque ouvert « restreint ».
- **Régime rouge** : pas d'entrée automatique ; une opportunité éligible à R21 peut être présentée avec l'accord de l'utilisateur. Ce n'est plus un gel absolu. `risk.exposure.red` reste le plafond d'exposition.
- **Brent (R12)** : au-dessus de `conjuncture.brent.srd_restrict_above` (100 $), le SRD passe en mode restreint (gain net supérieur au comptant d'au moins `srd.restricted_min_net_advantage_pct`, taille × 0,5). Retour à la normale sous `srd_normal_below` (95 $) pendant `stable_sessions` (3). Le Brent compte aussi dans le risque conjoncturel.

### 7.9.5 Événements non couverts par un stop (R14)

- **Événements concernés** : résultats de la société, BCE, Fed, inflation, emploi (calendrier docs/22 §22.7).
- **Règle par défaut** : pas d'entrée avant l'événement. Pour une position, la règle de sortie s'applique **avant** l'événement si le stop est à moins de `events.stop_distance_pct` ou si le gain ne couvre pas le gap.
- **Exception** (décision du 22/10) : l'opportunité est intéressante (score ≥ seuil BUY, ratio net ≥ 2) **et** faiblement exposée. L'exposition est mesurée sur les 8 derniers événements du même type : écart d'ouverture absolu de la valeur. La perte au 75e centile depuis P_max doit être ≤ `events.max_gap_loss_r` (1,5 R). La fiche affiche « exposition à l'annonce : faible (écart médian x %, 75e centile y %) ». Avec moins de 4 événements historiques, l'exposition est inconnue et l'exception ne s'applique pas.

### 7.9.6 Cours au moment de l'analyse (R19)

- **Relevé du cours** : chaque analyse relève le cours **au moment de l'analyse** auprès de la source temps réel disponible (Saxo si l'abonnement de la place est actif ; sinon un cours différé, déclaré comme tel). Les analyses concernées : calcul d'une proposition, thèse, revue des lettres, assistant, revue de portefeuille.
- **Enregistrement** : cours, horodatage de marché, statut et source vont dans l'instantané.
- **Contrôle de vraisemblance** : un écart > `data.plausibility_max_gap_pct` entre deux sources, ou face à la dernière clôture sans nouvelle, donne « à vérifier ». Une estimation ne fonde jamais un verdict.
