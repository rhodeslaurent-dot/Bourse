# ADR-002 — Sources de données retenues (EOD, intraday, temps réel) et périmètre RVOL

Statut : **proposé — en attente des mesures de qualification (A0.7, 3 séances)** · Date : 2026-09-13

## Contexte

`docs/04` sélectionne EODHD (socle payant), Saxo OpenAPI (portefeuille + prix primaires), tradingview-screener (scan large différé)
et yfinance (secours EOD). La règle 2 impose que chaque donnée porte source, horodatage de marché, périmètre et statut ; docs/04 §4.3 bis
impose une qualification par prototype avant de figer les sources. Les fiches sont dans `docs/providers/`.

## Proposition (à confirmer par les mesures)

| Usage | Source proposée | Périmètre | Statut | Repli |
|---|---|---|---|---|
| EOD officiel (06:45) | **EODHD** `eod-bulk-last-day` par bourse | primaire | `eod`, `official=true` | yfinance (contrôle croisé, dégradé) |
| Clôture provisoire (17:45) | tradingview-screener | à qualifier | `delayed`, `official=false` | EODHD REST différé |
| Intraday historique 5 min (référence RVOL_5/15 à 08:45, rejeu) | EODHD `intraday` | primaire | `delayed` (finalisé après clôture) | — (jamais pour reboucher un trou live) |
| Temps réel (positions + liste d'ouverture + watchlist ≤ 150) | **EODHD WebSocket `ws/eu`** | **Cboe consolidé** | `realtime` | Saxo streaming (primaire) si abonné L1 |
| Prix d'ouverture officiel | EOD / Saxo | primaire | — | gap calculé au premier trade Cboe puis **corrigé** |
| Scan large (≤ 6 requêtes) | tradingview-screener | à qualifier | `delayed` → candidats « à vérifier » | EODHD REST différé |
| Portefeuille CTO | Saxo OpenAPI (lecture) | — | — | déclarations Telegram/web |

**Périmètre RVOL** : `RVOL_5/15` = volume cumulé des 5/15 premières minutes **du flux WS (Cboe)** / moyenne de la même fenêtre sur 20 séances
**calculée sur le même flux** (barres 1 min stockées). Tant que 20 séances de barres WS ne sont pas stockées, la référence vient du backfill REST
intraday 5 min (périmètre primaire) et l'indicateur est marqué `approx` avec `perimeter_mismatch` — jamais présenté comme un RVOL exact.
`RVOL_jour` (EOD) = volume EODHD primaire / moyenne 20 j EODHD primaire. Un changement de source en cours d'indicateur produit
`source_switched` + alerte P4 (`data.silent_source_switch_allowed: false`).

**Ce que l'on ne fait pas** : pas de rebouchage d'un trou du flux par l'API historique intraday ; pas de changement de source silencieux ;
le rattrapage des ticks manqués par l'endpoint dédié du flux EODHD n'est retenu **que si la qualification prouve qu'il existe et fonctionne**
(sinon `complete_bar=false` et statut `gap_in_data` sur les indicateurs traversant le trou) ; yfinance jamais en intraday ; tradingview-screener
jamais indispensable (désactivable, repli EODHD REST).

## Choix d'implémentation déjà pris

- Screener appelé directement (`scanner.tradingview.com`, format du paquet MIT `tradingview-screener`) plutôt que via le paquet : pas de
  dépendance `requests`, réponses enregistrées avec respx, mêmes garde-fous (≤ 6 requêtes, ≥ 60 s).
- `SaxoClient` refuse tout endpoint hors liste de lecture et toute méthode non-GET (sauf abonnements de prix) avant tout appel réseau.
- Statut de donnée calculé par `classify_status(market_timestamp, now, declared_delay, stale_after)` : le délai **déclaré** du fournisseur
  et l'âge de l'horodatage de **marché** décident ; l'heure de téléchargement n'intervient pas.

## À décider après les 3 séances (remplir depuis `data/qualification/<date>.md`)

1. Délai réel du WS EODHD (médiane/p90) et de Saxo ; source temps réel retenue.
2. Ratio de volume WS/Saxo par valeur → périmètre RVOL confirmé ou non.
3. Existence de l'endpoint de rattrapage du flux (URL, fenêtre) → activation ou non du rattrapage.
4. Prix théorique d'ouverture Saxo en pré-ouverture : exploitable pour D6/liste d'ouverture ?
5. Couverture réelle des 10 valeurs par le screener et délai constaté.
