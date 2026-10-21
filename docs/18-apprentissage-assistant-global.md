# 18 — Retour d'expérience (apprentissage), assistant sur chaque page, watchlist quotidienne

Statut : spécification **acceptée le 21/10/2026** (demande utilisateur). Avance une partie de la phase 5 (docs/12 §12.2, §12.4, §12.5 ; docs/15-roadmap A5.1 et A5.3) et complète docs/17. Les règles absolues de `CLAUDE.md` s'appliquent intégralement ; en particulier **le LLM ne calcule pas** (règle 3), **jamais de donnée inventée** (règle 2), **instantané de décision** (règle 10) et **aucune optimisation automatique de paramètres** (docs/12 §12.5 point 4).

## 18.1 Ce que « l'IA s'entraîne » veut dire ici (sans promesse trompeuse)

Le modèle de langage (API Claude) **n'est pas ré-entraîné** : l'API ne le permet pas pour cet usage et un échantillon de quelques dizaines de trades conduirait au sur-apprentissage. L'amélioration repose sur trois leviers, tous traçables :

1. **Mesure** (Python, déterministe) : chaque proposition passée, **prise ou non**, reçoit son résultat à partir des cours réellement observés ensuite (§18.4).
2. **Ajustement borné des nouvelles recommandations** (Python, déterministe, paramétré) : chaque nouvelle proposition affiche l'historique de cas comparables et peut être **rétrogradée** (BUY → WATCH) ou classée plus bas quand cet historique est défavorable sur un échantillon suffisant (§18.5). Les tailles, stops et règles de risque ne sont **jamais** modifiés par ce mécanisme.
3. **Mémoire et dialogue** (LLM) : une revue lit uniquement les agrégats calculés, **pose des questions** à l'utilisateur, formule des **propositions d'amélioration** que l'utilisateur accepte ou refuse ; les réponses et les leçons acceptées forment la **mémoire de trading** (`trader_memory`, docs/12 §12.4) injectée dans les analyses suivantes et dans l'assistant (§18.6).

Une modification de paramètre n'est **jamais appliquée automatiquement** : une proposition acceptée produit un brouillon d'ADR et l'extrait YAML daté à reporter dans `config/params.yaml` (source unique, règle 8).

## 18.2 Watchlist recalculée chaque matin

- Nouveau job `watchlist_refresh` : **07:15 Paris, jours de marché**, après `eod_backfill_check` (06:45, qui recalcule `features_daily`) et avant `daily_regime` (07:30). Il applique `refresh_momentum_watchlist` (docs/05 : rang RS ≥ `universe.momentum_watchlist_rs_rank_min`, au-dessus de MM50 et MM200, ≤ `universe.momentum_watchlist_max`) à l'univers courant (dernier instantané) avec les indicateurs du dernier jour calculé.
- Le résultat indique le **jour des indicateurs utilisés** ; s'il est antérieur à la dernière séance attendue de la place, la ligne `jobs_runs` porte l'avertissement « indicateurs du JJ/MM » (jamais de recalcul sur données absentes).
- `universe_refresh` reste hebdomadaire (lundi 06:30) : éligibilité, liquidité et composition de l'univers changent peu ; il recalcule aussi la watchlist.
- Les valeurs `manual`, `external` et `a_l_affut` ne sont jamais retirées par ce job.

## 18.3 Assistant disponible sur chaque page

- Un bouton flottant **« Assistant »** (bas droite ; au-dessus de la barre mobile) ouvre un panneau latéral sans quitter la page. Fonctionne sans JavaScript (le formulaire poste alors vers `/assistant`) ; avec JavaScript (`/static/assistant.js`, local, sans CDN), la réponse s'affiche dans le panneau (`POST /assistant/panel` → fragment HTML).
- **Contexte automatique de la page** : le panneau transmet la page courante (`page`) et, quand la page concerne une valeur (fiche valeur, carte d'opportunité, ligne de position ou de watchlist via leur lien « Demander »), son ISIN. Le dossier de faits (`build_context`) est complété selon la page :

| Page | Faits ajoutés au dossier |
|---|---|
| `/` | Décisions du jour, risque ouvert vs plafond, régime |
| `/opportunites` | Propositions du jour avec leur explication (`explain.summary`) et l'historique comparable (§18.5) |
| `/positions` | Positions, protections, liquidités par compte, ordres en attente |
| `/watchlist` | Watchlist active (motif, force relative) |
| `/journee` | Étapes du jour et leur état |
| `/performance` | Synthèse calculée (réalisé, latent, qualité, fiabilité) |
| `/apprentissage` | Agrégats du retour d'expérience, questions ouvertes, propositions en attente |
| `/sante` | État des jobs (statut et heure, **sans** texte d'erreur) |
| `/valeur/<isin>` | Dossier valeur complet (docs/17 §17.4) |

- **Continuité** : les deux derniers échanges du même contexte (< 2 h) et les **leçons retenues actives** (§18.6, au plus `learning.memory_max_items`) font partie du dossier. Le garde-fou des nombres (docs/17 §17.4) s'applique à tout le dossier ainsi composé.
- Le panneau affiche les 3 derniers échanges du contexte et un lien vers l'historique complet (`/assistant`). Mention permanente : « Information, pas conseil. Aucun ordre n'est passé. »

## 18.4 Résultat de chaque proposition (`signal_outcomes`)

Calcul pur `app/domain/outcomes.py` (testé sur fixtures), alimenté par `app/services/outcomes.py` ; job `outcomes_update` **07:20 Paris, jours de marché** (après les clôtures officielles), idempotent, et bouton « Recalculer » sur `/apprentissage`.

- **Population** : **toutes** les propositions non supprimées (BUY, WATCH…), qu'elles aient été prises, ignorées, vues ou laissées expirer (docs/12 §12.2 : pas de biais de survie).
- **Niveaux** : ceux de la proposition et de son signal (instantané) — prix d'entrée maximal (`entry_zone.max`, sinon `entry_high`, sinon `price`), stop, premier objectif. Jamais des niveaux corrigés après coup (règle 10).
- **Point de départ sans anticipation** : la première barre évaluée est la séance **suivant** la création ; la séance de création n'est utilisée que si la proposition a été créée avant l'ouverture de sa place (08:55 Paris pour XPAR). Barres EOD officielles (`prices_eod`) uniquement.
- **Entrée simulée** (proposition avec prix d'entrée) : ordre à cours limité au prix maximal, valable jusqu'à `expires_at` ou `learning.entry_validity_sessions` (5) séances ; exécuté sur la première barre dont le plus bas ≤ prix ; prix d'exécution = min(ouverture, prix limite). Jamais exécuté → `not_filled` (le rendement de référence reste calculé).
- **Sortie simulée** : stop glissé `stop × (1 − risk.slippage_pct)` ou premier objectif, sur les barres à partir de l'exécution ; une barre touchant les deux → **stop d'abord** (`review.same_bar_rule`), y compris la barre d'exécution ; sinon clôture de la séance `learning.horizon_sessions` (20) après l'entrée → `horizon`.
- **Mesures** : R théorique brut et net des coûts (coût aller-retour estimé de la proposition, sinon « coûts non comptés » signalé), rendement en %, excursions favorable et adverse maximales (MFE/MAE en % et en R), rendements J+1, J+5, J+20 depuis le prix de référence (prix d'entrée, sinon ouverture de la première séance évaluée).
- **Statut** : `pending` (pas encore de barre), `running` (horizon non atteint), `not_filled`, `stopped`, `target`, `horizon`, `unavailable` (cours manquants : aucune estimation). Un résultat n'est **définitif** qu'aux statuts `not_filled`, `stopped`, `target`, `horizon`.
- **Groupe de décision** : `prise` (décision « Ordre saisi », ordre ou trade rattaché à la proposition), `ignoree`, `vue_sans_suite` (Vu / Watch), `sans_decision`. Pour une proposition prise, le résultat réel (FIFO, docs/17 §17.5) est affiché à côté du résultat théorique (écart d'exécution).

## 18.5 Statistiques et ajustement des nouvelles recommandations

Calcul pur `app/domain/learning.py` :

- **Agrégats** (résultats définitifs seulement) par détecteur, source, action, régime de l'instantané, compte recommandé, groupe de décision, tranche de force relative et tranche de dérive depuis l'ouverture (`drift_since_open`, signaux externes) : effectif, taux de réussite, R moyen et médian, **espérance** avec intervalle de confiance à 90 % (approximation normale), mention « indicatif » sous `learning.min_samples_display` (20) et « échantillon suffisant » à partir de `review.profitability_min_signals_per_detector` (60).
- **Biais de sélection** : espérance des propositions prises vs ignorées ; **gagnants manqués** (ignorées ayant atteint ≥ 2 R) et **pertes évitées** (ignorées stoppées).
- **Historique comparable** d'une nouvelle proposition : propositions **antérieures** à sa création, définitives à cette date, de même détecteur et même source (repli : même détecteur) ; espérance **rétrécie** `n / (n + learning.shrinkage_k) × R moyen` (k = 20) pour ne pas sur-réagir aux petits échantillons.
  - `favorable` si rétrécie ≥ `learning.favorable_r` (0,3) et n ≥ `learning.min_samples_adjust` (20) ; `defavorable` si ≤ `learning.unfavorable_r` (−0,2) et même effectif ; sinon `neutre` ou `insuffisant`.
  - **Effets** : (a) ligne « Historique comparable : n cas, espérance x R » dans l'explication et le détail ; (b) tri de `/opportunites` (favorable avant neutre avant défavorable, à action égale) ; (c) **règle docs/12 §12.5.3** : un BUY dont l'historique comparable est défavorable avec n ≥ `review.profitability_min_signals_per_detector` est créé en **WATCH** avec la porte `BLOQUÉ : historique défavorable (learning.demote_buy_to_watch)` ; désactivable par `learning.demote_buy_to_watch: false`.
  - L'historique utilisé est **figé dans l'instantané** (`decision_snapshots.features.track_record`) : un post-mortem relit ce que la proposition a vu.

## 18.6 Revue par l'IA, questions et propositions d'amélioration

- **Dossier d'apprentissage** (Python) : agrégats §18.5, 10 gagnants manqués et 10 pertes évitées, notes libres des propositions, questions et réponses précédentes, propositions déjà acceptées/refusées, leçons actives, valeurs **courantes** des paramètres concernés (lues par Python dans `params.yaml`) et **tableaux de seuils candidats** calculés par tranche (ex. espérance par tranche de dérive depuis l'ouverture ou de force relative).
- **Revue** : job `weekly_review` (samedi 08:00, existant dans docs/02, implémenté ici) et bouton « Lancer une analyse » sur `/apprentissage`. Le LLM (`write_model`, température 0, cache, plafonds) répond selon le schéma `LearningReview` :
  - `summary` (≤ 1 500 caractères) ; `findings` (constat, références aux agrégats, confiance faible/moyenne/forte) ;
  - `questions` (≤ 3, docs/12 §12.4) : question, pourquoi elle aide, type `texte` ou `choix` avec options ;
  - `suggestions` : type `parametre` / `processus` / `donnees` / `source`, titre, justification, effet attendu, `params_key` (si paramètre) et valeur proposée.
- **Garde-fous Python** : tout nombre du texte doit figurer dans le dossier (même mécanisme que l'assistant), sinon l'élément est écarté et compté ; `params_key` doit exister dans `params.yaml`, sa valeur courante est lue par Python (jamais recopiée du LLM) ; la valeur proposée doit être l'une des bornes de tranche du dossier. Une suggestion qui viole une règle de `CLAUDE.md` (passage d'ordre, hausse du risque au-delà des plafonds) est écartée.
- **Dialogue** : l'utilisateur répond aux questions (texte ou choix) ; chaque réponse devient une entrée `trader_memory` (source `reponse`). Il traite chaque suggestion : **accepter** / **refuser** / **plus tard**, avec commentaire. Accepter une suggestion `parametre` affiche le brouillon d'ADR et l'extrait YAML daté (`effective_from` = lendemain) à reporter ; accepter une suggestion `processus` crée une leçon active dans `trader_memory`. L'utilisateur peut aussi saisir une leçon libre et désactiver une leçon.
- La revue suivante reçoit les réponses et décisions : « la semaine dernière tu avais décidé de… » (docs/12 §12.4).

## 18.7 Page `/apprentissage`

1. En-tête : explication §18.1 en trois lignes, dernière revue (date, coût), boutons « Recalculer les résultats » et « Lancer une analyse ».
2. **Questions de l'IA** en attente (formulaire de réponse) puis **propositions d'amélioration** (accepter / refuser / plus tard).
3. Tuiles : propositions évaluées (définitives / en cours), espérance théorique globale, prises vs ignorées (biais de sélection), gagnants manqués.
4. Tableaux par détecteur, source, régime, décision, tranches (effectif, réussite, espérance ± IC, fiabilité).
5. Liste des propositions passées : valeur, date, action, décision, statut du résultat, R théorique, J+5/J+20 ; détail dépliable avec graphique des clôtures depuis la proposition et niveaux (entrée, stop, objectif).
6. **Leçons retenues** (actives / désactivées), ajout libre.

## 18.8 Modèle de données (migration 0007)

- `signal_outcomes` (proposal_id unique, signal_id, computed_at, status, decision_group, ref_price, entry_day, entry_price, exit_day, exit_price, exit_reason, r_gross, r_net, costs_counted, ret_pct, mfe_pct, mae_pct, mfe_r, mae_r, ret_1d, ret_5d, ret_20d, last_bar_day, final).
- `learning_reviews` (ts, trigger ∈ {weekly, manual}, summary, findings json, dossier json, rejected json, cost_usd, model, status ∈ {ok, rules, unavailable}).
- `learning_questions` (review_id, ts, question, why, kind, choices json, answer, answered_at, status ∈ {open, answered, dismissed}).
- `learning_suggestions` (review_id, ts, kind, title, rationale, expected_effect, params_key, current_value json, proposed_value json, status ∈ {open, accepted, rejected, later}, user_comment, decided_at).
- `trader_memory` (ts, text, source ∈ {utilisateur, reponse, suggestion}, ref, active).

## 18.9 Paramètres (`config/params.yaml`)

```yaml
learning:
  effective_from: "2026-10-22"
  horizon_sessions: 20
  entry_validity_sessions: 5
  min_samples_display: 20
  min_samples_adjust: 20
  shrinkage_k: 20
  favorable_r: 0.3
  unfavorable_r: -0.2
  demote_buy_to_watch: true
  memory_max_items: 30
jobs:
  watchlist_refresh: {cron: "15 7 * * 1-5", misfire_grace_min: 240, market_days_only: true}
  outcomes_update: {cron: "20 7 * * 1-5", misfire_grace_min: 240, market_days_only: true}
llm:
  review_max_tokens: 2500
```

Le groupe `learning` est optionnel : absent, la page `/apprentissage` est désactivée et aucune proposition n'est ajustée (validation bloquante par fonctionnalité, règle 8).

## 18.10 Critères d'acceptation

- A18.1 `watchlist_refresh` tourne chaque jour de marché après `eod_backfill_check`, écrit une ligne `jobs_runs` et indique le jour des indicateurs utilisés.
- A18.2 Le panneau Assistant est présent sur toutes les pages ; une question posée depuis une page reçoit un dossier contenant les faits de cette page ; sans JavaScript le formulaire fonctionne.
- A18.3 Sur fixture OHLC : entrée limite, stop d'abord sur barre mixte, horizon, non exécutée, cours manquants → `unavailable` ; la séance de création n'est pas utilisée pour une proposition émise en séance.
- A18.4 Agrégats avec effectif, IC et mention « indicatif » ; prises vs ignorées ; gagnants manqués.
- A18.5 Une proposition reçoit son historique comparable (sans données postérieures à sa création), figé dans l'instantané ; un BUY à historique défavorable sur ≥ 60 cas est créé en WATCH avec la porte `BLOQUÉ`.
- A18.6 Une revue LLM dont un nombre n'est pas dans le dossier voit l'élément écarté ; une `params_key` inconnue est écartée ; sans clé API la page affiche les agrégats seuls.
- A18.7 Une réponse à une question et une suggestion « processus » acceptée apparaissent dans les leçons et dans le dossier de l'assistant ; une suggestion « paramètre » acceptée affiche l'extrait YAML et ne modifie pas `params.yaml`.
