# 14 — Exigences non fonctionnelles : sécurité, fiabilité, exploitation, coûts

## 14.1 Sécurité

- Accès web uniquement via Cloudflare Access (policy : une seule adresse e-mail autorisée, OTP + Google, session 24 h) ; l'application vérifie le JWT `Cf-Access-Jwt-Assertion` (audience, émetteur, expiration) sur chaque requête ; aucune route accessible sans jeton, sauf `/health` (réponse minimale) et `/api/webhooks/*` (secret HMAC dédié).
- Aucun port publié sur l'hôte Windows hors réseau Docker interne ; `cloudflared` sortant uniquement ; Telegram en long polling.
- Secrets dans `.env` (gitignoré) ; `.env.example` documenté ; jetons Saxo (refresh) chiffrés au repos (clé dans `.env`) ; rotation documentée dans `RUNBOOK.md`.
- Connecteurs courtiers en lecture seule (test `tests/test_no_trading_endpoints.py` : aucune chaîne `/orders` en méthode mutante, aucune permission de trading demandée).
- Telegram : le bot n'accepte que l'`user_id` de l'utilisateur ; toute autre commande est ignorée et journalisée.
- Journalisation sans secrets ni contenu intégral de newsletters payantes.
- Dépendances : `uv lock`, `pip-audit` mensuel via hook.

## 14.2 Fiabilité et exploitation (PC Windows)

- Docker Desktop : « démarrer à l'ouverture de session » activé ; compte Windows en ouverture de session automatique + verrouillage d'écran ; `restart: unless-stopped` sur tous les conteneurs.
- Alimentation : `powercfg /change standby-timeout-ac 0`, `hibernate-timeout-ac 0`, `powercfg /hibernate off` ; veille réseau de la carte désactivée (checklist `RUNBOOK.md`).
- **Test de reprise après incident** (phase 0, réel) : redémarrage du PC, coupure de la box pendant 10 min, arrêt de Docker Desktop — la pile revient seule, les jobs manqués sont rejoués ou marqués, le watchdog externe a alerté.
- Windows Update : heures d'activité **07:00–01:00 Réunion** (18 h max ; couvre les jobs de 05:00 Paris à la sauvegarde de 23:00 Réunion et la clôture US), `NoAutoRebootWithLoggedOnUsers=1`, redémarrage volontaire planifié le **samedi à 06:00 Réunion** (avant `weekly_review`) ; script au démarrage (Task Scheduler, différé 3 min) : `docker compose up -d` + heartbeat.
- **Validation de configuration par fonctionnalité** : groupes requis au démarrage = `config_validation.required_groups` (`timezones`, `capital`, `risk`, `accounts`, `brokers` — nécessaire aux frais estimés —, `notifications`, `data`, `jobs`) → refus de démarrer s'ils manquent ; groupes optionnels (`tax`, `llm`, `detectors.d1_gap_catalyst`, `availability.schedule`…) → fonctionnalité désactivée avec mention sur `/sante`, le reste continue (le suivi des stops ne dépend jamais d'un paramètre fiscal).
- Planificateur : jobstore Postgres, `coalesce=True`, `misfire_grace_time` par job (docs/02), `max_instances=1` ; un job manqué est rejoué s'il est encore utile ; changement d'heure (mars/octobre) testé.
- **Watchdog externe** : healthchecks.io est un service **hors du PC** (« dead man's switch ») : chaque job clé le pinge, et l'absence de ping déclenche l'alerte depuis l'extérieur — une panne complète du PC, de la box ou de Docker est donc détectée même si rien ne tourne localement. Uptime Kuma (local) ne sert qu'à l'affichage. Checks attendus jours ouvrés : `brief_0845`, `open_scan_0905` (phase 3), `eod_1750`, `portfolio_sync_1900`, `backup_2300` ; grâce 15 min → alerte Telegram + e-mail ; page `/sante` affiche le dernier run et la fraîcheur de chaque source.
- **Dégradation contrôlée** : perte du WebSocket → les propositions intraday passent en « à vérifier » (statut D15, aucune fiche d'ordre) + alerte P4 ; le trou de données est marqué, jamais rebouché avec des données finalisées après coup ; quota EODHD atteint → priorisation (positions > liste d'ouverture > watchlist) ; tradingview-screener indisponible → scan EOD seul ; LLM indisponible → classification par règles (mots-clés) avec confiance réduite ; IMAP indisponible → rappel manuel.
- **Sauvegardes** : `pg_dump -Fc` quotidien 23:00 Réunion vers un dossier synchronisé cloud (OneDrive/Drive), rétention 30 jours + 12 mensuelles ; export CSV des tables métier ; **test de restauration mensuel** (script `scripts/restore_test.ps1`) ; `.env` sauvegardé séparément (chiffré).
- Logs : JSON, rotation (10 Mo × 5), niveau INFO ; erreurs des providers avec extrait de réponse tronqué.
- Robustesse des parseurs : fixtures réelles anonymisées versionnées dans `tests/fixtures/{rss,imap,csv}/` ; assertion sur la structure attendue ; alerte P4 si un parseur ne trouve plus rien 2 jours de suite (changement de format).

## 14.3 Performance

- Scan EOD de l'univers (~900 valeurs) < 10 min ; `open_scan` < 60 s ; page mobile < 2 s ; WebSocket : agrégation 1 min sans perte sur ≤ 150 symboles.
- Base : index sur (isin, date/ts) ; partitions mensuelles pour l'intraday ; archivage Parquet.

## 14.4 Qualité logicielle

- Tests unitaires (indicateurs, détecteurs sur fixtures, scoring, risque avec les exemples de docs/09 §9.1, comparaison des comptes, fiscal/PMP avec l'exemple BOFiP 100×95 + 200×105 + 100×107 → PMP 103, fuseaux), tests d'intégration (jobs sur fixtures, API `TestClient`), tests de non-régression des parseurs, test « aucun endpoint de trading », test « aucun chiffre du texte LLM absent des données ».
- **Tests d'incidents** (obligatoires par phase) : coupure WebSocket en séance, quota API atteint, réponse RSS vide ou malformée, e-mail de newsletter au format inattendu, redémarrage du PC pendant un job, changement d'heure, jour férié sur une place mais pas sur l'autre, exécution déclarée deux fois (idempotence), ordre saisi jamais exécuté, stop rejeté par le courtier.
- **Calendrier par place** : les jobs qui traitent des instruments utilisent `market_calendar` de la place de l'instrument (Xetra fermé les 24 et 31 décembre, Paris en demi-séance) ; test dédié.
- Couverture cible : `domain/` ≥ 90 %.
- CI locale : `scripts/check.ps1` (ruff, mypy, pytest, `docker compose config`) branché en hook Claude Code (Stop) ; pre-commit.
- Versionnement : SemVer ; `CHANGELOG.md` ; tag à chaque fin de phase.

## 14.5 Coûts d'exploitation (suivis dans `/sante` et `subscriptions`)

| Poste | Estimation mensuelle (09/2026) |
|---|---|
| EODHD EOD+Intraday | ≈ 27 € (29,99 $) |
| Saxo données Euronext L1 | 0–7 € (remboursé si ≥ 4 transactions) |
| Domaine personnel | ≈ 1 € |
| Cloudflare Tunnel + Access | 0 € |
| API Anthropic (Haiku classification ≈ 30 items/jour, Sonnet rapport quotidien + digest) | ≈ 4–5 $ (plafond configuré 15 $/mois, coupure au-delà avec repli règles) |
| healthchecks.io | 0 € |
| Newsletters existantes (Zonebourse, Momentum) | inchangé |
| Newsletters en test (Décision Bourse 29 €, ABC Premium 19,90 €) | selon décision, revue mensuelle |
| Électricité du PC | non chiffré |

## 14.6 Documentation vivante

`README.md` (démarrage), `RUNBOOK.md` (exploitation : redémarrage, restauration, rotation des jetons, checklist Windows, procédure Cloudflare), `docs/ADR/`, `docs/15-status.md` (avancement par critère), `CHANGELOG.md`.
