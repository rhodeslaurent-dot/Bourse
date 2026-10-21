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
