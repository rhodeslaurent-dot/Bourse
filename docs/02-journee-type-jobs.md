# 02 — Journée type, créneaux utilisateur et planning des jobs

Toutes les heures sont données en **heure de Paris** (planification) puis en **heure Réunion** (été / hiver). L'outil affiche toujours les deux dans l'UI et dans Telegram.

## 2.1 Cartographie horaire des sources (jour ouvré Euronext)

| Paris | Réunion été / hiver | Événement | Source (canal) |
|---|---|---|---|
| 05:00–06:00 | 07:00 / 08:00 | Analyses techniques de la veille au soir (Tradosaure, blogs) | Blog/RSS |
| 06:00 | 08:00 / 09:00 | Briefing marchés allemands (morningcrunch), agenda du jour | E-mail |
| 07:00–08:30 | 09:00 / 10:00 → 10:30 / 11:30 | **Communiqués de sociétés avant bourse** (résultats, contrats, guidance) | RSS ActusNews, GlobeNewswire, Euronext, Boursorama « Communiqués » |
| 07:15 | 09:15 / 10:15 | Ouverture de la pré-ouverture Euronext (carnet, prix théorique d'ouverture) | Flux primaire (Saxo si abonné) |
| 08:16 | 10:16 / 11:16 | ABC Bourse « Les actions à suivre » | RSS |
| 08:17–08:30 | | Bourse Direct « Ce Matin », Morning Meeting | Web/e-mail |
| 08:30 | | Décision Bourse (lettre quotidienne), notes macro | E-mail |
| 08:37 | | ABC Bourse « CAC 40 attendu » (pré-ouverture) | RSS |
| 08:46 | | TradingSat/BFM Bourse recommandations d'analystes | RSS |
| 08:50–09:00 | | Enchère d'ouverture Xetra | Flux |
| **09:00** | **11:00 / 12:00** | **Fixing d'ouverture Euronext Paris, début du continu** | Flux temps réel (EODHD WS / Saxo) |
| 09:02–09:10 | | Boursorama/AOF « Valeurs à suivre à Paris » | Web |
| 09:05 / 09:15 / 09:30 | 11:05 / 12:05 … | **Scans d'ouverture de l'outil** (gap, RVOL, opening range) | Outil |
| 09:26 | | Premières recommandations de brokers relayées (ABC Bourse) | RSS |
| ~10:00 | 12:00 / 13:00 | Newsletter Zonebourse (constat utilisateur, non vérifié officiellement) | E-mail (IMAP) |
| ~13:00 | 15:00 / 16:00 | Lettre Momentum de Capital (constat utilisateur) | E-mail (IMAP) |
| 14:30 | 16:30 / 17:30 | Statistiques US | RSS |
| 15:30 | 17:30 / 18:30 | Ouverture de Wall Street | Flux |
| 17:30–17:35 | 19:30 / 20:30 | Pré-clôture puis **fixing de clôture** Euronext | Flux |
| 17:35–17:40 | | Trading at last | Flux |
| 18:00–18:30 | 20:00 / 21:00 | Lettres du soir (Décision Bourse 18:30, Börse-Intern 18:00) | E-mail |
| 22:00 | 00:00 / 01:00 | Clôture US | Flux |

## 2.2 Les trois créneaux utilisateur (≤ 1 h cumulée)

| Créneau | Paris | Réunion | Durée cible | Ce que l'utilisateur fait | Ce que l'outil a préparé |
|---|---|---|---|---|---|
| **C1 — Brief pré-ouverture** | 08:45 | 10:45 / 11:45 | 10 min | Lit le brief Telegram/e-mail, valide la « liste d'ouverture » (valeurs à surveiller à 9h00), ajuste les stops si demandé | Brief : régime, positions et distance aux stops, catalyseurs du matin classés, valeurs candidates avec niveaux d'entrée/stop/taille pré-calculés, agenda (résultats, liquidation SRD) |
| **C2 — Ouverture** (uniquement en mode `disponible`, phase 3) | 09:05–09:45 | 11:05 / 12:05 → 11:45 / 12:45 | 15 min (fractionné) | Réagit aux alertes BUY/WATCH (fiche d'ordre prête), saisit éventuellement un ordre chez le courtier, confirme « Ordre saisi » puis « Exécuté » avec quantité et prix | Alertes à 09:05 / 09:15 / 09:30 (gap + RVOL + catalyseur), niveaux d'entrée (cassure de l'opening range), prix max, stop, taille, comparaison des comptes |
| **C3 — Revue du soir** (créneau principal) | 17:45–18:30 | 19:45 / 20:45 → … | 20–30 min | Lit l'écran « 3 décisions du jour », puis le rapport (10 rubriques) en second niveau ; saisit les ordres du soir (stops, **entrées préparées pour le lendemain à plage de déclenchement** : D2/D3/D4), renseigne le journal | Rapport complet, réconciliation newsletters ↔ signaux propres, mise à jour des stops (trailing), ordres à plage de déclenchement proposés pour J+1 avec prix max, positions à alléger, agenda J+1 |
| **Hebdo (samedi)** | — | 30–45 min | 1×/semaine | Revue de la semaine, décisions sur les règles | Digest hebdo : KPI, expectancy par détecteur/source, signaux ratés, comportement, propositions d'ajustement (ADR) |

Si l'utilisateur est indisponible sur C2 (réunion), les alertes restent consultables et les propositions **expirent automatiquement** 30 minutes après leur scan et au plus tard à 11:00 Paris (paramètres) : une entrée sur gap au-delà de ce délai n'est plus proposée comme BUY (le gap est absorbé), elle bascule en WATCH avec niveau de pullback.

## 2.3 Mode de disponibilité (règle du moteur, dès le MVP)

L'utilisateur travaille en journée : la disponibilité est une **donnée d'entrée du moteur**, pas une hypothèse. Le mode courant est fixé par un planning hebdomadaire par défaut (`availability.schedule`, en heure Réunion) et modifiable à tout moment par Telegram (`/mode disponible|reunion|absent [durée]`) ou depuis le dashboard.

| Mode | Comportement du moteur | Alertes |
|---|---|---|
| `disponible` | Toutes les propositions, y compris celles exigeant une décision immédiate (D1 à l'ouverture, phase 3) ; au plus **3 propositions prioritaires** par créneau, classées par qualité d'entrée | P1 + P2 (quota `availability.quotas.disponible`) + P3 groupé |
| `reunion` | Surveillance et scénarios de repli seulement : aucune proposition exigeant une réaction rapide ; les candidats D1 sont enregistrés (post-mortem) et convertis en WATCH « pullback » ; les ordres d'entrée déjà saisis chez le courtier **restent actifs** (le mode ne les annule pas) et sont suivis : une exécution sans protection est une **P1 dans tous les modes** (docs/07 §7.4) | P1 en temps réel (dont « exécuté sans protection ») ; P2 différées au prochain créneau |
| `absent` (dont vacances) | Gel des nouvelles propositions ; suivi des risques, des échéances (liquidation SRD, résultats) et des protections ; rappels de stops | P1 uniquement ; digest quotidien e-mail |

Le mode `absent` prolongé remplace le « mode vacances » ; les stops restent chez le courtier, l'outil ne fait que surveiller. **Ordres d'entrée préparés pour J+1 et disponibilité** : la règle unique est en docs/07 §7.4 — entrée préparée comme ordre seulement avec un ordre à plage de déclenchement ou un ordre lié ; protection liée acceptable dans tous les modes, sinon ordre proposé seulement si le mode `disponible` est prévu sur la fenêtre d'exécution ou si l'utilisateur accepte explicitement le risque (tracé) ; à défaut, alerte « à saisir manuellement ». **Heure d'été** : l'ouverture de Paris tombe à 11:00 Réunion en été et 12:00 en hiver ; le planning par défaut contient un créneau **relatif à l'ouverture** (`ref: market_open`) à confirmer par l'utilisateur ; s'il n'est pas confirmé, les scans D1 d'ouverture sont volontairement hors créneau en été (choix explicite, affiché sur `/sante`). La page principale répond à une seule question : **« Quelles sont les trois décisions utiles aujourd'hui ? »** (docs/09 §9.5) ; le rapport en dix rubriques reste accessible en second niveau. Les détecteurs préparés la veille (D2/D3/D4, ordres J+1 à plage de déclenchement) sont privilégiés ; la détection d'ouverture (D1) n'apporte un avantage que si l'entrée reste exécutable dans ces contraintes, d'où son activation en phase 3, en mode `disponible` seulement.

## 2.4 Planning des jobs (planificateur en `Europe/Paris`, jours ouvrés de la place concernée)

| Heure Paris | Job | Contenu | Sortie |
|---|---|---|---|
| 06:30 (lundi, tous les lundis même fériés) | `universe_refresh` | Recalcul de l'univers, éligibilité PEA/SRD, liquidité, index membership, secteurs, prochaines dates de résultats | Table `instruments`, diff versionné |
| 06:45 | `eod_backfill_check` | Charge l'EOD **officiel** de la veille (EODHD) pour tout l'univers, remplace les clôtures provisoires, recalcule `features_daily` si écart, relance si trou | `prices_eod`, `jobs_runs`, alerte si trou |
| 07:00 → 09:00 (toutes les 5 min), puis 09:00 → 19:00 (toutes les 15 min) | `news_scan` | RSS communiqués et recos (ActusNews, GlobeNewswire, ABC Bourse, TradingSat…) + classification LLM (type, sens, ampleur, surprise) ; couvre les communiqués du soir (17:40–19:00) pour le brief du lendemain | `news_items` classés, `premarket_watch` |
| 07:30 | `daily_regime` | Régime de marché (indices, breadth), volatilité (VSTOXX si dispo), futures | `market_regime` |
| 07:30 → 19:00 (toutes les 10 min) | `imap_poll` | Lecture de la boîte dédiée : lettres du matin (Décision Bourse 08:30, Bourse Direct, ABC Premium), Zonebourse (~10:00), Momentum Capital (~13:00), lettres du soir (18:30) ; extraction → signaux externes ; heure de réception, `p_open`, `p_recv`, `drift_since_open` | `newsletter_items`, `signals(detector=external)` |
| 08:30 | `zonebourse_lists_daily` (si Premium) | Lecture 1×/jour des listes Momentum Europe/USA → watchlist momentum (source tracée) | `watchlist` |
| **08:45** | **`brief_premarket`** | Backfill REST intraday 5 min (référence RVOL 20 j) pour la liste d'ouverture ; brief C1 : régime, positions/stops, catalyseurs, liste d'ouverture (≤ 15 valeurs) avec niveaux pré-calculés, agenda | Telegram + e-mail + page `/brief` |
| 08:55 | `intraday_watch_start` | Démarre les souscriptions temps réel (WebSocket) sur : positions + liste d'ouverture + watchlist momentum (≤ 50 × n connexions, ≤ 150 symboles) | Flux `prices_intraday` |
| 09:05, 09:15, 09:30, 10:00, 10:30 | `open_scan` (**phase 3**, mode `disponible` seulement) | Détecteurs **D1** (gap+catalyseur), **D2 intraday** (cassure de pivot), **D4 reprise** (cassure du plus haut de la veille) — candidats sur l'univers via screener différé (≤ 6 requêtes, statut « à vérifier »), fiches d'ordre uniquement sur cotation temps réel (liste d'ouverture) ; scoring ; risque ; alerte immédiate, argumentaire LLM ajouté ensuite | Alertes BUY/WATCH Telegram, page `/opportunites` |
| 09:00 → 17:40 (toutes les 1 min sur positions, 5 min sur watchlist) | `position_monitor` | Distance au stop, seuil franchi (P1 « vérifier l'exécution », puis SELL au marché si non confirmée sous 5 min), objectif atteint (alerte REDUCE/trailing), volume anormal sur position | Alertes, `stops` |
| 09:00 → 17:40 (toutes les 5 min) + après chaque déclaration + avant toute proposition | `portfolio_sync_intraday` | Saxo (lecture) : positions, ordres en attente (états, exécutions partielles, stops), cash, engagements → `portfolio_state` daté | `portfolio_state`, rapprochements provisoires |
| 12:00 | `midday_digest` (silencieux si rien) | Synthèse des alertes du matin non traitées, positions | Telegram si événement |
| 15:35 | `us_open_scan` (phase 6) | Scan US sur watchlist restreinte | Alertes |
| **17:50** | **`eod_pipeline`** | Clôtures **provisoires** (screener 17:45 + REST différé, statut D15, réconciliées à 06:45) → détecteurs D2/D3/D4 EOD → scoring → SELL/REDUCE (thèse invalidée, trailing) → routage compte → plan J+1 → rapport 10 rubriques (rédaction LLM sur données calculées) | Telegram (résumé) + e-mail (rapport) + page `/rapport` |
| 19:00 | `portfolio_sync` | Saxo OpenAPI (lecture) : rapprochement complet du jour (exécutions déclarées ↔ confirmées, stops, cash) ; rappel d'import CSV BoursoBank si > 7 jours | `positions`, `trades`, `cash_snapshots` |
| 23:00 **Réunion** (tous les jours) | `backup` | `pg_dump` + export CSV des tables métier vers dossier synchronisé | Fichier daté |
| Samedi 08:00 | `weekly_review` | KPI, expectancy, post-mortem des signaux, comportement, digest LLM | E-mail + page `/revue` |
| Chaque job | `watchdog_ping` | Ping healthchecks.io / Uptime Kuma ; alerte si un job attendu n'a pas tourné | Telegram + e-mail |

Règles : les jobs marqués `market_days_only: true` vérifient le calendrier **de la place de chaque instrument traité** (un scan tourne pour Paris et saute Xetra le 24/12 ; un job sans instrument, comme `brief_premarket`, tourne si au moins une place P0 est ouverte) ; `backup`, `universe_refresh`, `weekly_review` tournent toujours ; les demi-séances (24 et 31 décembre) décalent `eod_pipeline` à 14:20 et arrêtent `position_monitor` à 14:10 ; un job manqué (PC redémarré) est rejoué s'il est encore utile (`misfire_grace_time` par job : 10 min pour `open_scan`, 4 h pour `eod_pipeline`).

## 2.5 Ce que l'utilisateur saisit (et rien d'autre)

1. **Ordres et exécutions** : bouton « Ordre saisi » (l'ordre existe chez le courtier, non exécuté) puis « Exécuté » → quantité et prix (pré-remplis depuis la fiche d'ordre, modifiables) ; ou synchronisation Saxo (en séance toutes les 5 min, après chaque déclaration, rapprochement le soir) ; ou import CSV BoursoBank. Une position n'est créée qu'à l'exécution confirmée.
2. **Protection** : confirmation « Stop saisi chez le courtier » (type, niveau, quantité) — toute quantité exécutée non protégée = P1 immédiate dans tous les modes, rappelée toutes les 15 min.
3. **Décisions** sur les propositions : Vu / Watch / Ignorer (avec motif optionnel, 1 clic) — alimente le journal et les statistiques de comportement.
3 bis. **Mode de disponibilité** : `/mode` Telegram ou dashboard (§2.3).
4. **Cash disponible** par compte, si l'import automatique n'est pas possible (BoursoBank).
5. **Événements exceptionnels** : blocage temporaire (« pas de nouvelles entrées cette semaine »), capital pilote, cash additionnel autorisé.
