# Saxo OpenAPI — fiche de qualification (lecture seule)

## Pré-qualification documentaire (09/2026, docs/04 §4.1, §4.5)

| Rubrique | Annoncé | À vérifier |
|---|---|---|
| Fonction attendue | **Portefeuille en lecture** (positions, ordres en attente dont stops, cash, exécutions, positions fermées) — P0 phase 1 ; **prix temps réel Euronext** (infoprices + streaming) si abonnement données L1 — P1 ; graphiques intraday `chart/v1/charts` | — |
| Marchés | Euronext (L1 7 €/mois, remboursé si ≥ 4 transactions/mois/bourse), Xetra L1 7 €, Nasdaq Nordic 7 € | Champ `DelayedByMinutes` sur les 10 valeurs |
| Délai | Temps réel flux primaire si abonné (`DelayedByMinutes = 0`) ; sinon différé 15 min | Mesure `LastUpdated` vs horloge |
| Champs | `Quote.Bid/Ask/Mid/DelayedByMinutes/PriceTypeAsk`, `PriceInfoDetails.LastTraded/Volume/Open/High/Low`, `LastUpdated` | Sémantique de `LastUpdated` (dernier échange ou dernière mise à jour du carnet ?) ; **prix théorique d'ouverture en pré-ouverture 07:15–09:00** (à valider empiriquement) |
| Périmètre | Carnet **primaire** Euronext (volumes officiels) | Référence pour le ratio de volume des autres providers |
| Accès | Application « usage personnel » sur developer.saxo : simulation d'abord (**aucune donnée de marché en simulation**), puis demande de clé live, compte financé ; OAuth Authorization Code ; refresh token chiffré (Fernet) | Délai d'approbation (chemin critique) ; rate limits (en-têtes `X-RateLimit-*`) |
| Interdits | `trade/v2/orders` et toute écriture : refusés par `SaxoClient` avant tout appel réseau (`ReadOnlyViolation`) ; test `tests/test_no_trading_endpoints.py` | Permissions demandées à la création de l'app = lecture seule |
| Coût | API gratuite ; données 0–7 €/mois | — |
| Repli | Prix : EODHD WS ; portefeuille : saisie Telegram/web + rapprochement différé | — |
| Code | `app/data/providers/saxo.py` (`SaxoClient`, `SaxoPriceProvider`, `TokenStore`) | Tests : garde lecture seule, parsing infoprices |

## Qualification par prototype (à compléter)

| Séance | `DelayedByMinutes` | Délai mesuré | Cours vs interface | Volume vs interface | Pré-ouverture (prix théorique) | Streaming : reconnexion |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |
