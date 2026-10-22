# RUNBOOK — exploitation de BOURSE-PILOT (PC Windows, Docker Desktop)

Rappel : l'outil ne passe jamais d'ordre. En cas de panne, les stops restent chez le courtier ; l'outil ne fait que surveiller.

## 1. Démarrage initial

Tout se fait depuis la racine du dossier (`cd Bourse`), sans éditer de fichier à la main.

0. Secrets dans `.env` : `uv run python -m app.cli env-setup saxo` (puis `telegram`, `eodhd`, `anthropic`, `imap`, `healthchecks`). Saisie masquée, rien n'est affiché ni journalisé.
1. Base de données : `uv run python -m app.cli env-setup database` — demande le nom du conteneur Postgres existant (`docker ps`), son réseau (`docker network ls`), compose `DATABASE_URL` (mot de passe généré si vide) et crée le rôle et la base dans ce conteneur (idempotent, via `docker exec`, mot de passe transmis par stdin).
2. Paramètres : `uv run python -m app.cli params-init --email <adresse>` — crée `config/params.yaml` depuis l'exemple avec `user.telegram_user_id` (lu dans `.env`) et `user.email`, puis affiche `check-config` (`--update` réécrit ces deux lignes dans un fichier existant **et ajoute les jobs apparus depuis dans l'exemple** ; `/sante` signale un job « absent de params.yaml »). Vérifier ensuite `availability.schedule`.
3. Test local (facultatif, PC seulement) : `copy docker-compose.override.example.yml docker-compose.override.yml` publie le port 8000 sur 127.0.0.1 uniquement. À supprimer avant l'exploitation (accès par le tunnel seulement).
4. `docker compose up -d --build app` — le conteneur applique les migrations, charge les calendriers, démarre l'API, le planificateur et le bot Telegram. (`cloudflared` démarre à l'étape §2, une fois `CLOUDFLARE_TUNNEL_TOKEN` renseigné.)
5. Vérifier : `docker compose logs -f app` (attendre « Application startup complete »), puis `http://localhost:8000/sante` (test local) ou `https://bourse.<domaine>/sante`.
6. Telegram : envoyer `/test` au bot → message avec boutons ; cliquer → « Enregistré ». `/mode reunion 2h` → mode affiché sur `/sante`.
7. E-mail : `docker compose exec app python -m app.cli test-email`.

## 2. Cloudflare Tunnel + Access (aucun port ouvert)

