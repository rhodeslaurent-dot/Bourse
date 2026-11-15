# 40 — Étape 1 : recueil, historique et mesure de toutes les sources (lots S1 à S5)

- **Demande de l'utilisateur (15/11/2026)** : « Je veux que tu t'assures que toutes les sources précédemment identifiées sont bien stockées chaque jour, historisées, comparées quotidiennement aux mouvements de marché et aux événements conjoncturels. L'outil doit travailler méthodiquement et traiter un maximum de données. »
- **Place dans le plan de l'utilisateur** : étape 1 (recueil et intégration en base). L'étape 2 (analyse multi-agent, ADR-020) et l'étape 3 (classement par place selon le rapport gain / risque, par anticipation) s'appuient sur ce socle.
- **Principe** : des faits seulement. Chaque chiffre de ce document est un comptage de lignes stockées ou un calcul sur des cours stockés ; rien n'est estimé. La mesure est **descriptive** : aucun seuil de ce lot ne décide une proposition (règle d'observation P1 et ADR-020 : seule la mesure, une fois assez nombreuse, désignera ce qui mérite d'être suivi).

## 40.1 S1 — Santé de chaque source, chaque jour

- **Registre** (`app/services/source_health.py`, `registry()`) : 21 sources, chacune avec la table où elle arrive, la colonne qui date un élément, la cadence attendue, les places couvertes et le job qui l'alimente.

| Famille | Sources |
|---|---|
| Cours et indicateurs | cours de clôture (16 places Europe et US), indicateurs techniques |
| Macro et conjoncture | régime par place, taux de change BCE, séries macro (VIX, Brent, or, taux, indices), calendrier macro |
| Actualités | flux RSS |
| Momentum | lettres (Gmail), podcasts et médias, dirigeants (AMF), consensus des analystes, fondamentaux, résultats, forums (comptages et contenu), Wikipédia |
| Sources officielles | positions courtes publiées, communiqués officiels (AMF) |
| Signaux de l'outil | radar, signaux des détecteurs et des lettres, journal des événements |

- **Instantané du soir** (table `source_health`, une ligne par jour et par source) : éléments du jour (jour calendaire de Paris), des 7 derniers jours, total, premier et dernier élément, jours ouvrés de silence, état `à jour` / `en retard` / `vide`. Une source est **en retard** quand elle se tait plus de jours ouvrés que sa cadence ne le permet (`max_silent_weekdays` du registre : 2 pour les cours, 5 pour les lettres, 7 pour les podcasts…).
- **Alerte** : un message P3 « source(s) muette(s) » au **premier** soir de retard seulement (pas un message par jour de silence).
- **Où le voir** : page `/sources` (état par famille et bande des 14 derniers jours), ligne « sources à jour n/N » dans la revue du jour, commande `sources-health`.

## 40.2 S2 — Un journal unique des événements

- **Table `market_events`** : chaque élément d'une source qui concerne une valeur devient une ligne de même forme — quand il est devenu public (`event_at`), ce qu'il dit (`kind`, `direction` +1 / −1 / 0 ou rien, `strength`), d'où il vient (`ref_table`, `ref_id`) et le **contexte du jour** (régime de la place, ou de l'Europe à défaut ; VIX de moins de 7 jours ; annonce macro à fort impact à un jour près).
- **Sources versées au journal** : lettres (achat / vente / surveillance), podcasts et médias (achat / vente / citation), nouvelles classées (une ligne par valeur citée), déclarations des dirigeants (acquisition / cession), communiqués officiels (par famille), consensus (variation de l'objectif moyen d'au moins `event_journal.consensus_min_change`), résultats (surprise sur le BPA), radar (étiquette), forums (emballement analysé), positions courtes (nouvelle, hausse, baisse), part vendue à découvert FINRA (lot S5), signaux des détecteurs.
- **Le sens est celui de la source**, jamais une opinion de l'outil : une lettre « achat », une nouvelle classée positive, un achat d'un dirigeant. Une position courte qui apparaît ou grossit est lue −1, qui diminue +1. Un communiqué ou une étiquette du radar n'a pas de sens.
- **Date de publication prudente** : une position courte est publique le jour ouvré suivant sa date (18:00 Paris) ; un fait daté du seul jour (consensus lu le soir, étiquette du radar sans heure) est daté 22:00 Paris.
- **Idempotent** : une ligne n'est jamais écrite deux fois (contrainte `ref_table, ref_id, kind, isin`) ; une source en échec n'empêche pas les autres.

## 40.3 S3 — Ce qui a suivi chaque événement, mesuré chaque soir

- **Référence** : la clôture de la **première séance de la place de la valeur** qui se termine après la publication (on pouvait agir à cette clôture). Une lettre reçue à 10:00 se mesure depuis la clôture du jour ; une déclaration publiée à 19:00 depuis celle du lendemain.
- **Mesure** (table `event_outcomes`) à `event_journal.horizons` séances (1, 5, 20) : rendement de la valeur, **médiane des autres valeurs de sa place** sur les mêmes séances (au moins `event_journal.venue_min_members` valeurs), médiane de son **secteur sur la place** (au moins `event_journal.sector_min_members`), et les écarts.
- **États** : `final`, `pending` (fenêtre pas encore close : remesuré le soir suivant), `unavailable` (opération sur titre ou cours aberrant dans la fenêtre — saut du facteur d'ajustement de plus de 20 % ou cours ×4 / ÷4 —, ou cours manquants trop longtemps). Une mesure finale n'est jamais réécrite.
- **Tableau de mesure** (`scoreboard`) : par source, type et sens, l'écart moyen à la place **lu dans le sens de la source** (une « vente » qui a raison donne un écart positif), la médiane, le taux de réussite, le t, et la même chose **par contexte** : régime de la place, VIX < 20 ou ≥ 20, annonce macro proche ou non, et contre le secteur.
- **Où le voir** : page `/sources` (choix de l'horizon), commande `events-scoreboard --horizon 5 --context 10`.

## 40.4 Job et commandes

- **Job `sources_daily`** : 22:40 Paris **tous les jours** (lettres, podcasts et forums arrivent aussi le week-end ; une source muette doit se voir le lundi) : journal des 3 derniers jours → mesure → santé → alerte P3 éventuelle.
- **Rattrapage de l'historique** (une fois, sur le PC) : `events-build --from 2016-01-01` puis `events-measure` (par lots de 20 000 événements, les plus anciens d'abord).
- **Contrôle** : `sources-health`, `events-scoreboard`.

## 40.5 S4 — Fréquences de recueil

En cours de réalisation (voir `docs/15-status.md`).

## 40.6 S5 — Couverture des États-Unis

En cours de réalisation (voir `docs/15-status.md`).

## 40.7 Limites connues

- La médiane de la place est celle des valeurs **de l'univers** présentes aux deux dates ; une place peu couverte (moins de 10 valeurs) ne donne pas d'écart, et c'est dit.
- Le contexte est celui **connu le jour de l'événement** (régime de la veille au plus tard 5 jours, VIX de moins de 7 jours) : jamais reconstruit après coup (règle 10).
- Le t affiché est descriptif : beaucoup de lignes sont comparées à la fois, et un t isolé de 2 arrive par hasard. Le tableau ne sert pas encore à décider ; il dira, quand il sera assez nourri, quelles sources méritent l'étape 2.
