# 24 — Comité d'investissement multi-agents (proposition)

> Demande de l'utilisateur (25/10/2026) : faire challenger les étapes majeures de la sélection — analyse des informations d'entrée, sélection des opportunités, timing et volume — par des analyses et discussions multi-agents, chaque agent spécialisé sur un aspect de la décision, pour calibrer le raisonnement, le ratio risque / investissement et la maximisation du gain.
> Statut : **proposition**, à valider avant tout code. Version 2 après contre-expertise (§24.12).

## 24.1 Principe directeur : le comité discute, Python décide des chiffres, vous décidez de l'ordre

Le comité ne remplace aucune règle absolue de CLAUDE.md :

- **Règle 3 (le LLM ne calcule pas)** : les agents ne reçoivent que des **faits numérotés calculés en Python** (cours, niveaux, taille, coûts, impôts, consensus, positions courtes, régime…). Ils argumentent, classent, objectent ; ils ne produisent **aucun chiffre** qui ne figure pas dans le dossier. Un garde-fou Python rejette toute affirmation contenant un nombre absent des faits (le même que pour les thèses, docs/22 §22.8).
- **Sorties bornées** : le comité ne peut que **maintenir, réduire ou différer** une proposition — jamais augmenter la taille, élargir un stop, relever un plafond (R21 reste une décision humaine).
- **Règle 10 (instantané)** : le dossier transmis, les mémos de chaque agent et le verdict sont stockés avec la proposition ; les post-mortems se font sur cet instantané.
- **Règle 1** : aucun ordre n'est passé ; le verdict est une aide, la décision reste la vôtre.

## 24.2 Les agents (8 rôles)

