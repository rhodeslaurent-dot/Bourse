# 10 — Portefeuille et KPI de suivi des gains et pertes (priorité 2)

## 10.1 Données de base

- `accounts` : PEA BoursoBank, CTO Saxo (sous-comptes comptant / SRD), devise EUR.
- `orders` (ordres saisis chez le courtier, déclarés ou importés) : compte, instrument, sens, quantité, type, prix limite / seuil, validité, `broker_order_id?`, état ∈ {entered, partially_filled, filled, cancelled, expired, rejected}, `proposal_id?`.
- `trades` (exécutions) : `kind ∈ {declared, confirmed}` ; `declared_uid` (déclaration manuelle : hash `account+isin+ts+qty+price`, idempotent pour les doubles clics) et `broker_fill_id` (import courtier) ; une exécution déclarée est **rapprochée** d'une exécution confirmée (docs/08 §8.6) et les deux références sont conservées sur la même ligne — l'unicité d'un identifiant ne suffit pas à éviter les doublons entre Telegram et API, seul le rapprochement le fait ; compte, instrument, sens, quantité, prix, devise, taux de change, frais de courtage, TTF, horodatage, mode (`srd` / `comptant`), source (`telegram`, `saxo_api`, `csv_boursobank`, `manual`), `order_id?`, `proposal_id?`, `detector`, `external_sources[]`.
- `positions` : état d'exécution ∈ {filled_partial, filled, closed} et état de protection ∈ {unprotected, partially_protected, protected}, indépendants, suivis par quantité ; ouvertes (quantité détenue, quantité couverte par le stop, PMP d'entrée, stop courant et type, `protected_at`, R initial, date d'ouverture, mode, compte) et fermées (prix de sortie moyen, date, motif de sortie ∈ {stop, objectif, trailing, thèse, temps, résultats, liquidation, discrétionnaire}).
- `cash_snapshots` : cash par compte et par jour (Saxo auto, PEA saisi/importé).
- `costs` : courtage, CRD (calculée quotidiennement sur les positions SRD à partir de `params.srd.crd_daily_rate`, puis rapprochée des prélèvements Saxo à l'import), prorogations, TTF, abonnements (`subscriptions`).
- `prices_eod` pour la valorisation quotidienne ; `fx_rates` (EUR/SEK, EUR/DKK, EUR/NOK, EUR/PLN, EUR/USD) EOD.

## 10.2 KPI calculés (quotidiens, `kpi_daily`, et par période)

| Famille | KPI | Détail |
|---|---|---|
| Performance | P&L réalisé, latent, total (€ et % du capital pilote) ; par compte, par détecteur, par source externe, par place, par secteur, par horizon, par mode (SRD/comptant) | Après frais ; avant/après impôt estimé (docs/11) |
| Comparaison | Performance vs CAC 40 GR, STOXX Europe 600 NR, portefeuille « 60 % CAC + 40 % STOXX » ; alpha simple ; courbe d'équité | Depuis le début, YTD, 3 mois, 1 mois, semaine |
| Qualité | Taux de réussite ; gain moyen / perte moyenne (€ et R) ; **espérance par trade (R)** ; profit factor ; ratio gain/perte ; R-multiple moyen ; médiane | Global et par détecteur/source ; fiabilité annoncée selon le nombre de trades (< 30 : « indicatif ») |
| Risque | Drawdown d'équité (vs plus haut d'équité TWR, versements neutralisés) et pertes de période en R (coupe-circuit) ; volatilité de l'équité ; exposition moyenne ; levier SRD moyen et max ; couverture minimale observée ; nombre de positions ; pertes > 1,2 R (stops mal exécutés) ; slippage moyen à l'entrée et à la sortie | |
| Coûts | Courtage total, CRD, prorogations, TTF, abonnements ; coûts / P&L brut ; coût SRD annualisé | Par mois |
| Discipline | % de trades avec stop initial, délai exécution → protection (médiane, % sous 15 min, 100 % avant la clôture), dérogations aux portes, trades hors créneaux, trades sur proposition vs discrétionnaires | |
| Activité | Nombre de propositions BUY / prises / ignorées ; temps entre alerte et « Ordre saisi », puis entre « Exécuté » et « Stop saisi » ; nombre de trades par semaine | Alimente le « ≤ 1 h/jour » |

## 10.3 Valorisation et P&L

- P&L latent = (cours EOD − PMP) × quantité − frais estimés de sortie ; positions SRD : P&L calculé sur le notionnel, CRD cumulée déduite ; devise ≠ EUR : P&L de change isolé.
- **Équité** = cash + positions au comptant valorisées + P&L latent SRD − CRD courue (docs/08 §8.6) ; le notionnel SRD n'entre jamais dans l'équité. Engagements SRD, couverture requise et disponible sont des lignes séparées.
- Le P&L réalisé d'une position SRD est reconnu à la clôture de la position (et fiscalement au règlement, docs/11).
- Courbe d'équité = cash + valorisation, par compte et consolidée ; versements/retraits neutralisés (TWR) pour la comparaison aux indices.

## 10.4 Rapprochement

- Saxo : synchronisation en séance (5 min, après déclaration, avant proposition) et rapprochement complet du soir des exécutions confirmées avec les `trades` déclarés (tolérances docs/08 §8.6) ; les exécutions non déclarées sont créées et signalées (« trade discrétionnaire ? à documenter »).
- BoursoBank : import CSV → même logique ; entre deux imports, l'état PEA est marqué « déclaratif ».
- Écarts de cash > 1 % → alerte P3.

## 10.5 Pages et exports

`/kpi` : cartes (P&L YTD, espérance R, profit factor, drawdown, coûts), courbe d'équité vs indices, tableau par détecteur/source/compte, heatmap mensuelle P&L, distribution des R-multiples. Exports CSV/XLSX de toutes les tables (usage personnel). Digest hebdo (docs/12).
