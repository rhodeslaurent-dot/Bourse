# ADR-007 — Jeton Saxo : rotation, maintien et partage CLI / conteneur

Statut : **accepté** · Date : 2026-10-20

## Contexte

Au premier démarrage Docker, `portfolio_sync_intraday` a échoué avec `Saxo refresh failed: 401`. Saxo OpenAPI **fait tourner le refresh token à chaque rafraîchissement** (l'ancien est invalidé) et lui donne une **durée de vie courte** (`refresh_token_expires_in`, 40 à 60 min dans les exemples officiels). Un jeton obtenu par `saxo-auth` sur le PC et non rafraîchi pendant plus d'une heure est donc mort ; de plus, le fichier de jeton était copié dans l'image, donc figé, et distinct de celui du PC.

## Décision

1. `TokenStore` enregistre (chiffré Fernet, écriture atomique) le refresh token **et** son expiration (`refresh_expires_at`) ; les fichiers anciens (jeton nu) restent lisibles.
2. Le fichier vit dans `./data`, **monté en volume** dans le conteneur (`./data:/srv/app/data`) et exclu de l'image (`.dockerignore`) : `saxo-auth` sur le PC et l'application partagent le même jeton, et chaque rafraîchissement le remplace pour les deux.
3. Job `saxo_token_keepalive` toutes les 20 min, tous les jours (`market_days_only: false`) : rafraîchit le jeton tant que l'application tourne.
4. Un refresh refusé (400/401) lève `SaxoAuthExpired` : alerte **P3 `saxo_auth_expired`** une fois par jour avec la commande à lancer (`saxo-auth`), jamais une erreur de job toutes les 5 minutes ; `/sante` affiche l'état du jeton (valide jusqu'à…, expiré, absent) sans appel réseau.

## Conséquences

- Après un arrêt de l'application de plus d'une heure (PC éteint, Docker arrêté), **relancer `uv run python -m app.cli saxo-auth`** sur le PC ; l'application reprend au prochain cycle.
- Le jeton d'accès (20 min) reste en mémoire ; seule sa rotation est persistée.
- Tests : `tests/unit/test_saxo_token_keepalive.py`.

## Complément du 18/11/2026 — un renouvellement à la fois, jeton d'accès partagé

Constat de l'utilisateur : « je n'avais pas à me reconnecter aussi souvent à Saxo avant ». Cause probable : chaque nouveau client Saxo (maintien du jeton toutes les 20 min, synchronisation du portefeuille, cours toutes les 15 s, rattrapage au démarrage — dix redémarrages le 17/11 —, `saxo-check`) renouvelait le jeton de son côté ; deux renouvellements simultanés avec le même jeton tournant font échouer l'un (« expirée ou révoquée ») et peuvent coûter toute l'autorisation.

- **Un renouvellement à la fois** : verrou des fils de l'application, et verrou de fichier entre processus partageant le jeton là où le système de fichiers le permet (`exclusive_refresh`).
- **Jeton d'accès partagé** : stocké chiffré avec le jeton de renouvellement ; un nouveau client l'utilise tant qu'il est valide au lieu de renouveler.
- **Jamais de fausse expiration** : un refus de renouvellement est relu contre le fichier ; si un autre processus a tourné le jeton entre-temps, son résultat est repris (ou le nouveau jeton essayé une fois).
- La conséquence ci-dessus reste vraie : après plus d'une heure d'arrêt (PC éteint, Docker arrêté), `saxo-auth` est nécessaire.
