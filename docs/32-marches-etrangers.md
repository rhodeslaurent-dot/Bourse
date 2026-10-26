# 32 — Marchés étrangers : régime par place, indices, places hors PEA

> Décision de l'utilisateur (26/10/2026) : « je veux que l'analyse et l'outil fonctionnent également sur les marchés étrangers ; actuellement le contexte macro sur le CAC 40 bloque tout investissement, et le marché français est trop limité pour identifier suffisamment d'opportunités ».

## 32.1 Constat

- **Un seul régime pour tout l'univers.** Le régime de marché (vert, orange, rouge) était calculé une fois par jour, sur le CAC 40, le STOXX 600 et la largeur de tout l'univers, puis appliqué à toutes les valeurs. Un CAC 40 rouge fermait le portail (`restreint_plus`) même pour une valeur allemande, suédoise ou néerlandaise dont le marché allait bien.
- **Les séries d'indices n'étaient pas rechargées.** Aucun job ne tenait à jour les indices. Le backtest n° 2 a classé tous ses trades en régime « inconnu ».
- **Les conditions publiées par les lettres** (marché français) s'appliquaient aussi aux autres places.

## 32.2 Règles

1. **Un régime par place.** Chaque place est jugée sur son propre indice (`regime.indices_by_mic`). Ses quatre composantes (docs/06 §6.2) sont :
   - l'indice de la place et le STOXX 600 par rapport à leur moyenne mobile 50 ;
   - la largeur des valeurs suivies de cette place, calculée seulement à partir de `breadth_min_values` valeurs (10) ;
   - les jours de distribution et la volatilité de l'indice de la place.

   Le job `daily_regime` écrit une ligne par place suivie, plus la ligne **Europe** (`market = EU`, STOXX 600 et tout l'univers).
2. **Une valeur suit le régime de sa place.** Le portail (R10) et le risque conjoncturel d'une proposition se calculent avec ce régime et avec la variation sur 5 séances de l'indice de la place. Une place sans régime propre (indice absent ou périmé depuis plus de 5 jours) suit le régime européen. La fiche le dit : « régime européen : pas d'indice propre à cette place ».
3. **Le niveau de risque du jour** (docs/28), les rapports et l'assistant lisent le régime **européen**.
4. **Lettres :** leurs conditions publiées ne s'appliquent qu'aux places de `market_gate.letters_markets` (Paris par défaut). Ailleurs, le portail dépend du régime de la place et du niveau conjoncturel (raison affichée : « lettres non applicables à cette place »).
5. **Indices :**
   - `eod_backfill_check` charge chaque jour, de façon incrémentale, l'indice de chaque place et le STOXX 600 (historique depuis 2016 au premier passage). Ils sont stockés dans `prices_eod` sous un pseudo-ISIN (`IDX.GDAXI`…), sans entrer dans l'univers.
   - Un code refusé par EODHD est signalé (alerte P4, `index-check`), jamais remplacé par un autre indice.
   - **Secours Yahoo pour le même indice** (26/10/2026) : quand EODHD échoue, ne renvoie rien ou laisse la série périmée, l'indice est lu sur Yahoo si `indices_by_mic.<MIC>.yahoo` le nomme (FTSE MIB `FTSEMIB.MI`, FTSE 100 `^FTSE` : absents du forfait EODHD ; ISEQ `^ISEQ` : arrêté chez EODHD le 06/10/2022). Chaque séance garde sa source (`yfinance`), affichée par `index-check`.
   - **Indice périmé** : plus ancien que `regime.index_max_age_days` (5 jours), il ne donne **aucun** régime ; la place suit le régime européen (défaut corrigé le 26/10/2026 : un régime « du jour » pouvait être calculé sur des clôtures de 2022). `index-check` l'affiche « périmé », jamais « à jour ».
   - `index-check --search <texte>` liste les codes d'indices EODHD qui contiennent ce texte (code ou nom).
6. **Backtest et recherche :** chaque trade est classé selon le régime de sa place (haussier ou baissier, au-dessus ou au-dessous de la moyenne 200 de son indice). À défaut, il est classé selon le régime européen.

## 32.3 Places hors PEA (compte-titres Saxo seulement)

- `universe.markets_us: [US]` (NYSE, NASDAQ, jamais l'OTC) et `universe.markets_ex_pea: [LSE, SW]` (Londres, Zurich) chargent ces places. Leurs filtres propres sont dans `universe.market_overrides`.
- **Activées par défaut** dans `config/params.example.yaml` (décision de l'utilisateur du 26/10/2026, droits Saxo confirmés). Dans le `params.yaml` du PC, on les active **après** l'exécution du banc de recherche (docs/31), pour ne pas changer les données pendant la mesure (commandes en §32.4).
- Une valeur cotée sur plusieurs places suivies est suivie sur **sa place d'origine** (préfixe ISIN : US → New York, GB → Londres, CH → Zurich). La cotation secondaire de Xetra n'est retenue que si la place d'origine n'est pas suivie (docs/15, 25/10/2026).
- **Exécution sur Xetra** (décision du 26/10/2026 : « je préfère investir sur les marchés principaux, mais ok pour Xetra s'ils ne sont pas activés ou si l'écart de change est déterminant ») :
  - `universe_refresh` garde la cotation en euros d'une valeur hors euro comme **alias** (`instrument_aliases`, fournisseur `eodhd_alt`, places de `universe.alt_listing.exchanges`), jamais comme cotation suivie ;
  - pour une proposition dimensionnée, la fiche affiche le change aller-retour évité (`brokers.saxo.fx_fee_pct` à l'achat au prix maximal et à la vente à l'objectif) et sa part du gain net. La cotation en euros est dite **à privilégier** quand cette part atteint `universe.alt_listing.min_share_of_gain` (10 %) ; sinon la place principale est conservée ;
  - quand le change seul fait refuser l'entrée (prix maximal sous le stop, ou ratio insuffisant), la fiche le dit : « admise sans change sur la cotation en euros ». **Information seulement** : la proposition reste refusée et doit être réévaluée sur le cours de cette cotation ;
  - les niveaux en euros sont **indicatifs** (convertis au taux du jour) ; la liquidité et la fourchette de la cotation en euros ne sont pas vérifiées : à contrôler avant toute saisie.
- **Horaires :** New York ouvre de 15 h 30 à 22 h (heure de Paris), soit de 18 h 30 à minuit à La Réunion. Le calendrier `XNYS` est déjà encodé.
- **Change :** les frais de change Saxo (`brokers.saxo.fx_fee_pct`) sont dans les coûts. La marge de change réduit la quantité, jamais le budget de risque (règle 7).

## 32.4 Commandes

```
docker compose exec app python -m app.cli index-check
```

Charge les indices, puis affiche pour chaque place le code EODHD, le nombre de séances chargées et la date de la dernière séance. Un code en échec se corrige dans `config/params.yaml › regime.indices_by_mic`.

```
uv run python -m app.cli params-init --update --email <adresse> --set "universe.markets_us=[US]" --set "universe.markets_ex_pea=[LSE, SW]"
docker compose exec app python -m app.cli run-job universe_refresh
docker compose exec app python -m app.cli backfill --from 2016-01-01 --exchanges US,LSE,SW --missing-only
docker compose exec app python -m app.cli run-job daily_regime
```

`backfill --exchanges` limite le chargement aux places citées (codes EODHD) ; `--missing-only` ne charge que les valeurs dont l'historique ne remonte pas à `--from` (premier jour stocké plus de 10 jours après). À lancer après `universe_refresh`, qui a ajouté les nouvelles valeurs au référentiel.

**Compte Saxo** : pour New York, vérifier que le formulaire fiscal américain W-8BEN est signé dans l'espace client (sinon retenue à la source de 30 % au lieu de 15 % sur les dividendes).

## 32.5 Critères d'acceptation

- **A32.1** Une valeur de Francfort n'est pas bloquée par un CAC 40 rouge ; une valeur de Paris l'est (test `tests/integration/test_regime_per_market.py`).
- **A32.2** Une place sans régime propre suit le régime européen, et la fiche le dit (test).
- **A32.3** Les indices se chargent de façon incrémentale ; un code inconnu est signalé, jamais remplacé (test).
- **A32.4** Les conditions des lettres ne ferment pas une place qu'elles ne couvrent pas (test).
- **A32.6** Un indice absent du forfait EODHD est lu sur Yahoo (même indice, source visible) ; un indice périmé ne donne pas de régime (tests `test_regime_per_market.py`).
- **A32.5** Une valeur en dollars cotée aussi sur Xetra montre le change évité et sa part du gain net ; la cotation en euros n'est jamais la cotation suivie ; une valeur en euros n'a pas d'alternative (test `tests/integration/test_alt_listing.py`).
