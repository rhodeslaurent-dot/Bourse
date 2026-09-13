# yfinance — fiche de qualification (secours EOD uniquement)

## Pré-qualification documentaire (09/2026, docs/04 §4.1, docs/01 §1.3)

| Rubrique | Annoncé | À vérifier |
|---|---|---|
| Fonction attendue | **Secours EOD** : backfill gratuit, contrôle croisé de l'EOD EODHD ; `yfinance.calendar` en recoupement des dates de résultats | — |
| Marchés | Toutes les places PEA (`.PA`, `.AS`, `.BR`, `.DE`, `.MI`, `.MC`, `.ST`, `.CO`, `.HE`, `.OL`) | Couverture des 10 valeurs ; `adj_close` cohérent |
| Délai | Différé 15–20 min ; EOD fiable | Jamais utilisé en intraday (le provider lève `ProviderError`) |
| Fiabilité | Lib non officielle ; refonte Yahoo et quotas 429 en 2025 ; dividendes/fondamentaux peu fiables | Taux d'erreur sur 3 séances |
| Droits d'usage | « personal use only » | — |
| Coût | Gratuit | — |
| Statut de donnée | `data_status=eod`, `source=yfinance` : dégradé, toujours visible | — |
| Code | `app/data/providers/yfinance_eod.py` | Tests avec téléchargeur factice |

## Qualification par prototype (à compléter)

| Séance | Valeurs récupérées | Écart close vs EODHD | Écart volume vs EODHD | Erreurs |
|---|---|---|---|---|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |
