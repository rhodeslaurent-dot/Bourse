# 15 — Roadmap par phases et critères d'acceptation (v1.1.1)

Chaque phase = une ou plusieurs sessions Claude Code, un tag Git, des critères vérifiables. L'utilisateur valide chaque phase avant la suivante. Les durées sont indicatives (soirées/week-ends). Principe de l'ordre : **d'abord des données qualifiées et un portefeuille opérationnel avec ses protections, puis quelques décisions exploitables par jour, puis la détection d'ouverture, enfin les tableaux de bord.** L'enregistrement minimal des décisions et de leurs résultats commence dès la phase 1.

## Phase 0 — Socle technique, accès distant, qualification des données, reprise après incident (2 semaines)

Livrables : dépôt initialisé (structure de CLAUDE.md), `docker compose` (app + cloudflared, Postgres existant), migrations initiales, `config/params.yaml` chargé avec **validation bloquante par fonctionnalité** (docs/14 §14.2), calendriers de marché 2026 **par place** (Euronext Paris/Amsterdam/Bruxelles, Xetra) + calendrier SRD 2026, planificateur avec un job de test, `/health`, `/sante`, bot Telegram (boutons, `/mode`), e-mail de test, Cloudflare Tunnel + Access, watchdog externe, sauvegarde, scripts Windows, `RUNBOOK.md`. **Qualification des données** (docs/04 §4.3 bis) : providers EODHD (EOD, intraday, WebSocket), tradingview-screener, yfinance, Saxo (lecture, compte réel) sur un échantillon de 10 valeurs FR/DE/NL, avec fiches `docs/providers/*.md`.

Critères :
- [ ] A0.1 `docker compose up -d` démarre tout ; après redémarrage du PC, coupure de box de 10 min et arrêt de Docker Desktop, la pile revient seule, les jobs manqués sont rejoués ou marqués, le watchdog externe a alerté (test réel).
- [ ] A0.2 Depuis le **PC pro (navigateur)** et le smartphone : `https://bourse.<domaine>/` demande l'OTP Cloudflare puis affiche `/sante`. Si le proxy pro bloque : ADR-001 sur le plan B.
- [ ] A0.3 Un job planifié à 09:05 Paris s'exécute à l'heure Réunion attendue (été et hiver simulés) ; un job sur un instrument Xetra ne tourne pas le 24/12 alors qu'un job Paris tourne en demi-séance.
- [ ] A0.4 Telegram : message avec boutons, clic enregistré, autre `user_id` ignoré, `/mode reunion 2h` change le mode et l'affiche sur `/sante`.
- [ ] A0.5 healthchecks.io alerte si le job de test manque ; `pg_dump` quotidien présent ; restauration testée.
- [ ] A0.6 `tests/test_no_trading_endpoints.py` passe ; démarrage refusé si un groupe requis manque, fonctionnalité désactivée (sans arrêt) si un groupe optionnel manque (tests) ; `pytest`, `ruff`, `mypy` verts.
- [ ] A0.7 **Qualification** : pré-qualification documentaire avant le code, puis, par prototype, pour chaque provider, fiche complétée avec délai mesuré (écart `market_timestamp`/horloge sur 3 séances), périmètre des volumes, comportement à la déconnexion **et existence réelle d'un endpoint de rattrapage des ticks manqués du flux temps réel** ; comparaison à l'interface Saxo sur 10 valeurs (cours, volumes, horodatages) ; décision consignée (ADR-002) sur les sources retenues pour l'EOD, l'intraday et le temps réel, et sur le périmètre RVOL.

## Phase 1 — Portefeuille opérationnel, protections, risque, univers et watchlist européenne (3 semaines)

Livrables : Saxo OpenAPI **live en lecture** (positions, cash, ordres en attente dont stops, exécutions), import CSV BoursoBank, états d'exécution et de protection par quantité (docs/07 §7.4, docs/09 §9.1) avec rapprochement déclaré/confirmé, saisie Telegram/web (« Ordre saisi », « Exécuté », « Stop saisi »), `position_monitor` (seuil franchi vs stop exécuté), équité avec engagements SRD, **moteur de risque** (docs/07 §7.2–7.3 : dimensionnement sur prix max, risque ouvert cumulé, secteur/facteur, scénario de gap, coupe-circuit), calendrier SRD et alertes de liquidation, référentiel instruments + univers PEA/SRD (docs/05) + drapeaux PEA à trois niveaux, backfill EOD 10 ans, `features_daily`, régime de marché, watchlist momentum, ingestion RSS + IMAP (Zonebourse, Momentum) avec `gap_open`/`drift_since_open`, classification LLM des news, enregistrement minimal des décisions et `decision_snapshots`, pages `/` (3 décisions : au début, positions/protections/risques), `/positions`, `/watchlist`, `/sante`.

