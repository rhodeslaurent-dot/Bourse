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
