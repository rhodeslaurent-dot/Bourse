# Séquence de prompts Claude Code — BOURSE-PILOT (v1.1.1)

Mode d'emploi : une session par phase (ou par sous-lot), `/clear` entre deux, plan validé avant tout code, commit à chaque critère validé, **petit lot par session terminé par un comportement observable** (page, message Telegram, ligne de rapport). Copier-coller les prompts tels quels (adapter les éléments entre `<…>`). Les prompts sont en français ; Claude Code écrit le code en anglais (convention CLAUDE.md).

## Prompt 0 — Prise en main de la spécification

```
Tu démarres le projet BOURSE-PILOT. Lis dans l'ordre : CLAUDE.md, docs/00, docs/01, docs/02, docs/03, docs/07, docs/15, docs/15-status.md, config/params.example.yaml. Ne code rien.
Rends-moi : (1) un résumé en 15 lignes de ce que tu as compris des objectifs et des dix règles absolues ; (2) la liste des points de la spec que tu juges ambigus ou contradictoires, avec ta proposition par défaut pour chacun (rappel : params.yaml > docs/07 > docs/06 > autres, et toute divergence est signalée, jamais résolue en silence) ; (3) les décisions qui m'appartiennent (comptes, abonnements, domaine, clés API) et ce dont tu as besoin de ma part pour la phase 0 ; (4) un plan de la phase 0 (fichiers, tests, ordre) en 10 étapes maximum, en séparant le socle technique et la qualification des données.
Enregistre tes propositions par défaut dans docs/ADR/000-hypotheses-initiales.md, statut "proposé". Attends ma validation.
```

## Prompt 1 — Phase 0 (a) : socle et reprise après incident

```
Phase 0 (docs/15 §Phase 0), plan validé, sous-lot « socle ». Implémente : structure du dépôt (CLAUDE.md), pyproject avec uv, docker-compose (service app + cloudflared ; Postgres existant : <nom du conteneur / URL>), chargement de config/params.yaml avec validation Pydantic par fonctionnalité (groupes requis → démarrage refusé ; groupes optionnels manquants → fonctionnalité désactivée avec mention sur /sante ; tests des deux cas), migrations Alembic initiales (tables système, market_calendar PAR PLACE pour XPAR/XAMS/XBRU/XETR 2026, srd_calendar 2026, availability), APScheduler avec jobstore Postgres et un job de test respectant market_days_only et le calendrier de la place, routes /health et /sante, bot Telegram (long polling, filtrage sur mon user_id, message de test avec boutons dont le clic s'enregistre en base, commande /mode disponible|reunion|absent [durée]), envoi d'e-mail de test, scripts Windows (scripts/check.ps1, scripts/backup.ps1, tâche au démarrage), RUNBOOK.md (checklist Windows de docs/14 §14.2, procédure Cloudflare Tunnel + Access, procédure de test de reprise après incident), tests/test_no_trading_endpoints.py.
Contraintes : tests unitaires pour le chargement des paramètres, la conversion Europe/Paris ↔ Indian/Reunion (été et hiver), le calendrier par place (24/12 : Xetra fermé, Paris demi-séance) ; rien en dur.
Avance par étapes ; après chaque étape, lance uv run pytest -q et commit. Termine en remplissant docs/15-status.md (A0.1 → A0.6) avec ce qui reste à faire de mon côté (domaine, Cloudflare, test depuis le PC pro, test de reprise réel).
```

## Prompt 2 — Phase 0 (b) : qualification des données

```
Phase 0, sous-lot « qualification des données » (docs/04 §4.3 bis, docs/15 A0.7). Lis docs/04 et docs/13 §13.2. Implémente l'interface MarketDataProvider (EOD, intraday, realtime) avec des DTO Pydantic portant source, market_timestamp, received_at, processed_at, price_type, market_perimeter, data_status, puis les providers EODHD (EOD bulk, intraday 5 min, WebSocket ws/eu avec barres 1 min et indicateur complete_bar), tradingview_screener (limit >= 1000, <= 6 requêtes par scan, désactivable), yfinance (secours EOD), Saxo OpenAPI en lecture sur mon compte RÉEL (la simulation n'a pas de données de marché) : infoprices + streaming sur 10 valeurs.
Écris un script scripts/qualify_providers.py qui, sur 10 valeurs FR/DE/NL que je te donne, pendant 3 séances, mesure : délai (market_timestamp vs horloge), écarts de cours et de volumes entre providers et vs l'interface Saxo (je saisirai les valeurs de référence dans tests/fixtures/qualification/), trous, comportement à la déconnexion/reprise. Produis une fiche docs/providers/<nom>.md par provider (modèle de docs/04 §4.3 bis) et un ADR-002 proposant : source EOD, source intraday, source temps réel, périmètre RVOL (numérateur et dénominateur sur le même périmètre), et ce qu'on ne fait PAS (pas de rebouchage par l'API historique intraday, pas de changement de source silencieux ; le rattrapage des ticks par l'endpoint dédié du flux temps réel n'est retenu que si la qualification prouve qu'il existe et fonctionne). Tests avec réponses enregistrées (respx/vcr). Critère A0.7.
```

## Prompt 3 — Phase 1 : portefeuille opérationnel, protections, risque, univers

```
Phase 1 (docs/15 §Phase 1). Lis docs/07 (référence risque), docs/08, docs/09 §9.1, docs/10 §10.1, docs/05, docs/13. Plan d'abord, découpé en 4 sous-lots ; je valide chaque sous-lot.
Sous-lot A — Comptes et états : Saxo OpenAPI live lecture (positions, balances, orders dont stops, closedpositions, exécutions) avec synchronisation en séance toutes les 5 min + immédiate après déclaration + avant toute proposition, import CSV BoursoBank (export réel anonymisé dans tests/fixtures/csv/), tables orders/trades/positions/stops avec états d'exécution et de protection PAR QUANTITÉ (CLAUDE.md règle 9), exécutions déclarées (declared_uid) rapprochées des exécutions confirmées (broker_fill_id) selon docs/08 §8.6 (tests : double clic = une déclaration ; déclaration + import = une ligne avec les deux références ; import sans déclaration = trade tagué discretionary?), déclarations Telegram/web « Ordre saisi » / « Exécuté » / « Stop saisi » (type à seuil vs à plage, quantité couverte), P1 immédiate dans tous les modes pour toute quantité exécutée non protégée (regroupée par incident, rappel toutes les 15 min), position_monitor distinguant seuil franchi et stop exécuté, portfolio_state avec âge (proposition « à vérifier » au-delà de 10 min), équité avec engagements SRD (docs/10 §10.3).
Sous-lot B — Moteur de risque (docs/07 §7.2–7.3) : dimensionnement sur prix max (limite non dépassable) et stop glissé (stop_exec = stop × (1 − slippage)), marge de change qui réduit la quantité, frais A/R + TTF avec une itération, recalcul après exécution ; risque initial / courant (borné à zéro par position) / réservé, plafond cumulé, secteur/facteur, scénario de gap, exposition (repères, pas objectifs), levier et couverture SRD, coupe-circuit sur pertes de période en R (réalisées + variation du latent), drawdown d'équité TWR ; tests de référence = exemple de docs/09 §9.1 (114 actions, R 2,381 €, 214 € de risque réel) et un portefeuille synthétique multi-comptes.
Sous-lot C — Univers et données : référentiel instruments + alias, universe_refresh (docs/05), drapeaux PEA à trois niveaux (docs/08 §8.4 bis), srd_eligibility, backfill EOD 10 ans, features_daily (annexe docs/06, tests sur fixtures), market_regime, watchlist momentum, calendrier SRD et alertes J−3.
Sous-lot D — Ingestion et enregistrement : RSS (feeds de params.yaml, UA navigateur, dédup, fixtures), IMAP Gmail (adresse dédiée, heure de réception) avec parseurs Zonebourse et Momentum Capital sur fixtures, gap_open et drift_since_open, classification LLM D6 (schéma JSON, cache, budget, repli mots-clés), decision_snapshots et decisions pour toute proposition, pages /, /positions, /watchlist, /sante.
Critères A1.1 → A1.8. Un ADR par choix non couvert. docs/15-status.md à jour.
```

## Prompt 4 — Phase 2 : entrées préparées, scoring, trois décisions, brief et rapport (MVP)

```
Phase 2 (docs/15 §Phase 2). Lis docs/06, docs/07 §7.5–7.6, docs/08 §8.2, docs/09, docs/02 §2.3. Plan puis 4 sous-lots validés un par un.
Sous-lot A — Détecteurs EOD : D4 pullback (aucun catalyseur requis), D2 cassure et D3 expansion (ordres J+1 à plage de déclenchement avec prix max ; protection liée « if-done » si le courtier le permet, sinon règle de docs/02 §2.3 sur la disponibilité), D5 externe, D6 catalyseurs, S1–S9 (S1 = seuil franchi → P1 « vérifier l'exécution », SELL au marché seulement après 5 min sans exécution confirmée) ; stops STRUCTURELS avec tampon et rejet si distance > max (docs/07 §7.4) ; ≥ 2 fixtures par détecteur + 1 test d'incident chacun ; historisation.
Sous-lot B — Score global /100 (information) ET ratio d'admission par détecteur (dénominateur = blocs applicables : C seulement pour D1 ; bonus C plafonné à 5 pour D2/D3/D4 ; F partiel sur sous-critères disponibles), explanations[], confiance, risque, trois lectures (attractivité / qualité de l'entrée / admissibilité), portes communes + portes par détecteur, comparaison des trois options de compte avec TTF dans les trois quand elle est due ; tests table-driven dont les fixtures de référence de docs/06 §6.5 (D4 sans catalyseur : 0,96 → BUY ; 0,60 → WATCH).
Sous-lot C — Mode de disponibilité (planning + /mode), quotas d'alertes par mode, sélection des 3 décisions du jour (classement par qualité d'entrée et urgence), page / réorganisée, /brief, /opportunites, /rapport/<date> (10 rubriques ; test « aucun chiffre non sourcé »), /ecarts.
Sous-lot D — Jobs : news_scan (pré-ouverture, séance, soir), imap_poll, brief_premarket, eod_pipeline (clôtures provisoires, réconciliation 06:45), midday_digest ; fiches d'ordre avec prix max et protection ; rappel d'annulation des ordres courtier à l'expiration.
Critères A2.1 → A2.7. Puis deux semaines de fonctionnement réel en parallèle de mon processus actuel : validation technique uniquement, consignée dans docs/15-status.md.
```

## Prompt 5 — Phase 3 : détection d'ouverture (D1), après validation du flux

```
Phase 3 (docs/15 §Phase 3). Lis docs/06 D1 et annexe, docs/04 §4.5, ADR-002. Plan puis implémentation : intraday_watch_start (positions + liste d'ouverture + watchlist, ≤ 150 symboles, connexions de 50), barres 1 min avec complete_bar, RVOL_5/RVOL_15 et OR5/OR15 sur le périmètre retenu par l'ADR-002 (numérateur et dénominateur identiques ; source_switched + P4 sinon), backfill de référence 20 j à 08:45 pour la liste d'ouverture, D1 avec exigence d'horodatage de marché < data.buy_intraday_max_market_age_minutes (sur donnée différée : candidat « à vérifier », sans fiche d'ordre), D2 intraday et D4 reprise, open_scan aux heures de params.yaml, actif seulement en mode disponible, alerte immédiate puis argumentaire LLM, expiration scan + 30 min, commande cli run-job open_scan --date <date> sur données intraday historiques avec coûts, délai humain (review.human_delay_minutes) et règle « stop d'abord ». Critères A3.1 → A3.4 ; je fournis 5 journées à rejouer.
```

