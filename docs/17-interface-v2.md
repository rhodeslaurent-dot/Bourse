# 17 — Interface v2 : opportunités expliquées, assistant, performance, journée guidée, notes

Statut : spécification **acceptée le 21/10/2026** (demande utilisateur après la première journée d'exploitation). Complète docs/09 §9.5 ; les règles absolues de `CLAUDE.md` s'appliquent intégralement (jamais d'ordre, jamais de donnée inventée, le LLM ne calcule pas, paramètres datés, secrets hors code).

## 17.1 Objectifs

1. Chaque information affichée est **compréhensible sans le glossaire** : intitulés en français courant, unité, et statut de la donnée (source, horodatage de marché).
2. Chaque opportunité porte une **explication courte** (une ligne) et un **détail dépliable** (fiche complète, données utilisées, portes de risque, décision) sans alourdir la liste.
3. Un **assistant** répond aux questions sur une valeur à partir des données de l'outil uniquement.
4. Un onglet **Performance** présente les résultats (réalisé, latent, qualité, coûts) calculés en Python.
5. Une page **Journée** guide les mises à jour quotidiennes, étape par étape, chaque étape étant cliquable (exécute un job, ouvre une page ou se coche).
6. Des **notes libres** persistantes sur les positions, les valeurs de la watchlist et chaque valeur, reprises chaque jour.
7. Une **fiche valeur** et une recherche globale (ISIN ou nom).

## 17.2 Vocabulaire affiché (intitulés parlants)

Source unique : `app/web/labels.py` (dictionnaires `LABELS`, `HELP`). Les identifiants techniques restent dans l'API JSON ; l'interface affiche les libellés.

| Identifiant | Libellé affiché | Aide (info-bulle) |
|---|---|---|
| `execution_state` : `order_entered` / `filled_partial` / `filled` | Ordre saisi / Partiellement exécutée / Exécutée | État de l'ordre chez le courtier |
| `protection_state` : `unprotected` / `partially_protected` / `protected` | Non protégée / Partiellement protégée / Protégée | Quantité couverte par un stop saisi chez le courtier |
| `rs_rank` | Force relative (rang 0–100) | Performance relative à l'univers sur 1, 3, 6 et 12 mois ; 100 = la plus forte |
| `mm50` / `mm200` | Moyenne 50 j / 200 j | Moyennes mobiles des clôtures |
| `pivot_60` | Plus haut 60 j | Niveau de cassure potentiel |
| `atr14` | Amplitude moyenne 14 j (ATR) | Mouvement quotidien typique, en € |
| `adv_eur_20` | Volume moyen 20 j (€) | Liquidité : montant échangé par séance |
| `rvol` | Volume relatif | Volume du jour / moyenne 20 j |
| `data_status` : `realtime` / `delayed` / `eod` / `stale` / `unavailable` | Temps réel / Différé 15 min / Clôture / Périmé / Indisponible | Statut de la donnée de marché |
| `regime` : `green` / `orange` / `red` | Marché porteur / Marché incertain / Marché défavorable | Régime (docs/06 §6.2) |
| `to_verify` | À vérifier | Donnée différée ou état de compte périmé : pas de fiche d'ordre |
| `declarative` | Déclaratif | État saisi à la main, non confirmé par le courtier |
| `mode` : `comptant` / `srd` | Comptant / SRD | Règlement différé (levier, coût) |
| `R` | R (risque initial) | Multiple du risque pris à l'entrée (distance au stop × quantité) |

## 17.3 Opportunités (`/opportunites`)

- Liste des propositions du jour (`?date=` pour un autre jour), les plus récentes en tête, filtres : action (BUY / WATCH / autres), détecteur, compte.
- **Carte repliée** : valeur, action, compte recommandé, **explication en une ligne** générée en Python par `app/services/explain.py` à partir du signal (`evidence`) et de la proposition (zone d'entrée, stop en % sous l'entrée, taille, risque en € et en % du capital, portes bloquantes). Jamais de texte LLM dans cette ligne.
- **Détail dépliable** (`<details>`, sans JavaScript) : fiche de proposition complète (docs/09 §9.1), données utilisées (instantané `decision_snapshots` : cours, statut, horodatage de marché, indicateurs), coût estimé, portes (`gates`) avec la mention `BLOQUÉ : <règle>` le cas échéant, décisions déjà prises, note libre.
- Boutons de décision (formulaire) : Vu / Watch / Ordre saisi / Ignorer (motif optionnel) → `record_decision` (même table que Telegram).
- Une proposition `to_verify` affiche le bandeau « à vérifier » et **aucune** fiche d'ordre.

**Mise à jour du 26/10/2026.** La page regroupe les propositions : **actionnables** d'abord (achats retenus et limites préparées, c'est-à-dire le carnet détaillé), puis **à surveiller**, puis **écartées**. Le rang reste celui du moteur ; il est affiché en gris (« rang brut ») hors du groupe actionnable, car le classement intervient avant les portes. Une valeur n° 1 au classement peut ainsi rester à surveiller. Chaque mention WATCH ou BLOQUÉ est suivie d'une phrase « En clair » ; un décompte par place figure en tête. Le mode d'emploi est sur `/aide`.

## 17.4 Assistant (`/assistant`)

- Question libre, optionnellement rattachée à une valeur (ISIN ou nom). L'outil construit en Python un **dossier de faits** (`app/services/assistant.py › build_context`) : identité (nom, place, devise, éligibilité PEA/SRD), dernière clôture (date, statut), indicateurs du jour avec libellés, position et protection si détenue, propositions et décisions récentes, nouvelles des 7 derniers jours (titres, classification), note libre, régime de marché.
- Le LLM (`write_model`, température 0) reçoit le dossier et la question et répond selon le schéma `AssistantAnswer` : `answer` (≤ 1 200 caractères), `facts_used` (extraits du dossier), `caveats`, `not_available` (ce qui manque). Règles du prompt (`app/llm/prompts/assistant_v1.md`) : aucun chiffre hors dossier, aucune recommandation d'achat ou de vente, aucun calcul (les tailles et risques viennent du dossier), réponse en français.
- **Garde-fou Python** : tout nombre de la réponse absent du dossier fait remplacer la réponse par la mention « réponse rejetée : chiffre non présent dans les données » (même mécanisme que M16).
- Sans clé API ou plafond atteint : l'assistant affiche le dossier de faits lui-même (« réponse par règles »), jamais une réponse inventée.
- Coût : compté dans le plafond LLM (`purpose = assistant`), affiché sur `/sante` ; historique conservé (`assistant_messages`) et affiché (20 derniers échanges).
- Mention permanente : « Information, pas conseil. Aucun ordre n'est passé. »

## 17.5 Performance (`/performance`)

Tout est calculé en Python (`app/domain/performance.py`, pur, testé ; `app/services/performance.py` pour l'accès aux données) :

- **Réalisé** : par position fermée, P&L = ventes − achats − frais − TTF (FIFO par position, devise convertie au taux du jour, sinon « change indisponible ») ; R réalisé = P&L / risque initial (stop initial × quantité) quand le stop initial est connu.
- **Latent** : (dernière clôture − PMP) × quantité − frais de sortie estimés, avec statut et date de la clôture ; position sans cours → « indisponible », exclue des totaux, bannière.
- **Qualité** : taux de réussite, gain moyen / perte moyenne (€ et R), espérance en R, profit factor ; fiabilité « indicatif » sous 30 trades.
- **Coûts** : courtage, TTF, CRD (positions SRD) par mois.
- **Courbe d'équité** : `kpi_daily` (job `kpi_snapshot` 18:10 Paris, jours ouvrés) : équité consolidée et par compte, cash, valorisation, réalisé cumulé ; tracée en SVG côté serveur (sans JavaScript). Versements neutralisés plus tard (TWR, docs/10).
- Tableau mensuel (P&L réalisé par mois), répartition par compte, par détecteur, par mode.

## 17.6 Journée guidée (`/journee`)

- Les étapes viennent de `config/params.yaml › workflow.steps` (liste datée) : `{key, slot: C1|C2|C3|hebdo|systeme, label, kind: job|page|check, target, days: "1-5"|"5"|"6"}`.
- Kinds : `job` exécute le job nommé immédiatement (`execute_job`, résultat affiché), `page` ouvre la page cible et se coche à l'ouverture, `check` se coche à la main (ex. « journal renseigné »).
- État persistant par jour (`workflow_steps` : jour, clé, fait à, résultat) ; progression par créneau ; créneaux affichés aux heures Réunion (Paris) ; les étapes non pertinentes selon le mode (`absent`) ou le calendrier (jour férié) sont grisées.
- Étapes par défaut (phase 1) : C1 lire les décisions, vérifier `/sante`, synchroniser Saxo, scanner les nouvelles ; C3 synchroniser Saxo, relire les positions non protégées, saisir les ordres du soir (check), compléter les notes (check), vendredi : import BoursoBank (page) ; hebdo samedi : relire la performance (page) ; système : vérifier la sauvegarde du jour (job `backup` si absente).

## 17.7 Notes libres

- Table `notes` (`scope` ∈ {`isin`, `position`, `watchlist`, `proposal`, `global`}, `ref`, `text`, `updated_at`) ; une note courante par (`scope`, `ref`), modifiée en place, horodatée ; jamais supprimée (vidée au plus).
- Affichées et éditables : sur chaque carte de position, chaque ligne de watchlist (détail dépliable), chaque fiche valeur, chaque opportunité, et une note globale « à faire » sur la page Journée.
- Les notes font partie du dossier de faits de l'assistant et du journal (docs/12).

## 17.8 Fiche valeur (`/valeur/<isin>`) et recherche

- Recherche dans l'en-tête (ISIN ou début de nom, référentiel `instruments`) → fiche.
- Fiche : identité, éligibilités, dernière clôture et statut, graphique des 120 dernières clôtures (SVG serveur) avec moyennes 50/200 et plus haut 60 j, indicateurs libellés, position/protection, propositions, nouvelles 7 jours, note, lien « poser une question à l'assistant ».

## 17.9 Modèle de données ajouté (migration 0006)

`notes`, `workflow_steps`, `assistant_messages`, `kpi_daily` (docs/13 complété).

## 17.10 Critères d'acceptation

- A17.1 Une proposition s'affiche avec sa ligne d'explication et son détail ; une décision prise sur le web apparaît dans `decisions` avec `via = web`.
- A17.2 L'assistant refuse une réponse contenant un nombre absent du dossier ; sans clé API il affiche le dossier de faits.
- A17.3 La page Performance affiche réalisé, latent (avec statut), qualité avec mention de fiabilité, et une courbe d'équité à partir de `kpi_daily`.
- A17.4 Une étape `job` de la Journée exécute le job et enregistre le résultat ; l'état persiste au rechargement et se remet à zéro le lendemain.
- A17.5 Une note saisie sur une position est visible le lendemain et dans la fiche valeur.
- A17.6 Aucun intitulé technique brut (`filled`, `rs_rank`…) n'apparaît dans les pages ; les tests de pages vérifient les libellés.