Critères :
- [ ] A1.1 Positions, cash, ordres en attente Saxo identiques à l'interface Saxo pendant 5 jours, avec synchronisation en séance toutes les 5 min et après chaque déclaration ; import BoursoBank testé sur un export réel anonymisé ; écarts listés.
- [ ] A1.2 Une exécution déclarée puis importée est rapprochée en une seule ligne portant `declared_uid` et `broker_fill_id` (prix ± 0,5 %, ± 10 min) ; un double clic sur « Exécuté » ne crée qu'une déclaration ; un « Ordre saisi » ne crée pas de position ; toute quantité exécutée non protégée déclenche une P1 immédiate **quel que soit le mode**, rappelée toutes les 15 min, et une exécution partielle donne un état `filled_partial` + `partially_protected` cohérent (tests).
- [ ] A1.3 Risque ouvert cumulé, réservé, par secteur, scénario de gap et levier affichés et testés sur un portefeuille synthétique (SRD + comptant + PEA, devise SEK) ; équité conforme à docs/10 §10.3.
- [ ] A1.4 Exemples de dimensionnement de docs/09 §9.1 reproduits par les tests avec les paramètres de `params.example.yaml` (**114 actions, R 2,381 €, risque réel 214 €**) ; risque courant borné à zéro par position ; un dépassement du plafond produit `BLOQUÉ` avec la règle ; une proposition sur état de portefeuille périmé (> 10 min) est « à vérifier ».
- [ ] A1.5 Univers ≥ 600 instruments, `pea_eligible_regulatory` et `srd_status` renseignés ; diff hebdo produit ; EOD officiel complet chaque matin à 06:45 avec trous détectés.
- [ ] A1.6 Newsletters : extraction correcte sur 10 lettres de fixtures avec heure de réception, `p_open`, `p_recv`, `drift_since_open` ; RSS classés en < 2 min après récupération ; coût LLM affiché.
- [ ] A1.7 Seuil de stop franchi (cours simulé) → P1 « vérifier l'exécution » en < 60 s ; sans exécution confirmée après 5 min → proposition SELL au marché dans le même incident (tests).
- [ ] A1.8 Décisions et instantanés enregistrés pour toute proposition (même sans détecteur : lettres seules), base de la comparaison des trois démarches (docs/12).

## Phase 2 — Entrées préparées : pullbacks (D4) et cassures (D2/D3), signaux externes, scoring, trois décisions, brief et rapport (**MVP**, 3 semaines)

Livrables : indicateurs (annexe docs/06) ; détecteurs D4, D2, D3 en **EOD** (ordres J+1 à plage de déclenchement avec prix max, protection liée si possible) et D5, D6 ; S1–S9 ; scoring /100 sur total atteignable avec les trois lectures (docs/07 §7.6) ; portes communes et **par détecteur** ; comparaison des trois options de compte (docs/08 §8.2) ; fiches de proposition et d'ordre ; jobs `news_scan`, `imap_poll`, `brief_premarket`, `eod_pipeline`, `midday_digest` ; **mode de disponibilité** appliqué aux alertes et aux détecteurs ; écran « 3 décisions du jour » ; rapport 10 rubriques ; pages `/brief`, `/opportunites`, `/rapport`, `/ecarts`.

