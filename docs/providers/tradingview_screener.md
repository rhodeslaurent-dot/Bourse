# TradingView screener — fiche de qualification

## Pré-qualification documentaire (09/2026, docs/04 §4.1, §4.5)

| Rubrique | Annoncé | À vérifier |
|---|---|---|
| Fonction attendue | Scan de **tout l'univers PEA** en ≤ 6 requêtes (une par marché, `limit` ≥ 1 000) : gap, RVOL approx., cassures, RS, secteur, prochaine date de résultats ; clôtures **provisoires** à 17:45 | — |
| Marchés | `france, germany, netherlands, belgium, italy, spain, portugal, sweden, denmark, finland, norway, austria, poland` | Nombre de lignes par marché ; couverture des 10 valeurs |
| Délai | ~15 min sans cookie de session → statut **`delayed`** toujours ; jamais de fiche d'ordre sur cette donnée | Écart `close` screener vs cours Saxo à la même minute |
| Champs | `name, close, open, gap, change, change_from_open, volume, relative_volume_10d_calc, average_volume_10d_calc, market_cap_basic, Perf.*, SMA20/50/200, ATR, price_52_week_high, sector, earnings_release_next_date` | Définition de `gap` et de `relative_volume_10d_calc` (périmètre du volume : primaire ?) — **ne jamais mélanger avec le RVOL calculé sur EODHD** |
| Droits d'usage | Endpoint **non officiel** ; TradingView ne fournit pas d'API publique de données ; risque de bannissement si abus → ≤ 6 requêtes/scan, ≥ 60 s entre scans, désactivable (`data.tradingview_screener`) | Codes 429/403 observés |
| Coût | Gratuit | — |
| Repli | EODHD REST différé sur l'univers (≈ 900 appels/scan, dans le quota) | — |
| Implémentation | Appel direct `scanner.tradingview.com/{market}/scan` (même format que le paquet MIT `tradingview-screener`, sans dépendance `requests` → tests respx) | ADR-002 |
| Code | `app/data/providers/tradingview_screener.py` | Tests : limite de requêtes, intervalle, parsing |

## Qualification par prototype (à compléter)

| Séance | Lignes FR/DE/NL | Délai constaté | Écart close vs Saxo | Volume vs Saxo (ratio) | Erreurs (429/403) |
|---|---|---|---|---|---|
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |
