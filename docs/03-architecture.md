# 03 — Architecture cible (contraintes imposées, recommandation argumentée)

L'utilisateur laisse Claude Code choisir la stack. Ce document fixe donc les **contraintes** (obligatoires) et donne une **architecture de référence** (recommandée, à retenir sauf meilleure proposition consignée dans un ADR).

## 3.1 Contraintes imposées

| # | Contrainte | Justification |
|---|---|---|
| A1 | Hébergement sur le PC personnel Windows 10/11 avec Docker Desktop (WSL2), PostgreSQL déjà présent en conteneur | Pas de VPS (coût, bugs vécus) ; existant réutilisé |
| A2 | Accès distant **sans installation côté client** (navigateur seul sur PC pro sans droits admin ; smartphone) ; **aucun port ouvert** sur la box ; authentification avant d'atteindre l'application | Sécurité, CGNAT possible chez les FAI réunionnais, PC pro verrouillé |
| A3 | Notifications : Telegram (smartphone) + e-mail (repli lisible depuis le PC pro) | Choix utilisateur |
| A4 | Connecteurs courtiers **en lecture seule** ; aucun endpoint de trading dans le code | Objectif 5 |
| A5 | Toute logique métier (détecteurs, scoring, risque, fiscal) en **code pur testable**, indépendant de l'UI et des providers | Testabilité avec Claude Code, robustesse |
| A6 | Providers de données derrière une **interface unique** avec statut de donnée et bascule ; aucune source non officielle en dépendance critique | Fragilité constatée (yfinance, scraping) |
| A7 | Fuseaux : stockage UTC, planification `Europe/Paris`, affichage `Indian/Reunion` ; calendrier de marché en base | Utilisateur à La Réunion, marché à Paris |
| A8 | Reprise automatique après redémarrage du PC ; watchdog externe ; sauvegarde quotidienne | Objectif 4 (disponibilité) |
| A9 | LLM : API Claude (Anthropic), sorties structurées, cache, budget ; pas de dépendance à un GPU local | Coût faible (≈ 4–5 $/mois estimés), fiabilité |
| A10 | Un seul dépôt, un seul `docker compose`, démarrage en une commande | Maintenabilité par un non-développeur assisté |

## 3.2 Architecture de référence

```
[Smartphone / PC pro : navigateur]  ──HTTPS──▶  Cloudflare Edge (Access : OTP e-mail + Google, 1 seule adresse autorisée)
[Smartphone : Telegram]             ◀──────────  Telegram Bot API (long polling sortant, pas de webhook entrant)
                                                          │ connexion sortante uniquement
PC Windows personnel — Docker Desktop — docker compose (réseau interne, restart: unless-stopped)
 ├─ cloudflared   : tunnel nommé  → http://app:8000   (aucun port publié sur l'hôte)
 ├─ app (Python)  : FastAPI (API JSON + pages Jinja2/HTMX) + APScheduler dans le même processus (lifespan)
 │                  ├─ /                 dashboard mobile-first
 │                  ├─ /brief /opportunites /positions /rapport /journal /kpi /fiscal /parametres /sante
 │                  ├─ /api/…           JSON (Pydantic), OpenAPI auto
 │                  └─ /health /jobs    watchdog, état des jobs, fraîcheur des données
 ├─ postgres (existant) : schéma `bourse` (voir docs/13)
 ├─ n8n (existant, optionnel) : uniquement ingestion IMAP/RSS → POST /api/ingest ; aucune logique métier
 └─ uptime-kuma (optionnel) ou healthchecks.io (externe, gratuit 20 checks) : surveillance des jobs
Externe : EODHD (REST + WebSocket), Saxo OpenAPI (lecture), TradingView screener (différé), RSS, IMAP Gmail,
          API Anthropic (Haiku pour classer, Sonnet pour rédiger), SMTP Gmail.
```

### Choix argumentés

- **FastAPI + Jinja2/HTMX + Tailwind (CDN) + lightweight-charts** plutôt que Streamlit (relance tout le script à chaque interaction, lourd sur mobile derrière un tunnel), NiceGUI (valable, mais moins standard pour les tests HTTP) ou React/Next (second écosystème à maintenir). Un seul langage, pages légères en 4G, testable avec `TestClient`. lightweight-charts (Apache 2.0) exige l'attribution TradingView sur les graphiques.
- **APScheduler (jobstore Postgres) dans le processus `app`** plutôt que Celery/RabbitMQ/Redis : un seul worker suffit ; scinder `worker`/`web` en deux conteneurs uniquement si les scans ralentissent l'UI (même image, commande différente).
- **n8n conservé mais cantonné à l'ingestion** (IMAP, webhooks) : il fait bien ce travail et existe déjà ; la logique de scoring reste en Python testable. Alternative : `imaplib`/`aioimaplib` directement dans `app` (plus simple à tester) — au choix de Claude Code, à consigner en ADR.
- **Cloudflare Tunnel + Access** (plan Zero Trust gratuit ≤ 50 utilisateurs, vérifié 09/2026) sur **un domaine personnel** (`bourse.<mondomaine>`) : zéro port ouvert, indépendant du CGNAT, authentification en amont (OTP e-mail 10 min), jeton `Cf-Access-Jwt-Assertion` vérifié côté FastAPI. Les sous-domaines fournisseurs (`*.trycloudflare.com`, `*.ts.net`, `*.ngrok.io`) sont plus souvent filtrés par les proxys d'entreprise : **test préalable obligatoire depuis le PC pro** (phase 0). Plan B : Tailscale Funnel + authentification applicative (TOTP).
- **Telegram en long polling** (connexion sortante) : pas de webhook entrant, donc pas d'exposition supplémentaire. Boutons inline pour Vu / Watch / Ordre saisi / Exécuté / Stop saisi / Ignorer → écriture en base, jamais d'ordre.
- **PostgreSQL** (existant) pour tout ; Parquet dans un volume pour les historiques intraday volumineux si nécessaire.