Critères :
- [ ] A2.1 Chaque indicateur a un test sur fixture ; chaque détecteur a ≥ 2 fixtures (déclenche / ne déclenche pas) et ≥ 1 test d'incident (données incomplètes, split, barre incomplète).
- [ ] A2.2 D4 sans aucun catalyseur produit un BUY sur la fixture de référence (ratio d'admission 0,96, docs/06 §6.5) et un WATCH sur la fixture médiocre (0,60) ; D1 n'existe pas encore ; une valeur attractive avec entrée de faible qualité va en watchlist « à l'affût », jamais en BUY (tests).
- [ ] A2.3 Aucune proposition sans stop structurel, prix max, taille, comparaison des comptes (TTF comptée dans les trois options quand elle est due), portes évaluées ; score global et ratio d'admission affichés avec la complétude des fondamentaux ; une date de résultats inconnue est une réserve visible.
- [ ] A2.4 En mode `reunion`, aucune P2 intraday n'est envoyée ; en mode `absent`, aucune proposition ; au plus 3 propositions prioritaires par créneau en mode `disponible` (tests + 5 jours réels).
- [ ] A2.5 Rapport quotidien : 10 rubriques ; test « aucun chiffre non sourcé dans le texte LLM » ; brief reçu à 08:45 Paris 5 jours ouvrés consécutifs.
- [ ] A2.6 Ordres J+1 (règle docs/07 §7.4) : fiche avec ordre **à plage de déclenchement** (seuil + limite = prix max) ; sans plage ni ordre lié → jamais préparée comme ordre (alerte) ; plage sans ordre lié → préparée seulement si mode `disponible` prévu sur la fenêtre ou acceptation explicite tracée ; ordre lié → tous modes (tests des quatre cas) ; à l'expiration, rappel d'annulation de l'ordre courtier ; un changement de mode ne modifie aucun ordre (test).
- [ ] A2.7 Page `/ecarts` : chaque jour, valeurs de l'outil vs valeurs des lettres (heures, prix, drift) ; **deux semaines de fonctionnement réel en parallèle du processus actuel = validation technique** (pas de conclusion de rentabilité).

## Phase 3 — Détection d'ouverture (D1), en mode `disponible`, après validation du flux temps réel (2 semaines)

Livrables : WebSocket EODHD (et/ou Saxo streaming) sur positions + liste d'ouverture + watchlist ; `RVOL_5/15`, `OR5/15` avec périmètre cohérent (ADR-002) ; D1 avec exigence d'horodatage de marché < 3 min ; `open_scan` ; `intraday_watch_start` ; D2 intraday et D4 reprise ; alerte immédiate puis argumentaire LLM ; expiration scan + 30 min.

Critères :
- [ ] A3.1 100 symboles suivis de 08:55 à 17:40 avec barres 1 min complètes/incomplètes marquées ; coupure simulée → propositions rétrogradées « à vérifier », aucune fiche d'ordre sur donnée différée (test).
- [ ] A3.2 RVOL numérateur et dénominateur sur le même périmètre (test) ; changement de source en cours d'indicateur → `source_switched` + P4.
- [ ] A3.3 Rejeu de 5 journées passées sur données intraday historiques : D1 cohérent avec les gaps observés, coûts et délai humain appliqués, règle « stop d'abord » (contrôle manuel).
- [ ] A3.4 Alertes d'ouverture à 09:05/09:15/09:30 pendant 5 jours réels en mode `disponible` ; aucune en mode `reunion`.

## Phase 4 — KPI P&L et fiscal (3 semaines)

Livrables : coûts (courtage, CRD calculée puis rapprochée, prorogations, TTF), valorisation EOD et FX, `kpi_daily`, page `/kpi`, benchmarks, exports ; abonnements et KPI d'utilité ; **fiscal** : PMP, cessions, moins-values reportables, estimation PFU + simulation barème (indicative), simulation de clôture, PEA (ancienneté, 5 ans, versements), page `/fiscal`, rapprochement IFU.

Critères :
- [ ] A4.1 CRD calculée = CRD prélevée par Saxo à ± 5 % sur un mois.
- [ ] A4.2 P&L réalisé/latent, espérance R, profit factor, drawdown (réalisé + latent), courbe d'équité vs CAC 40 GR / STOXX 600 NR ; tests sur jeu de trades synthétique (SRD prorogé, SEK, vente partielle, stop gappé).
- [ ] A4.3 Test PMP conforme à l'exemple BOFiP ; devise ≠ EUR ; SRD au règlement ; changement de taux daté appliqué selon le fait générateur ; rapprochement IFU avec écarts.

## Phase 5 — Journal, post-mortem, comparaison des trois démarches (2 semaines)

Livrables : journal enrichi (tags, MAE/MFE, captures), `signal_outcomes` J+1/5/20 sur instantanés, expectancy théorique vs réalisée par détecteur/source/heure/place/régime/mode, précocité, **comparaison démarche actuelle (référence définie en docs/12 §12.2) / détection autonome / combinaison**, comportements, revue hebdo/mensuelle avec propositions d'ADR, mémoire de trading, règle de reconduction des abonnements, pages `/journal`, `/revue`.

Critères :
- [ ] A5.1 100 % des signaux ont leurs outcomes à J+20 sur instantané ; tableau par détecteur/source avec taille d'échantillon, intervalle de confiance et mention « indicatif » sous 60 signaux.
- [ ] A5.2 Comparaison des trois démarches disponible sur la période écoulée (résultat net, drawdown, R engagés, occasions exécutables, temps).
- [ ] A5.3 Digest du samedi reçu avec 3 questions et ≥ 1 proposition d'ADR chiffrée quand l'échantillon le permet ; règle de reconduction des abonnements appliquée.

## Phase 6 — Extensions (au fil de l'eau)

Webhooks TradingView ; extension US (CTO) ; Suisse/UK en CTO ; fondamentaux et calendrier EODHD All-in-One ; EQS Newswire ; transactions de dirigeants (AMF) ; backtest léger (Backtrader) ; export des positions vers TradingView ; second utilisateur (lecture).

## Suivi

`docs/15-status.md` : tableau des critères (ID, statut, date, commit, note) tenu par Claude Code en fin de session.
