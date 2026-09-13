# 09 — Rapports, alertes, fiches d'ordre, interface

## 9.1 Fiche de proposition (format unique, tout canal)

Pour chaque valeur analysée (BUY/WATCH/SELL/REDUCE/HOLD/NO TRADE), conformément au projet actuel, avec les trois lectures (docs/07 §7.6) :

```
[BUY] Nom (TICKER · ISIN) — Euronext Paris — 42,35 € (RT EODHD/Cboe · marché 09:14:05 Paris · reçu 09:14:06)
Détecteur : D1 gap+catalyseur · Catalyseur : résultats S1 > attentes, guidance relevée (ActusNews 07:32)
Score 81/100 (C 24 · T 19 · F 14 · R 14 · M 10) · Confiance 4/5 · Risque moyen · Régime VERT
Ratio d'admission D1 : 0,81 = (C 24 + T 19 + R 14 + M 10 + F 14) / 100 → BUY · Attractivité : forte · Qualité de l'entrée : moyenne (extension 1,2 ATR) · Admissibilité : 8/9 portes — BLOQUÉ (porte 4, ligne ci-dessous)
Entrée : 42,40 → prix max 42,90 = OR15_high 42,48 × 1,01 (ordre limite) · Stop : 40,60 à seuil (sortie glissée 40,52) · R = 2,38 €/action (prix max − stop glissé)
Obj 1 : 47,66 (2 R) · Obj 2 : 50,04 (3 R) · Horizon : jours → swing 2–6 sem.
Taille : (300 € − 27 € de frais A/R + TTF) / 2,38 € = 114 actions → max 4 891 € · Risque réel si exécuté à 42,40 : 214 € + 27 € de frais
Compte : SRD Saxo (coût est. 15 j : 44 € = 0,9 % du notionnel, 8 % du gain brut à l'obj 1 ; levier après trade 1,5)
         alt. PEA BoursoBank : cash insuffisant (1 200 €) · alt. CTO comptant : cash 4 100 €, taille réduite à 95 actions
Risque courant + réservé après trade : 1 640 € (4,1 %) ⚠ plafond 4 % → BLOQUÉ : réduire à 100 actions ou alléger une position
État du portefeuille : Saxo synchronisé 09:13:40 (OK) · Mode : disponible
Argumentaire : … (LLM, 3 lignes, chiffres issus des données)
Vigilance : résultats du concurrent X demain ; extension 1,2 ATR au-dessus MM20 ; volume à confirmer à 09:30
Invalidation : clôture < 41,60 (OR15 low) ; RVOL < 1,5 à 10:00 ; retrait de la guidance
Sources : ActusNews (07:32), EODHD RT (09:14:05), tradingview-screener (D15, 08:59), Zonebourse (non cité)
Expire : 09:45 Paris (scan 09:15 + 30 min)
[Ordre saisi] [Watch] [Ignorer] [Voir fiche d'ordre]
```

Vérification de l'exemple (test de référence `tests/unit/test_sizing_examples.py`, paramètres de `params.example.yaml`) : entrée par ordre **limite** au prix max 42,90 (pas de glissement d'entrée) ; stop à seuil 40,60 → sortie glissée `stop_exec` = 40,60 × (1 − 0,002) = 40,519 ; R par action = 42,90 − 40,519 = **2,381 €** ; frais de dimensionnement (courtage A/R + TTF, itérés jusqu'au point fixe : 125 actions → 30,0 € → 113 → 27,2 € → 114 → 27,4 € → 114) : notionnel ≈ 4 891 € → courtage 2 × 3,91 € + TTF 0,4 % × 4 891 = 19,6 € ≈ **27 €** ; (300 − 27) / 2,381 = 114,7 → **114 actions** ; notionnel max 114 × 42,90 = 4 891 € ; risque réel si exécuté à 42,40 = 114 × (42,40 − 40,519) = 214 € (+ 27 € de frais) ; objectifs = prix max + 2 R / 3 R = 47,66 / 50,04 ; coût SRD sur 15 j = courtage 7,8 € + CRD 4 891 × 0,00023 × 15 = 16,9 € + TTF 19,6 € ≈ **44 €** ; gain brut à l'objectif 1 = 114 × 4,76 = 543 € → coûts ≈ 8 %. La ligne « BLOQUÉ » illustre le plafond de risque courant + réservé : le blocage est affiché, jamais masqué.

Une **fiche d'ordre** (bouton « Voir fiche d'ordre ») donne exactement ce qu'il faut saisir chez le courtier : compte, libellé, ISIN, sens, quantité, type d'ordre (limite au prix max en intraday ; à plage de déclenchement pour J+1), validité, **puis** l'ordre de protection à saisir après exécution (type à seuil de déclenchement, niveau, quantité). 

