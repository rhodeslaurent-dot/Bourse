# 19 — Import de l'historique des lettres (Momentum…) puis mise à jour courante

Statut : spécification **acceptée le 21/10/2026** (demande utilisateur). Complète docs/04 §4.5 (IMAP), docs/06 D5 (signal externe), docs/12 §12.2 et docs/18 (retour d'expérience). Règles absolues de `CLAUDE.md` intégralement applicables.

## 19.1 Objectif

1. **Initialiser** la base avec toutes les lettres d'une source (ex. Momentum) déjà reçues sur la boîte principale de l'utilisateur, pour que le retour d'expérience (docs/18) dispose immédiatement d'un historique : chaque valeur citée devient une proposition datée de la lettre, dont le résultat est calculé sur les cours réellement observés ensuite.
2. **Mettre à jour** ensuite la base au fil de l'eau à partir de la boîte dédiée (`IMAP_USER`, ex. rds.cryptos@gmail.com) par le job `imap_poll` existant, sous le **même nom de source** pour que les statistiques se cumulent.

## 19.2 Accès à la boîte principale : lecture seule, rien n'est stocké

- Commande ponctuelle `newsletters-history` (CLI), lancée par l'utilisateur. Connexion IMAP Gmail avec un **mot de passe d'application** demandé en saisie masquée au lancement, **jamais écrit** dans `.env`, la base, les journaux ou la sortie ; révocable après l'import.
- Dossier « Tous les messages » repéré par son attribut IMAP `\All` (nom localisé par Gmail), ouvert en **lecture seule** (`EXAMINE`) ; lecture `BODY.PEEK` : aucun drapeau modifié, aucun mail déplacé ou supprimé.
- Recherche côté serveur par requête Gmail (`X-GM-RAW`, ex. `from:momentum`) et date de début ; seules les lettres trouvées sont téléchargées.
- **Mode repérage** (`--dry-run`) : affiche le nombre de lettres, les adresses d'expéditeur (comptées), la première et la dernière date et quelques objets, sans rien stocker. Sert à vérifier la requête et à renseigner `data.imap.senders` pour la mise à jour courante.

## 19.3 Enregistrement d'une lettre historique

- Même chaîne que `imap_poll` (`ingest_mail`) : déduplication (`dedup_hash` ; une lettre déjà présente, y compris reçue plus tard par la boîte dédiée, n'est jamais dupliquée), texte conservé (`newsletter_items.body_text`, privé), extraction des valeurs par règles puis LLM (niveaux acceptés seulement s'ils figurent dans le texte, M16), `p_open` et `close_prev` depuis les clôtures officielles stockées, `gap_open`.
- **Cours à réception inconnu** : `p_recv` et `drift_since_open` restent vides, statut `unavailable` (règle 2 : jamais d'estimation).
- La proposition (WATCH, signal `external`, source donnée) porte la **date de la lettre** (`created_at` = réception), le statut `expired` (aucune décision possible a posteriori) et un **instantané reconstitué** : indicateurs et régime du dernier jour **antérieur ou égal** à la séance de la lettre s'ils sont stockés, sinon absents ; mention « reconstitution historique » dans l'instantané. Aucune donnée postérieure n'y figure (règle 10).
- Aucune entrée en watchlist, aucune notification, aucun historique comparable (docs/18 §18.5) pour ces propositions.
- Groupe de décision dans les statistiques : « Sans décision » (l'outil ne connaît pas les choix passés de l'utilisateur ; une note libre sur la proposition peut les préciser).

## 19.4 Budget LLM, reprise, progression

- L'extraction LLM consomme le plafond journalier (`llm.daily_budget_usd`). Avant chaque lettre, l'import vérifie le budget : plafond atteint → **arrêt propre** avec le nombre de lettres restantes ; relancer le lendemain reprend où l'import s'est arrêté (déduplication). Option `--no-llm` : extraction par règles seules (gratuite, moins complète).
- Progression affichée (n / total, lettres nouvelles, valeurs, coût) ; à la fin, calcul des résultats (`outcomes_update`) et synthèse.

## 19.5 Critères d'acceptation

- A19.1 `--dry-run` n'écrit rien en base et n'affiche aucun contenu de lettre au-delà de l'objet.
- A19.2 La boîte est ouverte en lecture seule et lue en `BODY.PEEK` (test sur faux serveur IMAP) ; le mot de passe n'apparaît ni dans `.env` ni dans la sortie.
- A19.3 Une lettre historique crée une proposition datée de la lettre, `expired`, sans watchlist, avec un instantané sans donnée postérieure, `p_recv` indisponible ; son résultat est calculé sur les séances suivantes.
- A19.4 Relancer l'import ne duplique rien ; budget atteint → arrêt propre et reprise possible.