## Prompt 6 — Phase 4 : KPI P&L et fiscal

```
Phase 4 (docs/15 §Phase 4). Lis docs/10, docs/11, docs/16 §16.3. Plan puis implémentation : coûts (courtage, CRD quotidienne calculée puis rapprochée, prorogations, TTF), valorisation EOD et FX, kpi_daily, page /kpi (cartes, courbe d'équité vs CAC 40 GR et STOXX 600 NR, tableaux par détecteur/source/compte/mode, heatmap mensuelle, distribution des R), exports, subscriptions avec KPI d'utilité ; domain/tax : lots, PMP (test = exemple BOFiP → 103), cessions avec conversion de devise, SRD au règlement (paramètre), moins-values reportables (10 ans), estimation PFU + simulation barème étiquetée « indicative », simulation de clôture, PEA (ancienneté, 5 ans, versements), rapprochement IFU, page /fiscal avec l'avertissement. Tests sur jeu de trades synthétique (SRD prorogé, SEK, vente partielle, stop gappé) ; test d'un changement de taux daté. Critères A4.1 → A4.3.
```

## Prompt 7 — Phase 5 : journal, post-mortem, comparaison des trois démarches

```
Phase 5 (docs/15 §Phase 5). Lis docs/12. Implémente : journal (tags, notes, captures, MAE/MFE depuis prices_intraday), signal_outcomes J+1/5/20 calculés sur decision_snapshots pour TOUS les signaux (pris ou non, lettres seules comprises, survenus en mode reunion/absent, expirés), expectancy théorique vs réalisée par détecteur/source/heure/place/régime/mode avec taille d'échantillon et intervalle de confiance (« indicatif » sous 60), précocité (drift_since_open, gain_de_précocité), comparaison des trois démarches (démarche actuelle reconstituée selon docs/12 §12.2 / autonome / combinaison : résultat net des coûts, drawdown, R engagés, occasions exécutables, temps), comportements (docs/12 §12.3), weekly_review (agrégats + digest LLM Sonnet avec 3 questions et ADR chiffrés), trader_memory, règle de reconduction des abonnements, pages /journal et /revue. Critères A5.1 → A5.3.
```

## Prompt de revue adversariale (à lancer en fin de chaque phase, dans une session séparée)

```
Tu es relecteur adversarial du projet BOURSE-PILOT (lis CLAUDE.md et docs/15). Sans modifier le code, cherche : (1) toute voie par laquelle un ordre pourrait être passé ou une permission de trading demandée ; (2) tout cours/nombre affiché sans source, horodatage de marché ou statut, et tout endroit où l'âge de téléchargement masque le retard de marché ; (3) tout calcul de prix/stop/taille/score délégué au LLM ; (4) toute erreur de fuseau ou de calendrier (jours fériés par place, changement d'heure, demi-séances) ; (5) tout paramètre métier codé en dur ou dupliqué hors params.yaml ; (6) toute fuite de secret ; (7) tout chemin où un ordre saisi devient une position sans exécution confirmée, ou où une exécution peut être comptée deux fois ; (8) tout indicateur mélangeant deux périmètres de données ou changeant de source en silence ; (9) les tests manquants pour les critères de la phase, en particulier les tests d'incidents. Rends un rapport classé par gravité avec fichier:ligne, et propose les tests à ajouter. Ne corrige rien.
```

## Prompt de session courante (après la phase 2, usage quotidien)

```
Lis docs/15-status.md et les 3 derniers ADR. Voici ce que j'ai observé aujourd'hui : <observations>. Propose (sans coder) les corrections et leur priorité ; pour toute modification de paramètre, prépare un override tracé (ancienne valeur, nouvelle, motif) plutôt qu'un changement de code. Code uniquement ce que je valide.
```
