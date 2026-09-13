# EODHD — fiche de qualification

## Pré-qualification documentaire (09/2026, docs/04 §4.1)

| Rubrique | Annoncé par le fournisseur | À vérifier par prototype |
|---|---|---|
| Fonction attendue | EOD historique (scoring, RS, ATR), intraday 5 min historique, **flux temps réel WebSocket `ws/eu`** (positions + liste d'ouverture), splits/dividendes, bulk EOD par bourse | — |
| Marchés couverts | 70 bourses dont PA, AS, BR, XETRA, MI, MC, ST, CO, HE, OL, LS, VI, WAR ; WS temps réel : 18 marchés européens via Cboe Europe (~9 400 symboles, annonce 26/08/2026) | Liste des MIC réellement reçus sur les 10 valeurs |
| Délai annoncé | REST `real-time` : différé 15–20 min ; WS : temps réel ; EOD : après clôture | Écart `market_timestamp` vs horloge sur 3 séances (`delay_stats`) |
| Champs | REST : `code, timestamp, open, high, low, close, volume` ; WS : `s, p, v, t` (trade), statut de suspension ; intraday : `timestamp, o/h/l/c/volume` | Sémantique de `p` (dernier échange Cboe) et de `t` (horodatage d'échange ou de diffusion ?) |
| Périmètre des volumes | WS = **Cboe consolidé (BXE/CXE/DXE)**, pas le carnet Euronext ; EOD = marché primaire | Ratio volume WS / volume Saxo (`volume_perimeter_hint`) ; **le prix d'ouverture officiel vient de l'EOD/Saxo**, jamais du premier trade Cboe |
| Rattrapage de ticks manqués | « backfill REST » annoncé pour le flux | **Existence réelle d'un endpoint de rattrapage du flux** (pas l'API historique intraday) : URL, fenêtre, format. Sans preuve → trou marqué, jamais rebouché |
| Droits d'usage | Usage personnel, redistribution interdite | — |
| Coût daté | EOD+Intraday 29,99 $/mois (≈ 299,90 $/an) ; All-in-One 99,99 $/mois (fondamentaux, news, calendrier) — relevé 09/09/2026 | Facture |
| Limites | 100 000 appels/jour, 64 connexions WS/token, 50 symboles/connexion | Comportement 402/429 (`QuotaExceeded`) |
| Repli prévu | EOD : yfinance (contrôle croisé) ; temps réel : Saxo streaming (P1) ; intraday : screener différé « à vérifier » | — |
| Code | `app/data/providers/eodhd.py` (`EodhdProvider`, `EodhdWebSocket`, `BarAggregator`) | Tests `tests/unit/test_providers.py` (respx) |

## Qualification par prototype (à compléter — 3 séances)

| Séance | Délai REST (médiane / p90) | Délai WS (médiane / p90) | Écart cours vs Saxo | Ratio volume WS/Saxo | Trous / barres incomplètes | Déconnexion → reprise | Rattrapage prouvé ? |
|---|---|---|---|---|---|---|---|
| 1 | | | | | | | |
| 2 | | | | | | | |
| 3 | | | | | | | |

Test d'acceptation : comparaison à l'interface Saxo sur 10 valeurs FR/DE/NL (cours, volumes, horodatages), comportement à la
déconnexion/reprise (couper le réseau 2 min pendant la séance), trous.