**États** (docs/07 §7.4, CLAUDE.md règle 9) : « Ordre saisi » crée un `order_entered` (rien d'autre) ; « Exécuté » (quantité, prix, compte, horodatage) crée une exécution **provisoire** et la position (ou l'augmente), état de protection `unprotected` pour la quantité concernée ; l'import Saxo crée l'exécution **confirmée** et la rapproche de la provisoire ; « Stop saisi » (type, niveau, quantité) met à jour la quantité protégée (`partially_protected` / `protected`). Un ordre saisi non exécuté à la clôture est rappelé (« annuler ou reconduire ? »). Une proposition expirée avec un ordre encore chez le courtier déclenche un rappel d'annulation.

## 9.2 Rapport quotidien (17:50, les 10 rubriques du projet, inchangées)

1. Synthèse macro du jour (France, Europe, États-Unis) et catalyseurs — texte LLM sur données RSS + régime.
2. État des marchés (France puis US/EU) : indices, secteurs, volatilité — tableaux calculés.
3. Analyse du portefeuille (positions FR et étrangères) : R en cours, distance au stop, actions proposées.
4. Opportunités BUY prioritaires (fiches).
5. Positions à vendre ou alléger (S1–S9).
6. Watchlist (WATCH + valeurs « à l'affût » D4 avec niveaux).
7. Valeurs à éviter (catalyseurs négatifs, résultats imminents, régime).
8. Plan d'action du jour / J+1 (ordres d'achat, de vente/allègement, stops).
9. Synthèse des ordres manuels potentiels (tableau prêt à saisir).
10. Risques globaux du jour (macro, géopolitiques, devises, SRD, couverture).

Diffusion : Telegram (résumé ≤ 12 lignes + lien), e-mail (rapport complet HTML), page `/rapport/<date>` (archivée). Le rapport sépare visuellement **Faits / Sources externes / Analyse / Opinion / Incertitudes**.

## 9.3 Brief pré-ouverture (08:45)

Régime et composantes ; positions (cours veille, stop, R, alertes SRD) ; catalyseurs classés depuis 17:40 la veille (positifs/négatifs, forte/moyenne) ; **liste d'ouverture** (≤ 15 valeurs) avec niveaux d'entrée/stop/taille pré-calculés et compte cible ; signaux externes du matin (Décision Bourse, ABC, recos brokers) ; agenda (résultats, macro, liquidation) ; rappels (stops non confirmés, import BoursoBank). Longueur Telegram ≤ 15 lignes ; e-mail complet.

## 9.4 Alertes (Telegram + e-mail selon niveau)

| Niveau | Cas | Canaux | Quota |
|---|---|---|---|
| **P1 critique** | Seuil de stop franchi (puis SELL au marché si exécution non confirmée sous 5 min) ; quantité exécutée non protégée ; catalyseur négatif fort sur position ; couverture SRD < 1,3× ; job d'ouverture non exécuté | Telegram + e-mail immédiat | Regroupées par incident : première alerte immédiate, rappel toutes les 15 min tant que l'incident dure ; incidents distincts jamais fusionnés |
| **P2 action** | BUY (portes OK) ; REDUCE objectif atteint ; décision de liquidation SRD ; trailing à modifier | Telegram (+ e-mail si non lu en 10 min : bouton « Vu ») | Selon le mode (`availability.quotas`) : ≤ 3 propositions par créneau, ≤ 6 P2 par jour en mode `disponible` ; aucune P2 intraday en `reunion` ; aucune en `absent` |
| **P3 info** | WATCH ; signaux externes reçus ; digest de midi ; rappels d'import | Telegram groupé (1 message/heure max) ; e-mail dans le rapport | ≤ 3 messages groupés/jour |
| **P4 système** | Fraîcheur des données, quotas API, sauvegardes, coûts LLM | E-mail quotidien + page `/sante` ; Telegram seulement si bloquant | — |

