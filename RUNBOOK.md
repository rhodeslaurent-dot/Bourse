# RUNBOOK — exploitation de BOURSE-PILOT (PC Windows, Docker Desktop)

Rappel : l'outil ne passe jamais d'ordre. En cas de panne, les stops restent chez le courtier ; l'outil ne fait que surveiller.

## 1. Démarrage initial

1. Copier `config/params.example.yaml` → `config/params.yaml` et renseigner `user.telegram_user_id`, `user.email`, le planning `availability.schedule` (confirmer le créneau `ref: market_open`).
2. Copier `.env.example` → `.env` (secrets uniquement là). `DATABASE_URL` pointe vers l'instance Postgres existante (créer la base `bourse` et un rôle dédié).
3. `docker compose up -d --build` — le conteneur applique les migrations, charge les calendriers, démarre l'API, le planificateur et le bot Telegram.
4. Vérifier : `docker compose logs -f app`, puis `https://bourse.<domaine>/sante` (ou `http://localhost:8000/sante` depuis le PC si un port est publié temporairement pour le test — jamais en exploitation).
5. Telegram : envoyer `/test` au bot → message avec boutons ; cliquer → « Enregistré ». `/mode reunion 2h` → mode affiché sur `/sante`.
6. E-mail : `docker compose exec app python -m app.cli test-email`.

## 2. Cloudflare Tunnel + Access (aucun port ouvert)

1. Zero Trust → Networks → Tunnels → créer un tunnel (connector « Docker ») → copier le token dans `CLOUDFLARE_TUNNEL_TOKEN`.
2. Public hostname : `bourse.<domaine>` → service `http://app:8000`.
3. Access → Applications → Self-hosted `bourse.<domaine>` ; policy « Allow » sur l'e-mail unique ; méthodes : One-time PIN (+ Google) ; durée de session 24 h.
4. Copier l'**Application Audience (AUD) tag** dans `CF_ACCESS_AUD`, le domaine d'équipe (`<equipe>.cloudflareaccess.com`) dans `CF_ACCESS_TEAM_DOMAIN`, l'e-mail dans `CF_ACCESS_EMAIL`. Redémarrer `app`. `/sante` doit afficher « Cloudflare Access : activé ».
5. **Test depuis le PC pro** (A0.2) : ouvrir `https://bourse.<domaine>/` dans le navigateur d'entreprise → OTP → `/sante`. Si le proxy bloque : consigner ADR sur le plan B (Tailscale Funnel + TOTP applicatif).

## 3. Checklist Windows (docs/14 §14.2)

- Docker Desktop : « Start Docker Desktop when you sign in » ; ouverture de session automatique + verrouillage d'écran ; `restart: unless-stopped` (déjà dans compose).
- Alimentation : `powercfg /change standby-timeout-ac 0`, `powercfg /change hibernate-timeout-ac 0`, `powercfg /hibernate off` ; carte réseau : décocher « Autoriser l'ordinateur à éteindre ce périphérique ».
- Windows Update : heures d'activité 07:00–01:00 (Réunion) ; stratégie `NoAutoRebootWithLoggedOnUsers=1` ; redémarrage planifié samedi 06:00 Réunion (Task Scheduler : `shutdown /r /t 0`).
- Task Scheduler « BOURSE-PILOT startup » : au démarrage, différé 3 min, `pwsh -File scripts/startup.ps1` (relance la pile, pinge `pc-startup`).
- Sauvegardes : `BACKUP_HOST_DIR` sur un dossier synchronisé (OneDrive/Drive). Job `backup` 23:00 Réunion ; rétention 30 jours + 12 mensuelles. `.env` sauvegardé à part, chiffré.

## 4. Watchdog externe (healthchecks.io)

1. Créer un projet, récupérer la **Ping key** → `HEALTHCHECKS_PING_KEY`.
2. Les checks sont créés au premier ping avec le nom du job (`test_job`, `backup`, puis `brief_premarket`, `eod_pipeline`, `portfolio_sync`…). Régler pour chacun période + grâce (15 min) et l'intégration Telegram + e-mail.
3. Un check sans ping = PC / box / Docker en panne : l'alerte part de l'extérieur.

## 5. Test de reprise après incident (A0.1, à faire réellement)

| Étape | Attendu |
|---|---|
| Redémarrer le PC | Docker Desktop démarre, `startup.ps1` relance la pile ; `/sante` répond ; jobs manqués rejoués (`misfire_grace`) ou marqués `skipped`/absents ; ping `pc-startup` reçu |
| Couper la box 10 min | cloudflared se reconnecte seul ; Telegram (long polling) reprend ; healthchecks alerte si un job attendu manque |
| Arrêter Docker Desktop puis le relancer | Conteneurs `unless-stopped` reviennent ; `jobs_runs` continue |

Consigner le résultat dans `docs/15-status.md` (A0.1).

## 6. Opérations courantes

- Rejouer un job : `docker compose exec app python -m app.cli run-job <job> --date 2026-09-09 [--mics XPAR,XETR]`.
- Vérifier la configuration : `python -m app.cli check-config` (refus de démarrer si un groupe requis manque).
- Recharger les calendriers après mise à jour du YAML : `python -m app.cli seed-calendar --years 2026,2027`.
- Sauvegarde manuelle : `scripts/backup.ps1` ; test de restauration mensuel : `scripts/restore_test.ps1`.
- Rotation des jetons : régénérer le token Telegram (BotFather), le mot de passe d'application Gmail, le token du tunnel ; mettre à jour `.env` ; `docker compose up -d`.
- Mode de disponibilité : Telegram `/mode disponible|reunion|absent [2h|30m|1j]` ou formulaire sur `/sante`.

## 7. Restauration

```
docker cp backups/bourse-<date>.dump postgres:/tmp/restore.dump
docker exec postgres pg_restore -U bourse -d bourse --clean --if-exists /tmp/restore.dump
docker compose restart app
```