## 3.3 Modules applicatifs (correspondance avec les documents)

| Module (`app/…`) | Rôle | Spec |
|---|---|---|
| `data/providers/` | `MarketDataProvider` (EOD, intraday, temps réel), `NewsProvider`, `NewsletterProvider`, `BrokerReadProvider` ; implémentations `eodhd`, `saxo`, `tradingview_screener`, `yfinance`, `rss`, `imap`, `csv_boursobank` | docs/04 |
| `domain/universe` | Construction de l'univers, éligibilité PEA/SRD, liquidité, indices | docs/05 |
| `domain/indicators` | Définitions testées : ATR, ADR, RVOL, RS, MM, opening range, gap… | docs/06 annexe |
| `domain/detectors` | D1…D6 + détecteurs de sortie | docs/06 |
| `domain/scoring` | Grille /100, mapping BUY/WATCH/… | docs/06 |
| `domain/regime` | Feu tricolore de marché | docs/06 |
| `domain/risk` | Sizing, stops, exposition, levier SRD, coupe-circuit, portes de discipline | docs/07 |
| `domain/accounts` | Comparaison PEA / CTO comptant / SRD, coûts SRD, calendrier de liquidation, équité avec engagements SRD | docs/08 |
| `domain/orders` | Fiches d'ordre, machine à états ordre → exécution → protection, expiration, idempotence des exécutions | docs/07, 09 |
| `domain/availability` | Mode de disponibilité (planning + overrides), effets sur alertes et détecteurs | docs/02 §2.3 |
| `domain/portfolio` | Positions, exécutions, P&L, coûts | docs/10 |
| `domain/tax` | PMP, plus/moins-values, estimation d'impôt paramétrée, PEA | docs/11 |
| `domain/journal` | R-multiples, expectancy, MAE/MFE, post-mortem, comportement | docs/12 |
| `llm/` | Prompts versionnés (`app/llm/prompts/*.md`, distincts du dossier `prompts/` de la spec), schémas, cache, budget | docs/06, 09, 12 |
| `scheduler/` | Jobs, calendrier, watchdog, rejeu | docs/02, 14 |
| `notify/` | Telegram, e-mail, déduplication, quotas | docs/09 |
| `web/`, `api/` | UI et API | docs/09 |

## 3.4 Flux de données principal (du signal à la décision)

1. **Ingestion** : cours (EOD nocturne + temps réel sur liste restreinte), news (RSS 5 min), newsletters (IMAP), portefeuille (Saxo API en séance toutes les 5 min + rapprochement le soir, CSV BoursoBank hebdo).
2. **Enrichissement** : indicateurs calculés et stockés (`features_daily`, `features_intraday`), news classées par LLM (`news_items.classification`).
3. **Détection** : détecteurs → `signals` (un signal = instrument + type + horodatage + niveaux + preuves).
4. **Scoring** : `scores` (5 sous-scores + total + confiance + risque).
5. **Risque et routage** : `proposals` (action, compte recommandé, entrée, stop, objectifs, taille, nombre d'actions, blocages éventuels).
6. **Diffusion** : alertes (Telegram/e-mail), pages, rapport.
7. **Décision utilisateur** : Vu / Watch / Ordre saisi / Ignorer → `decisions` ; puis Exécuté → `trades` (déclaré, rapproché ensuite avec l'import courtier) et `positions` (protection `unprotected`) ; puis Stop saisi → `partially_protected` / `protected`. Un instantané de décision est figé à l'étape 5.
8. **Suivi** : `position_monitor` (stops, objectifs), `stops` (historique), alertes SELL/REDUCE.
9. **Apprentissage** : réconciliation J+5/J+20 des `signals` (pris ou non), KPI, revue hebdo.

## 3.5 Alternative minimaliste (si la phase 0 doit tenir en un week-end)

Un seul conteneur `app` (FastAPI + APScheduler), Postgres existant, Telegram + e-mail, Cloudflare Tunnel. Pas de n8n, pas d'Uptime Kuma (healthchecks.io gratuit suffit). EODHD seul provider payant au départ ; tradingview-screener et yfinance en secours ; Saxo API en lecture dès que la clé live est obtenue.
