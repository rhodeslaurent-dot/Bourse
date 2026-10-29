# 20 — Revue quotidienne des lettres par Claude : préconisations et validations

Statut : spécification **acceptée le 21/10/2026** (demande utilisateur : « que les éléments du mail et de l'article associé soient analysés tous les jours par Claude pour faire des préconisations ou valider des préconisations », sans filtre par objet). Complète docs/06 D5, docs/19 §19.6 et docs/18. Règles absolues de `CLAUDE.md` intégralement applicables : **le LLM lit, classe et résume ; il ne calcule ni prix, ni stop, ni taille, ni score** ; aucune donnée inventée ; aucune proposition d'ordre exécutable sans les portes de risque.

## 20.1 Deux niveaux de lecture

1. **À la réception** (`imap_poll`, inchangé) : extraction des valeurs et des niveaux écrits. **Une proposition (WATCH) n'est créée que pour une recommandation explicite** : sens « achat » ou « à surveiller ». Une simple **mention** (sens « neutre », fréquente dans les commentaires de marché type « Morning Meeting ») est conservée (`newsletter_values`) sans signal ni proposition. Un avis « vente » ne crée pas de proposition ; il est traité par la revue (§20.3) s'il concerne une position.
2. **Revue à la réception** (décision utilisateur du 21/10/2026 : « l'analyse ne peut pas se faire le soir mais à réception des mails pour que les signaux soient encore actifs ») : dans `imap_poll`, juste après l'enregistrement d'une ou plusieurs lettres nouvelles (et de leur page liée), Claude lit **ces lettres** avec le contexte de l'outil et produit des **avis par valeur** et des **validations** ; les préconisations qui en découlent partent aussitôt en **P2** (une par valeur, type et jour). Un échec (IA indisponible, plafond atteint) ne bloque pas la relève : la lettre reste « non analysée ».
3. **Rattrapage du soir** (job `letters_review`, 19:15 Paris, et bouton « Analyser maintenant ») : revue des lettres des 4 derniers jours non encore analysées, puis **digest** de la journée (P3, ≤ 12 lignes).

## 20.2 Dossier remis à Claude (construit en Python)

- Lettres : identifiant, source, objet, heure de réception (Réunion et Paris), texte de la lettre et de la page liée (statut `page_liee`), tronqués à `letters_review.max_chars_per_letter` (8 000) et `max_total_chars` (60 000) au total, les plus récentes d'abord.
- **Données de chaque valeur citée** (décision utilisateur du 21/10/2026 : « reprendre toutes les infos d'actualités et de cours disponibles pour une analyse la plus complète possible ») — pour les `letters_review.max_values` (15) valeurs les plus citées par les lettres analysées (extraction à la réception + alias dans le texte, page liée comprise), le **dossier de faits de l'assistant** (docs/17 §17.4) : identité, éligibilités, capitalisation, prochains résultats, 5 dernières clôtures avec statut, indicateurs du jour libellés (force relative, moyennes 50/200 j, plus haut 60 j, amplitude, RSI…), position et protection, propositions des 30 derniers jours avec explication et décisions, **nouvelles des 7 derniers jours** (au plus `max_news_per_value`, titre, source, classement, sens), avis des lettres des 7 derniers jours, notes de l'utilisateur ; plus, pour la lettre : **cours à la réception** (valeur, statut `realtime`/`delayed`…, horodatage de marché, source), ouverture du jour, clôture de la veille, écart d'ouverture et dérive depuis l'ouverture (`newsletter_values`). Une donnée absente est absente, jamais estimée.
- **Marché** : régime (feu et composantes), dernières clôtures des indices de référence (CAC 40, STOXX 600) stockées.
- Contexte : propositions ouvertes ou récentes (≤ `window_days` jours : identifiant, valeur, action, explication en une ligne), positions (valeur, quantité, protection, stop), watchlist active (valeur, motif), régime de marché, leçons retenues (docs/18).

## 20.3 Réponse attendue (schéma `LettersReviewOut`, température 0)

- `synthese` (≤ 1 200 caractères) : les idées de marché du jour, en français simple.
- `avis[]` : par valeur citée — nom, ISIN s'il figure dans le texte, `orientation` ∈ {positive, négative, neutre}, `explicite` (la lettre recommande-t-elle une action ?), `action_lettre` ∈ {achat, vente, surveiller, aucune}, `catalyseur` (≤ 160), `horizon` ∈ {court, moyen, long, non précisé}, `resume` (≤ 300), `citation` (extrait littéral ≤ 200), lettres sources (identifiants).
- `validations[]` : pour chaque proposition, position ou valeur de watchlist **du dossier** concernée par les lettres — `verdict` ∈ {confirme, contredit, sans avis}, `justification` (≤ 300), lettres sources.

## 20.4 Garde-fous Python (rien n'est accepté sans contrôle)

- **Citation** : doit figurer littéralement (espaces normalisés) dans le texte d'une lettre citée ; sinon l'avis est écarté.
- **Chiffres** : tout nombre de la synthèse, des résumés, catalyseurs et justifications doit figurer dans le dossier (même règle que l'assistant) ; sinon l'élément est écarté.
- **Valeur** : ISIN existant au référentiel, ou nom résolu par les alias ; sinon l'avis est conservé « valeur non identifiée » sans effet sur les propositions.
- **Validation** : la cible doit exister dans le contexte transmis ; une validation inventée est écartée.
- Les éléments écartés sont listés avec leur motif (comme la revue d'apprentissage).

## 20.5 Préconisations (calcul Python, déterministe)

Sur `window_days` jours glissants (5), par valeur identifiée :

- **À étudier — convergence positive** : avis positifs d'au moins `min_positive_sources` (2) lettres ou sources distinctes, aucun avis négatif ; mention si la valeur est déjà en watchlist, en position ou en proposition ; ce n'est **jamais** un ordre : l'entrée éventuelle reste soumise aux détecteurs, aux portes de risque et à la taille calculée (docs/07).
- **Recommandation explicite** : action « achat » ou « surveiller » explicite d'une lettre → la proposition WATCH correspondante (créée à la réception) est marquée « confirmée par la revue » ; si aucune n'existe (valeur seulement nommée dans la page liée), une proposition WATCH est créée avec les seuls niveaux écrits dans le texte.
- **Vigilance — position** : avis négatif (ou action « vente ») sur une valeur détenue → alerte **P2** « avis négatif sur position » (dédupliquée par valeur et par jour), jamais de vente automatique.
- **Contradiction** : validation « contredit » sur une proposition ouverte → la carte de l'opportunité l'affiche ; la proposition et son instantané ne sont pas modifiés (règle 10).

## 20.6 Restitution

- Page **`/lettres`** (« Revue des lettres ») : synthèse du jour, préconisations, validations (confirme / contredit) avec lettres sources, avis par valeur (source, orientation, résumé, lien vers l'article), lettres de la période (objet, source, statut de la page liée), bouton « Analyser maintenant », date au choix.
- Opportunités : badges « confirmée par N lettre(s) » / « contredite » sur les cartes concernées. Fiche valeur et assistant : avis des 7 derniers jours.
- Telegram : à la réception, **P2** pour chaque préconisation nouvelle d'une valeur (à étudier, recommandation explicite, contradiction) et pour la vigilance sur positions, dédupliquées par valeur, type et jour ; le soir, un digest **≤ 12 lignes** (P3) de la journée.
- Journée guidée : étape « Lire la revue des lettres » (soir).

**Mise à jour du 26/10/2026.** Chaque synthèse affiche **ses sources** : les lettres utilisées (source, date de réception, objet) et le nombre d'articles de la revue de presse, détaillés dans la section « Contexte macro ».

## 20.7 Paramètres (`config/params.yaml`)

```yaml
letters_review:
  effective_from: 2026-10-22
  window_days: 5
  min_positive_sources: 2
  max_chars_per_letter: 8000
  max_total_chars: 60000
  max_tokens: 4000
  max_values: 15
  max_news_per_value: 5
  telegram_digest: true
jobs:
  letters_review: {cron: "15 19 * * 1-5", misfire_grace_min: 240, market_days_only: true}
```

Groupe optionnel : absent → revue désactivée (page affichant la marche à suivre), réception inchangée.

## 20.8 Modèle de données (migration 0009)

- `letter_reviews` (ts, day, trigger, status, synthesis, item_ids json, dossier json, rejected json, cost_usd, model).
- `letter_opinions` (review_id, day, isin?, name, source, item_ids json, stance, explicit, action, catalyst, horizon, summary, quote).
- `letter_validations` (review_id, day, target_type ∈ {proposal, position, watchlist}, target_ref, isin, verdict, justification, item_ids json).

## 20.9 Critères d'acceptation

- A20.1 Une mention « neutre » ne crée ni signal ni proposition ; un « achat » explicite en crée une.
- A20.2 Une citation absente du texte, un chiffre hors dossier ou une validation sur une cible inconnue sont écartés et listés.
- A20.3 Deux sources positives sur 5 jours sans avis négatif → « à étudier » ; un avis négatif sur une position → P2 dédupliquée.
- A20.4 `/lettres` affiche synthèse, préconisations, validations et avis ; sans clé API, la page liste les lettres et indique que la revue IA est indisponible.

## 20.10 Analyse par l'outil de chaque valeur recommandée (29/10/2026)

Constat de l'utilisateur du 29/10/2026 : « les valeurs remontées par la lettre Momentum apparaissent en orange sans analyse supplémentaire : aucun intérêt ». Trois chemins créaient une proposition de lettre et un seul passait par le moteur : la réception (`ingest_mail`) créait un WATCH nu pour toute lettre non « recalculable », la revue (`_act_on_opinions`) un second WATCH nu, et le moteur ne voyait que les lettres avec achat/support/déclencheur, stop et objectif. **Remplacé** par un seul chemin :

- **Une proposition par valeur et par jour, construite par le moteur.** Toute valeur recommandée (« achat » ou « à surveiller », ISIN résolu) — lue à la réception ou relevée par la revue (§20.5, nom cité dans la page liée) — passe par le passage ciblé du moteur (`app/services/letter_analysis.py`) : à la réception (`imap_poll`, `POST /api/ingest/newsletter`), après la revue, et chaque matin (`opportunity_scan`) tant que la lettre a moins de 5 jours. La réception n'enregistre plus que la valeur, le signal et la ligne de watchlist ; une lettre **historique** (docs/19) garde son WATCH reconstitué, expiré.
- **Constructeur unique, pur et testé** (`app/domain/letter_setup.py`) — mode d'entrée en liste fermée : `achat` (prix d'achat de la lettre, ordre limite), `support` (repli sur le support de la lettre), `declencheur` (cassure : entrée = déclencheur × (1 + `letters.trigger_buffer_pct`), prix maximal technique = déclencheur × (1 + `letters.entry_max_pct_above_trigger`), obstacle à tenir en clôture = déclencheur), `marche` (niveaux sans prix d'entrée — « ouverture haussière », ligne de tendance cassée : entrée au cours de l'analyse, structure = zone de support détectée la plus proche) et `outil` (aucun niveau : comme `marche`, ou la configuration d'un détecteur qui se déclenche sur la valeur, étiquetée « lettre + détecteur » ; jamais un détecteur en surveillance seule, ADR-013).
- **Deux stops, toujours calculés et affichés** : l'invalidation de la lettre (citée) et le stop de structure de l'outil (R1, règle des détecteurs : sous la zone de support la plus proche × (1 − `risk.stop.wick_buffer_pct`), sinon plus bas des `letters.stop_lookback_low` séances − `risk.stop.buffer_atr_mult` × ATR14). `letters.stop_policy` = `letter_within_max_else_structure` : l'invalidation de la lettre est retenue si elle est à moins de `risk.stop.max_distance_adr` × ADR20 sous l'entrée, sinon le stop de structure (« stop de structure de l'outil X (invalidation de la lettre à Y, trop large : −18 %) ») ; `letter_only` et `structure_only` possibles. **Non validé par la recherche** : à mesurer (docs/31, étude `letters-study` à venir) avant tout changement de défaut par ADR.
- **Objectifs** : ceux de la lettre dans l'ordre (`objectif`, `objectif_2`, `objectif_3`), puis les résistances détectées au-dessus du dernier ; sans objectif publié, les niveaux exploitables de l'outil (résistances, objectif moyen des analystes, plus haut 52 semaines). Mention « sorties non validées par la recherche » conservée.
- **Cassure dont le prix maximal tombe sous le déclencheur** (toutes cassures, lettres et détecteurs) : jamais de limite en attente — une limite sous l'obstacle n'est pas une cassure. WATCH « ratio net < k au déclencheur (gain a / risque b = r) », avec, pour information, le ratio au **dernier** objectif ; pour une lettre, verdict `alerte_declencheur` si ce dernier passerait, `rejet` sinon (R4 retient le premier objectif). Une cassure de lettre **non franchie** est testée au déclencheur (un ordre à seuil s'exécute là, jamais dessous) : alerte au franchissement, ordre à plage de déclenchement (seuil, limite = prix maximal) après une clôture au-dessus ; le suivi en séance (`intraday_reeval`) la surveille.
- **Verdict** (liste fermée, calcul Python) : `entrer_maintenant` (cours ≤ prix maximal, admis, contrôles « cours » et « structure » satisfaits — clôture officielle pour un ordre de la séance suivante, ou temps réel frais), `fiche_fixing` (idem mais cours différé : pas d'ordre en séance, règle 2, fiche de 17:15), `alerte_declencheur`, `attendre_repli` (limite au prix maximal, modes de repli), `bloque` (règle de risque, plafond, portail, score — motif mot pour mot), `rejet` (R4 au déclencheur, objectif trop proche, pas d'objectif, stop trop large, invalidation franchie, configuration dégradée), `non_analysable` (valeur inactive ou absente du référentiel, historique < `letters.min_history_sessions`, indicateurs ou change indisponibles, erreur — jamais le silence).
- **Liste de contrôle** `details.controle` : valeur résolue · historique · cours (statut, âge de marché) · niveau de structure enregistré · objectif exploitable · ratio net ≥ 2 · stop ≤ distance maximale · structure intacte (R5) · annonce macro 30 h · résultats avant l'horizon · taille possible. « À vérifier » en découle : un échec sur « cours » ou « structure » ; la carte liste les points en échec au lieu d'une phrase fixe.
- **Restitution** : la carte des opportunités montre trois blocs — *Ce que dit la lettre* (source, heure, sens, niveaux nommés, citation vérifiée), *Ce que voit l'outil* (mode, stops, objectifs, zones, moyennes, force relative, ATR/ADR, distance au déclencheur, plus haut 52 s, résultats, nouvelles et catalyseur, radar/forum), *Verdict* (une phrase déterministe `app/services/letters_verdict.py::sentence`, le pourquoi en clair, la liste de contrôle). La même phrase figure dans les « Préconisations » de `/lettres`, dans `letters-trace` et dans l'alerte Telegram de la revue. Les cartes `rejet` et `non_analysable` vont dans le groupe replié « Pour information ».
- **Entonnoir** par lettre (`newsletter_items.parsed["funnel"]` : lues, résolues, noms non résolus, avec niveaux, recalculables R15, recommandées, analysées, propositions, verdicts, non analysables par motif) : ligne du journal de `imap_poll`, `letters-trace`, et une ligne « Lettres : … » dans le rapport du jour (`letters.report_line`).
- Paramètres : groupe optionnel daté `letters` (`config/params.example.yaml`). Absent : règle antérieure (seules les lettres recalculables au sens du code sont recalculées, sur le stop de la lettre), les autres valeurs ayant une proposition « non analysable » qui le dit. Aucune migration (tout est dans `proposals.details` et `newsletter_items.parsed`).