1. Zero Trust → Networks → Tunnels → créer un tunnel. Deux variantes de connecteur :
   - **A — conteneur** : copier le **token** (chaîne `eyJ…` de la commande d'installation, pas le Tunnel ID) dans `CLOUDFLARE_TUNNEL_TOKEN` (`env-setup cloudflare`), puis `docker compose --profile tunnel up -d` ; route vers `http://app:8000` ; `docker-compose.override.yml` peut être supprimé.
   - **B — service Windows** (retenue le 21/10/2026) : `cloudflared.exe service install <token>` en administrateur ; route vers `http://localhost:8000` ; **garder** `docker-compose.override.yml` (port lié à 127.0.0.1 seulement) ; ne pas démarrer le profil `tunnel`.
2. Route (« Public hostname » / « Published application ») : `bourse.<domaine>` → service selon la variante.
3. Access → Applications → Self-hosted `bourse.<domaine>` ; policy « Allow » sur l'e-mail unique ; méthodes : One-time PIN (+ Google) ; durée de session 24 h.
4. `uv run python -m app.cli env-setup cloudflare` : token du tunnel (`CLOUDFLARE_TUNNEL_TOKEN`), domaine d'équipe (`<equipe>.cloudflareaccess.com`, `CF_ACCESS_TEAM_DOMAIN`), **Application Audience (AUD) tag** (`CF_ACCESS_AUD`), e-mail autorisé (`CF_ACCESS_EMAIL`). Puis `docker compose up -d --force-recreate`. `/sante` doit afficher « Cloudflare Access : activé ».
5. **Test depuis le PC pro** (A0.2) : ouvrir `https://bourse.<domaine>/` dans le navigateur d'entreprise → OTP → `/sante`. Si le proxy bloque : consigner ADR sur le plan B (Tailscale Funnel + TOTP applicatif).

## 3. Checklist Windows (docs/14 §14.2)

- Docker Desktop : « Start Docker Desktop when you sign in » ; ouverture de session automatique + verrouillage d'écran ; `restart: unless-stopped` (déjà dans compose).
- Alimentation : `powercfg /change standby-timeout-ac 0`, `powercfg /change hibernate-timeout-ac 0`, `powercfg /hibernate off` ; carte réseau : décocher « Autoriser l'ordinateur à éteindre ce périphérique ».
- Windows Update : heures d'activité 07:00–01:00 (Réunion) ; stratégie `NoAutoRebootWithLoggedOnUsers=1` ; redémarrage planifié samedi 06:00 Réunion (Task Scheduler : `shutdown /r /t 0`).
- Task Scheduler « BOURSE-PILOT startup » : au démarrage, différé 3 min, `pwsh -File scripts/startup.ps1` (relance la pile, pinge `pc-startup`).
- Sauvegardes : `BACKUP_HOST_DIR` sur un dossier synchronisé (OneDrive/Drive), réglé par `uv run python -m app.cli env-set BACKUP_HOST_DIR <dossier>` (défaut `./backups` tant qu'il n'est pas réglé). Job `backup` 23:00 Réunion ; rétention 30 jours + 12 mensuelles. `.env` sauvegardé à part, chiffré.

## 4. Watchdog externe (healthchecks.io)

1. Créer un projet, récupérer la **Ping key** → `uv run python -m app.cli env-setup healthchecks` (saisie masquée).
2. Seuls les jobs de `notifications.watchdog_jobs` envoient un signal (par défaut `saxo_token_keepalive`, `backup`, `eod_backfill_check`, `portfolio_sync`, `position_monitor`) ; chaque check est créé au premier signal avec le nom du job. Planification (fuseau Europe/Paris sauf mention) : `saxo_token_keepalive` période 20 min, grâce 15 min (battement de cœur du PC) ; `backup` cron `0 23 * * *` fuseau Indian/Reunion, grâce 1 h ; `eod_backfill_check` cron `45 6 * * 1-5`, grâce 4 h ; `portfolio_sync` cron `0 19 * * 1-5`, grâce 4 h ; `position_monitor` cron `*/5 9-16 * * 1-5`, grâce 15 min. Intégrations : e-mail (et Telegram si souhaité).
3. Un check sans ping = PC / box / Docker en panne : l'alerte part de l'extérieur.
4. **Checks en trop** : avant le 21/10/2026 (commit `151917b`), chaque job envoyait un signal et créait son check (période par défaut 1 jour). Ces checks (`universe_refresh`, `portfolio_sync_intraday`, `imap_poll`, `news_scan`…) ne reçoivent plus rien et passent « DOWN » : les **supprimer** sur healthchecks.io (Check → Settings → Remove). Ne garder que les 5 checks ci-dessus (+ `pc-startup` si la tâche de démarrage est en place), chacun réglé selon le point 2.

## 5. Test de reprise après incident (A0.1, à faire réellement)

| Étape | Attendu |
|---|---|
| Redémarrer le PC | Docker Desktop démarre, `startup.ps1` relance la pile ; `/sante` répond ; jobs manqués rejoués (`misfire_grace`) ou marqués `skipped`/absents ; ping `pc-startup` reçu |
| Couper la box 10 min | cloudflared se reconnecte seul ; Telegram (long polling) reprend ; healthchecks alerte si un job attendu manque |
| Arrêter Docker Desktop puis le relancer | Conteneurs `unless-stopped` reviennent ; `jobs_runs` continue |

Consigner le résultat dans `docs/15-status.md` (A0.1).

## 6. Opérations courantes

Le fichier `.env` (racine du dossier) est lu automatiquement par les commandes `uv run python -m app.cli …` et par Docker Compose. Une variable déjà définie dans le shell ou le conteneur prime sur le `.env`.

- Rejouer un job : `docker compose exec app python -m app.cli run-job <job> --date 2026-09-09 [--mics XPAR,XETR]`.
- Vérifier la configuration : `python -m app.cli check-config` (refus de démarrer si un groupe requis manque).
- Recharger les calendriers après mise à jour du YAML : `python -m app.cli seed-calendar --years 2026,2027`.
- Sauvegarde manuelle : `scripts/backup.ps1` ; test de restauration mensuel : `scripts/restore_test.ps1`.
- Rotation des jetons : régénérer le token Telegram (BotFather), le mot de passe d'application Gmail, le token du tunnel ; mettre à jour `.env` ; `docker compose up -d`.
- Mode de disponibilité : Telegram `/mode disponible|reunion|absent [2h|30m|1j]` ou formulaire sur `/sante`.
- Saxo « autorisation expirée » (alerte P3 `saxo_auth_expired` ou ligne rouge sur `/sante`) : le refresh token Saxo expire en moins d'une heure sans rafraîchissement (ADR-007) ; après tout arrêt prolongé de l'application, relancer sur le PC `uv run python -m app.cli saxo-auth`. Le jeton est partagé par le volume `./data`.
- Interface : après toute modification des gabarits `app/web/templates/*.html`, recompiler la feuille de style (`scripts\build_css.ps1` sous Windows, `scripts/build_css.sh` ailleurs ; ADR-008) et committer `app/web/static/app.css`.
- Mise à jour de version (`git pull`) : lancer `uv run python -m app.cli params-init --update --email <adresse>` pour ajouter à `config/params.yaml` les jobs et blocs nouveaux de l'exemple (ex. job `kpi_snapshot`, bloc `workflow` de la journée guidée) sans toucher aux valeurs existantes, puis `docker compose up -d --build app` (les migrations Alembic s'appliquent au démarrage).
- Import de l'historique des lettres (docs/19), dans le conteneur : `docker compose exec app python -m app.cli newsletters-history --user <adresse> --query "from:<expéditeur>" --dry-run` (repérage, rien n'est écrit), puis la même commande sans `--dry-run`. Le mot de passe d'application est demandé à chaque lancement et jamais enregistré ; le révoquer après l'import. Plafond LLM atteint → relancer le lendemain (reprise automatique).
- Lettres « Lire la suite » (Zonebourse, docs/19 §19.6) : `docker compose exec app python -m app.cli newsletter-link-check --user <boîte> --query "from:noreply@zonebourse.com"` affiche le statut du lien de la dernière lettre. `refuse` + « domaine non autorisé : X » → si X est bien un domaine de suivi de clics de Zonebourse, l'ajouter à `data.imap.follow_links[].tracking_domains` dans `params.yaml`. `reserve` → page réservée aux abonnés : `env-setup zonebourse` (cookie de session copié depuis le navigateur connecté) puis recréer le conteneur.
- Données gratuites (docs/22 §22.7) : taux BCE (`fx_refresh`), tableau macro (`macro_snapshot`), calendrier économique (`macro_calendar`) tournent seuls ; premier chargement immédiat : `docker compose exec app python -m app.cli run-job fx_refresh`, puis `macro_snapshot`, puis `macro_calendar`. Une série muette ou hors plage apparaît dans la note du job (`/sante`), jamais estimée.
- Liste officielle TTF (une fois par an, en janvier) : `uv run python -m app.cli ttf-list-load --year 2026` sur le PC → `data/ttf_list.yaml` (dossier partagé avec le conteneur). Si le site refuse le téléchargement : ouvrir https://bofip.impots.gouv.fr/bofip/9789-PGP.html dans le navigateur, enregistrer la page (Ctrl+S, « page web complète » ou « HTML uniquement ») puis `uv run python -m app.cli ttf-list-load --year 2026 --file <fichier.html>`. Sans liste, la TTF est **estimée** (ISIN FR et capitalisation > 1 Md€) et la fiche le dit.
- Calendriers de places : `python scripts/gen_calendars.py <MIC…> --years 2027,2028` (bibliothèque `exchange_calendars`, à installer à part) affiche les blocs à ajouter et les écarts avec le fichier ; vérifier sur le calendrier officiel avant de les reporter dans `config/market_calendars.yaml`, puis `seed-calendar`.
- Propositions (docs/22) : le moteur tourne à 07:50 Paris (`opportunity_scan`) et à la réception d'une lettre « testable » ; relancer à la main : `docker compose exec app python -m app.cli run-job opportunity_scan`. Page `/opportunites` (classement, fiche, carnet d'ordres) ; Telegram n'envoie que les `ranking.notify_top_n` premiers achats non bloqués. Une dérogation (R21) n'est possible que si la fiche la propose, avec un motif ; elle n'est jamais appliquée automatiquement. Frais du PEA : renseigner `brokers.boursobank.commission_pct` (et `commission_min_eur`) dans `params.yaml`, sinon la fiche affiche ⚠ « frais non renseignés ».
- Pages et tâches ajoutées le 22/10/2026 : `/rapport` (rapport du jour en 10 rubriques, construit à 08:45 par `brief_premarket`, archivé), `/revue` (revue de portefeuille, samedi 08:30 `portfolio_review` ou bouton), `/rotation` (secteurs et ETF), fiche « entrée au fixing » à 17:15 (`fixing_card`), fondamentaux gratuits le dimanche (`fundamentals_refresh`). Relancer une tâche : `docker compose exec app python -m app.cli run-job <nom>`.
- Options désactivées par défaut, à activer dans `params.yaml` puis redémarrer : places hors PEA et États-Unis (`universe.markets_ex_pea`, `universe.markets_us`), ETP crypto (`crypto.etp_isins`), variante à levier sur les fiches (`derivatives.enabled`), revue de presse (`data.imap.press_review.momentum_capital.allowed_domains`).
- Sources reconnues par place (docs/23) : le catalogue est dans `data.rss_feeds` (« à renseigner » = ignoré). Découvrir les flux d'un site : `docker compose exec app python -m app.cli sources-discover` (toutes les sources à renseigner) ou `sources-discover --name eqs_news` ; recopier l'adresse retenue dans `config/params.yaml`, puis vérifier : `docker compose exec app python -m app.cli sources-check` (statut, nombre d'articles, date du plus récent ; rien n'est écrit). Redémarrer l'application pour que `news_scan` lise les nouveaux flux. Les déclarations de dirigeants, franchissements de seuils et rachats d'actions sont classés par règles, sans coût LLM.
- Archivage des simples mentions de l'import Momentum (décision en attente) : `docker compose exec app python -m app.cli archive-mentions` (aperçu), puis `--apply` seulement si décidé (réversible).
- Revue des lettres (`/lettres`, docs/20) : chaque soir à 19:15 Paris (job `letters_review`), ou bouton « Analyser maintenant ». Coût typique quelques centimes par revue (plafond `llm.*`). Les préconisations ne sont jamais des ordres ; une alerte P2 « avis négatif sur position » invite à relire la position.
- Apprentissage (`/apprentissage`, docs/18) : les résultats des propositions se calculent chaque matin (`outcomes_update`, 07:20) et la revue IA tourne le samedi 08:00 (`weekly_review`) ; boutons « Recalculer » et « Lancer une analyse » pour le faire à la demande. Une proposition de paramètre acceptée n'est **jamais** appliquée : reporter l'extrait YAML affiché dans `config/params.yaml`, enregistrer l'ADR, redémarrer l'application. Désactiver la rétrogradation BUY → WATCH : `learning.demote_buy_to_watch: false`.
- Journée guidée (`/journee`) : les étapes viennent du bloc `workflow` de `params.yaml` (type `job`, `page` ou `check`, créneau, jours) ; modifier ce bloc puis redémarrer l'application.
- Assistant (`/assistant`) : nécessite `ANTHROPIC_API_KEY` dans `.env` ; sans clé, il affiche le dossier de faits. Une réponse contenant un nombre absent du dossier est rejetée (règle 3) ; le coût s'ajoute au plafond `llm.*`.
- Position Saxo « non suivie » dans `saxo-check` ou alerte P3 `broker_unmapped` : Saxo ne fournit pas l'ISIN (ADR-006). Si l'univers EODHD n'est pas encore chargé, ajouter l'instrument dans `config/instruments_map.yaml` avec sa clé `saxo: <TICKER>:<mic>` (symbole affiché par `saxo-check`) puis relancer `saxo-check`. Un symbole en `…:xxxx` est un instrument radié ou bloqué : il reste volontairement hors suivi.

## 7. Restauration

```
docker cp backups/bourse-<date>.dump postgres:/tmp/restore.dump
docker exec postgres pg_restore -U bourse -d bourse --clean --if-exists /tmp/restore.dump
docker compose restart app
```
