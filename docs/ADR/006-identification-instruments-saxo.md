# ADR-006 — Identification des instruments Saxo sans ISIN

Statut : **accepté** · Date : 2026-10-20

## Contexte

Le premier `saxo-check` en live (3 positions) n'a résolu aucun ISIN : `ref/v1/instruments/details/{Uic}/{AssetType}` répond 200 mais sans champ `Isin`. La FAQ Saxo OpenAPI (« Can I retrieve ISINs through the OpenAPI? ») confirme que **l'API ne renvoie jamais d'ISIN** (restriction de licence : donnée de marché réservée aux partenaires). L'API accepte en revanche un ISIN comme mot-clé de recherche et renvoie le `Uic`.

Or le référentiel de l'outil est indexé par ISIN (règle 9 : rapprochement déclaration ↔ exécution courtier sur le même instrument).

## Décision

1. Le connecteur Saxo identifie un instrument par **`Uic` + `Symbol`** (`VK:xpar` = ticker + MIC en minuscules). Aucun appel à `instruments/details` pour l'ISIN.
2. La correspondance vers l'ISIN est faite par le service `instruments.resolve_broker_isin`, dans cet ordre, sans jamais deviner :
   1. alias persistant `instrument_aliases(provider="saxo_uic", alias=<Uic>)` ;
   2. `instruments.ticker_saxo == Symbol` ;
   3. `instruments.ticker_local == ticker` **et** `instruments.mic == MIC` (candidat unique, sinon non résolu) ;
   4. `config/instruments_map.yaml` : clé `saxo: VK:xpar` ou `saxo_uic: 117274`, à défaut `eodhd` (ticker avant le point) + `mic`.
   Toute résolution est persistée comme alias `saxo_uic` (et `ticker_saxo`), si bien qu'un ordre ou une position fermée sans symbole se résout ensuite par le `Uic`.
3. Un symbole dont la place est `xxxx` (ex. `LOCK-12806:xxxx`) désigne un instrument **radié ou bloqué** : il n'est pas suivi, la raison est affichée.
4. Une position Saxo non résolue est **visible** : ligne « non suivie » dans `saxo-check`, avertissement dans les logs, entrée `unmapped` dans le rapport `broker_sync_runs`, alerte P3 `broker_unmapped` (dédupliquée par jour). Elle n'est jamais importée sous un ISIN approximatif.

## Conséquences

- Avant le premier `universe_refresh` (clé EODHD), les positions existantes se déclarent dans `config/instruments_map.yaml` (clé `saxo:`) ; après, la correspondance ticker + place est automatique.
- La qualification (A0.7) et les Uic de `tests/fixtures/qualification/symbols.yaml` restent inchangés.
- Tests : `tests/unit/test_saxo_isin_mapping.py` ; les tests d'intégration Saxo n'enregistrent plus de réponse `instruments/details`.