Règles : déduplication par (`instrument`, `compte`, `type`, `jour`), **jamais sur les P1** ; regroupement par scan ; quotas selon le mode de disponibilité (docs/02 §2.3 : `disponible` ≤ 3 propositions prioritaires par créneau, `reunion` P1 seules en temps réel, `absent` P1 + digest) ; silence 22:00–06:30 Réunion sauf P1 ; rate limit Telegram 1 msg/s ; chaque message porte l'heure Paris et Réunion et l'horodatage de marché de la cotation citée ; boutons inline (Vu / Watch / Ordre saisi / Exécuté / Stop saisi / Ignorer / Snooze 30 min).

## 9.5 Interface web (mobile-first, derrière Cloudflare Access)

| Page | Contenu |
|---|---|
| `/` **Trois décisions du jour** | En tête : les 3 décisions utiles aujourd'hui (une entrée préparée, un stop à remonter, une position à alléger…), classées par qualité d'entrée et urgence, avec bouton d'action ; puis : mode de disponibilité (bouton), régime (feu + composantes), état des données (badges + horodatage de marché), positions (R, distance stop, état de protection), cash et exposition par compte, risque ouvert cumulé / plafond, scénario de gap, levier/couverture SRD, ordres saisis non exécutés, prochaine liquidation |
| `/brief` | Brief du matin (dernier) + liste d'ouverture avec niveaux, boutons de décision |
| `/opportunites` | Cartes BUY/WATCH du jour, filtres (détecteur, place, compte), fiche d'ordre, graphique lightweight-charts (bougies + niveaux entrée/stop/objectifs, volume, MM) |
| `/positions` | Tableau + graphique par position, état d'exécution (`order_entered` / `filled_partial` / `filled`) et de protection (`unprotected` / `partially_protected` / `protected`), quantité détenue vs couverte par le stop, historique des stops, coût SRD cumulé, jours restants avant liquidation, boutons (exécuté, stop saisi, stop modifié, réduire, clôturer) |
| `/rapport/<date>` | Rapport 10 rubriques archivé, navigation par date |
| `/watchlist` | Watchlist momentum et « à l'affût » D4 ; export TradingView (`EXCHANGE:TICKER` par ligne) ; ajout manuel |
| `/journal` | Trades clos, R-multiples, notes, tags, captures (upload), filtres |
| `/kpi` | P&L (docs/10) |
| `/fiscal` | KPI fiscaux (docs/11) |
| `/revue` | Digest hebdo / mensuel, post-mortem des signaux (docs/12) |
| `/parametres` | Capital pilote, cash additionnel, cash PEA, blocages temporaires, abonnements (revue), seuils (lecture seule sauf `overrides` tracés) |
| `/sante` | Jobs (dernier run, statut), fraîcheur par source, quotas API, coûts LLM du mois, sauvegardes, version des paramètres, tunnel |
| `/import` | Dépôt CSV BoursoBank, saisie manuelle d'exécutions |
| `/ecarts` (phase 2, période de fonctionnement en parallèle) | Chaque jour : valeurs proposées par l'outil (heure, prix) vs valeurs des newsletters (heure de réception, prix), recouvrement, précocité — sert à la validation technique A2.7 (pas de conclusion de rentabilité avant docs/12 §12.2) |

Exigences : temps de rendu < 2 s en 4G ; aucune donnée sans badge de statut ni horodatage de marché ; heures affichées en Réunion (Paris entre parenthèses) ; thème clair/sombre ; aucune action d'ordre (aucun bouton « acheter/vendre » — uniquement des déclarations : « Ordre saisi », « Exécuté », « Stop saisi »).

## 9.6 API (JSON, usage interne + n8n + webhooks)

`GET /api/health`, `GET /api/jobs`, `POST /api/ingest/newsletter`, `POST /api/ingest/rss`, `POST /api/webhooks/tradingview` (secret), `GET /api/opportunities?date=`, `GET /api/positions`, `POST /api/orders` (déclaration « ordre saisi »), `POST /api/executions` (déclaration d'exécution, idempotente par identifiant unique), `POST /api/protections`, `POST /api/decisions`, `POST /api/mode`, `GET /api/report/<date>`, `GET /api/instruments/<isin>` (fiche + statut d'éligibilité), `GET /api/prices/<isin>?tf=1m|5m|1d`. Authentification : jeton Cloudflare Access vérifié + jeton applicatif pour n8n/webhooks.