| # | Agent | Spécialité | Faits reçus (calculés en Python) | Question à laquelle il répond |
|---|---|---|---|---|
| A1 | **Contrôleur des données** | qualité des entrées | fraîcheur et statut de chaque donnée, écarts entre sources (Yahoo / TradingView, cours à l'analyse), données manquantes | « Le dossier est-il assez fiable pour décider ? » — peut **suspendre** (« à vérifier ») |
| A2 | **Analyste technique (micro)** | prix, volumes, structure | détecteur, niveaux, zones, force relative, volume relatif, distance au stop en ATR, extension | « La configuration est-elle propre et le timing bon ? » |
| A3 | **Analyste fondamental** | qualité, valorisation, révisions | croissance, marges, dette, PER vs secteur, consensus et sa dispersion, révisions, résultats à venir | « L'entreprise justifie-t-elle le potentiel visé ? » |
| A4 | **Stratégiste macro et secteur** | régime, taux, devises, rotation | portail de marché, risque conjoncturel, courbe des taux, calendrier macro, force du secteur, corrélation à l'indice | « Le contexte porte-t-il ou contrarie-t-il ce trade ? » |
| A5 | **Analyste sentiment et flux** | signaux faibles publics | médias (citations, pression médiatique), lettres, dirigeants, positions courtes, rachats, signaux faibles (docs/24 §24.9) | « Qui achète, qui vend, et le consensus est-il déjà dans le prix ? » |
| A6 | **Gestionnaire de risque** | taille, stop, portefeuille | risque en € et en R, plafonds R8/R9, risque ouvert, concentration secteur / devise / corrélation, événements non couverts par le stop (R14), liquidité | « Le risque est-il juste, et que se passe-t-il dans le pire scénario ? » — peut **réduire** |
| A7 | **Analyste fiscal et coûts** | compte, coût complet | comparaison PEA / CTO / SRD au coût complet, TTF, change, impôt, portage SRD, ancienneté du PEA, poche fiscale | « Est-ce le bon compte, et le gain net justifie-t-il les frictions ? » |
| A8 | **Avocat du diable** | contradiction | tout le dossier + les mémos A2–A7 | « Pourquoi ce trade va-t-il échouer ? » — construit le **scénario baissier** le plus solide |
| P | **Président du comité** | arbitrage | dossier + tous les mémos + objections et réponses | rend le **verdict borné** (§24.5) et consigne les dissidences |

Pourquoi ces 8 et pas plus : chaque rôle couvre une source d'erreur distincte et documentée (données fausses, mauvais timing, valorisation, contexte, foule, taille, frictions, biais de confirmation). Un agent « momentum » distinct serait redondant avec A2 ; un agent « ESG » sort du mandat. Le contrôleur des données passe **en premier** : un dossier douteux ne mobilise pas le comité.

## 24.3 Quand le comité se réunit (4 étapes de la décision)

| Étape | Déclencheur | Agents | Sortie |
|---|---|---|---|
| **E1 — Entrées** | chaque scan (07:50) | A1 seul, **par règles Python** (pas de LLM) ; LLM seulement pour expliquer une incohérence | dossiers marqués « fiable / à vérifier » |
| **E2 — Sélection** | les `committee.top_n` (5) premiers BUY du classement, et toute proposition à dérogation R21 | A2 → A7 en parallèle, puis A8, puis P | verdict par proposition |
| **E3 — Timing et volume** | inclus dans E2 (A2 timing, A6 taille, A7 compte) ; re-convoqué si le cours atteint la limite plus de 2 séances après le verdict | idem, mémo court | « maintenir / attendre / réduire » |
| **E4 — Portefeuille** | samedi (revue de portefeuille) | A3, A4, A5, A6, A7, A8, P sur les **positions** et la poche fiscale | alléger / conserver / vendre (opinion), arbitrages fiscaux |

## 24.4 Protocole de délibération (3 tours)

1. **Tour 1 — mémos indépendants** (en parallèle, sans voir les autres) : chaque agent A2–A7 rend un mémo validé (Pydantic) : `avis` ∈ {favorable, réservé, défavorable}, `conviction` 1–5, 3 arguments au plus **chacun relié à des numéros de faits**, 1 risque principal, `condition_d_invalidation`, `probabilité_objectif_avant_stop` en **classe** (moins de 40 %, 40–55 %, 55–70 %, plus de 70 %) — une estimation subjective, jamais un calcul.
   *L'indépendance du tour 1 limite l'effet moutonnier et l'ancrage sur le premier avis.*
2. **Tour 2 — contradiction** : A8 reçoit les 6 mémos et produit les 3 objections les plus fortes ; chaque agent visé répond en une phrase (maintient / révise son avis, avec le fait qui le justifie).
3. **Tour 3 — arbitrage** : P lit tout et rend le verdict borné. Il doit **citer** la dissidence la plus forte et dire pourquoi il la suit ou non.

Garde-fous de délibération : un mémo sans fait cité est écarté ; un nombre absent du dossier invalide l'argument ; un agent ne voit jamais les décisions passées de l'utilisateur sur la valeur (pas d'ancrage sur vos choix).

## 24.5 Verdict borné et règle d'agrégation (Python)

Le président choisit dans une liste fermée ; Python applique :

| Verdict | Effet appliqué par Python | Condition |
|---|---|---|
| `maintenir` | proposition inchangée | — |
| `reduire_un_cran` | taille × `committee.reduce_step` (0,75) | aucune |
| `reduire_deux_crans` | taille × 0,5 | au moins 2 agents défavorables ou A6 défavorable |
| `attendre_repli` | BUY → WATCH, limite conservée | A2 ou A4 défavorable sur le timing |
| `attendre_evenement` | BUY → WATCH jusqu'après l'annonce | événement R14 cité par A6 ou A4 |
| `rejeter` | BUY → NO_TRADE (motif affiché) | A1 « à vérifier » non levé, ou ≥ 4 défavorables |

**Unanimité favorable** : aucun effet sur la taille (pas de bonus) — la confiance affichée passe à 5/5. Les verdicts plus prudents que les conditions sont toujours acceptés ; un verdict moins prudent que les conditions est **remplacé** par le plus prudent autorisé (le comité peut être plus prudent que Python, jamais moins).

## 24.6 Mesure et calibrage (apprentissage, docs/18)

- Chaque agent est **noté** sur ses classes de probabilité : score de Brier par agent et par famille de signal, une fois l'issue connue (objectif atteint, stop touché, expiration).
- Tableau sur `/apprentissage` : fiabilité par agent, « dissidence utile » (part des rejets / réductions qui ont évité une perte), coût du comité par proposition et gain net attribué.
- **Aucune pondération automatique** : si un agent se révèle mal calibré, la proposition de le repondérer ou de reformuler son rôle passe par un ADR, comme tout paramètre.

## 24.7 Coût et modèles

Hypothèses : dossier commun ≈ 6 000 jetons ; mémo ≈ 400 jetons ; 5 propositions par jour.

| Poste | Modèle proposé | Estimation par proposition |
|---|---|---|
| A2–A7 tour 1, réponses tour 2, A8 | `claude-haiku-5-5` (0,10 $ / 0,50 $ par million de jetons en entrée / sortie) | ≈ 13 appels × 7 500 jetons en entrée (tokeniseur +30 % inclus) ≈ 0,010 $ + 5 000 jetons en sortie ≈ 0,003 $ |
| P (arbitrage) | `claude-sonnet-5-5` (2 $ / 10 $) | ≈ 10 000 jetons en entrée ≈ 0,02 $ + 800 en sortie ≈ 0,008 $ |
| **Total** | | **≈ 0,04 $ par proposition, ≈ 0,20 $ par jour** (≈ 4 $ par mois) |

Le **dossier commun est placé en cache de prompt** (préfixe identique pour les 7 agents) : seules les instructions de rôle changent ; le cache réduit encore l'entrée. Plafond dédié `committee.daily_budget_usd` (0,50 $) ; au-delà, le comité ne se réunit pas et la fiche le dit (jamais de verdict inventé).

Prérequis : les modèles de la génération 5 **refusent un réglage de température** ; la règle 3 (« température 0 ») devra être reformulée par ADR (déterminisme assuré par la sortie validée, le cache par empreinte et le garde-fou des nombres). Voir docs/25 §25.4.

## 24.8 Interface

- Fiche d'opportunité : section **« Comité »** — verdict, effet appliqué, tableau des 6 avis (favorable / réservé / défavorable, conviction), objections de l'avocat du diable, dissidence citée par le président, coût de la délibération.
- Telegram : le verdict tient sur une ligne du message existant (« Comité : maintenir 5/6 · dissidence risque : résultats le 30/10 »).
- `/apprentissage` : calibrage des agents (§24.6).

## 24.9 Agent de signaux faibles (alimente A5)

Conception détaillée en docs/26 (sources qualifiées, calculs, garde-fous anti-manipulation). Rôle dans le comité : fournir à A5 des faits Python (« volume de messages ×4 sur 5 jours vs 60 jours », « pic de consultations Wikipédia », « citations médias ») et, en dehors du comité, **générer des idées** (valeurs hors classement qui deviennent intéressantes) soumises au moteur normal — jamais un achat direct.

## 24.10 Critères d'acceptation

- **A24.1** Aucun verdict ne peut augmenter une taille, élargir un stop ou lever un blocage (test).
- **A24.2** Un mémo contenant un chiffre absent du dossier est rejeté (test du garde-fou).
- **A24.3** Le dossier, les mémos et le verdict sont dans l'instantané de décision.
- **A24.4** Budget atteint → « comité non réuni (budget) », proposition inchangée.
- **A24.5** Le score de Brier de chaque agent est calculé à l'issue de chaque proposition.

## 24.11 Lots proposés

| Lot | Contenu | Pré-requis |
|---|---|---|
| C1 | E1 par règles (contrôleur des données) + dossier commun numéroté (réutilise les faits de la thèse) | — |
| C2 | Tours 1–3 sur les 5 premiers BUY, verdict borné, section « Comité » de la fiche | ADR modèles 5.x / température |
| C3 | Calibrage (Brier par agent), tableau `/apprentissage` | 2 mois de propositions |
| C4 | E4 portefeuille du samedi | C2 |

## 24.12 Contre-expertise

Version soumise à des contre-expertises indépendantes le 25/10/2026 ; leurs objections et les corrections retenues sont consignées en docs/25 §25.6.
