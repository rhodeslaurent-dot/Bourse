# 06 — Moteur de signaux : régime, détecteurs, scoring

Principe : **4 détecteurs d'entrée = 4 stratégies** (chacune avec ses paramètres, ses statistiques et son post-mortem), + 1 détecteur de catalyseurs pré-ouverture, + 1 intégrateur de signaux externes (newsletters), + des détecteurs de sortie. Tous les seuils sont dans `config/params.yaml › detectors.*` ; toutes les définitions d'indicateurs sont en annexe et testées sur fixtures.

## 6.1 Objets

- `Signal` : `instrument`, `detector`, `ts_utc`, `timeframe ∈ {premarket, intraday, eod}`, `direction ∈ {long}`, `entry_zone (low, high)`, `stop_initial`, `targets[]`, `horizon ∈ {days, swing_2_6w, medium}`, `evidence{}` (valeurs des indicateurs ayant déclenché), `data_status`, `catalyst_id?`, `external_refs[]`.
- `Score` : sous-scores C (0–30), T (0–25), F (0–20), R (0–15), M (0–10), `total`, `confidence (1–5)`, `risk_level ∈ {faible, moyen, élevé}`, `explanations[]`.
- `Proposal` (docs/07 et 08 ajoutent taille, compte, blocages).

## 6.2 Régime de marché (calculé à 07:30 et 17:50, `domain/regime`)

| Indicateur | Vert | Orange | Rouge |
|---|---|---|---|
| CAC 40 et STOXX Europe 600 vs MM50 | Les deux au-dessus | Un seul | Les deux en dessous |
| Breadth univers : % de valeurs au-dessus de la MM50 | ≥ 50 % | 35–50 % | < 35 % |
| Distribution days (séances de baisse ≥ 0,2 % avec volume > veille) sur 25 séances, CAC 40 | ≤ 3 | 4–5 | ≥ 6 |
| Volatilité (VSTOXX ou ATR14/close du STOXX 600 en percentile 1 an) | < 60e pct | 60–85e | > 85e |

Agrégation (paramètres `regime.*`) : **rouge** si ≥ `regime.red_min_components` (2) composantes rouges ; **orange** si ≥ 1 composante orange ou 1 rouge ; **vert** sinon. Effets : **Vert** = tailles normales ; **Orange** = taille × 0,5, BUY seulement si ratio d'admission ≥ `scoring.buy_min_ratio_orange` ; **Rouge** = pas de nouvelle entrée (WATCH seulement), rappel de resserrer les stops. Le régime est affiché en tête de chaque brief/rapport avec ses composantes.

## 6.3 Détecteurs d'entrée

### D1 — Gap + catalyseur à l'ouverture (« episodic pivot »)
- Fenêtre : 09:00–10:30 Paris, scans à 09:05, 09:15, 09:30, 10:00, 10:30. **Au scan de 09:05, seules 5 minutes existent : on utilise `OR5` et `RVOL_5` ; à partir de 09:15, `OR15` et `RVOL_15`.**
- Conditions : `gap_open = open / close_prev − 1 ≥ +3 %` (large caps, ADV ≥ 5 M€) ou **≥ +5 %** (autres) ; **RVOL_5 ≥ 2,0 (09:05) puis RVOL_15 ≥ 2,0** — référence 20 j calculée à 08:45 par backfill REST intraday 5 min pour les valeurs de la liste d'ouverture ; pour le reste de l'univers (screener différé), approximation `volume cumulé / (ADV20 × fraction de séance écoulée) ≥ 2,0`, statut `approx` dans `evidence` ; **catalyseur identifié** (D6 : communiqué, résultats, contrat, relèvement de guidance, reco de broker) classé `positif`, ampleur `forte` ou `moyenne` ; prix ≥ `OR5_high` au moment du scan ; pas de publication de résultats **à venir** dans les 48 h (des résultats publiés le matin même sont le catalyseur, pas un blocage) ; pas dans un régime rouge.
- Niveaux : zone d'entrée = cassure de `OR_high` jusqu'à `OR_high × 1,01` (**prix max**), où `OR_high` = `OR5_high` au scan de 09:05 et `OR15_high` ensuite ; stop initial **structurel** = `day_low − 0,25 × ATR14` (repli `entrée − 1,0 × ATR14` seulement si `day_low` indisponible) ; si la distance dépasse `1,5 × ADR20` sous le prix max → signal rejeté (WATCH « stop trop large ») ; objectif 1 = prix max + 2 R ; objectif 2 = +3 R ou plus haut 52 s ; horizon : `days` (peut évoluer en swing si la position progresse). **Cotation requise** : horodatage de marché < 3 min (temps réel) ; sur donnée différée, le D1 est un candidat « à vérifier », sans fiche d'ordre.
- Gap sans catalyseur (ou catalyseur `neutre`) → WATCH seulement. Gap ≥ 15 % → risque `élevé`, taille × 0,5 (paramètre).
- Expiration : chaque proposition D1 expire à `min(heure du scan + 30 min, 11:00)` ; passé ce délai, elle devient WATCH « pullback vers OR15_high / VWAP ».
- Latence : l'alerte est envoyée dès le calcul (< 60 s) avec les niveaux ; l'argumentaire LLM est ajouté **ensuite** (mise à jour du message / de la page), jamais attendu.

### D2 — Cassure momentum (breakout de leader)
- Univers : watchlist momentum (docs/05 §5.4) + tout l'univers en EOD.
- Conditions EOD (17:50) ou intraday (09:30, 10:30 : candidat sur donnée différée, fiche d'ordre sur cotation temps réel seulement) : clôture (ou cours) > plus haut des 60 séances (pivot) **ou** > plus haut 52 semaines ; RVOL_jour ≥ 1,5 ; rang RS ≥ 80 ; MM50 > MM200 et cours > MM50 ; extension limitée : cours ≤ MM20 + 1,5 × ATR14 (sinon WATCH « trop étendu ») ; consolidation préalable : amplitude (max−min) des 20 séances précédentes ≤ 15 % (paramètre) ; pas de résultats < 48 h.
- Niveaux : entrée = pivot + 0,3 % (ordre à plage de déclenchement pour J+1 : seuil = pivot + 0,3 %, limite = prix max), prix max = pivot + 1,0 % ; stop **structurel** = plus bas des 10 séances − 0,25 × ATR14 (repli `entrée − 1,5 × ATR14` si structure indisponible) ; rejet si distance > 2,5 × ADR20 ; objectifs 2 R / 3 R ; horizon `swing_2_6w`. Catalyseur non requis (bonus C s'il existe).

### D3 — Expansion de range (« momentum burst »)
- Conditions EOD : `close/close_prev ≥ 1,04` ; volume > volume veille et RVOL ≥ 1,3 ; bougie fermant dans le tiers haut de sa plage ; les 3–20 séances précédentes en consolidation (variation quotidienne moyenne < 2 %, pas de +4 % dans les 5 jours) ; TI65 : moyenne(close, 7) ≥ 1,05 × moyenne(close, 65) ; rang RS ≥ 70.
- Niveaux : entrée J+1 = au-dessus du plus haut de la bougie de cassure (ordre à plage de déclenchement : seuil = plus haut + 0,1 %, limite = prix max = plus haut + 0,5 %) ou sur pullback à mi-bougie ; stop **structurel** = plus bas de la bougie de cassure − 0,25 × ATR14 ; rejet si distance > 2,5 × ADR20 ; sortie temps : J+5 si l'objectif 1 (2 R) n'est pas atteint ; horizon `days`. Compte : comparaison PEA / CTO comptant / SRD (docs/08 §8.2), sans préférence par défaut.

### D4 — Pullback sur leader momentum (point d'entrée sur valeur sous surveillance)
- Objectif explicite de l'utilisateur : entrer sur les valeurs momentum « sous surveillance » (listes Zonebourse, Momentum, watchlist propre) sans courir après le gap.
- Conditions : valeur dans la watchlist momentum (rang RS ≥ 80, tendance haussière) ; repli de 3 à 10 % depuis le plus haut 20 j ; cours revenu au contact de la MM10 ou MM20 (± 1 × ATR) **ou** du niveau de l'ancienne cassure ; volume en baisse pendant le repli (volume moyen 3 j < 0,8 × ADV20) ; **signal de reprise** : cours repasse au-dessus du plus haut de la veille (intraday) ou bougie de retournement en clôture ; RSI(14) entre 40 et 60 pendant le repli (paramètre indicatif) ; pas de résultats < 48 h.
- Niveaux : entrée = plus haut de la veille + 0,2 % (prix max = + 0,8 %) ; stop **structurel** = plus bas du repli − 0,25 × ATR14 ; rejet si distance > 1,5 × ADR20 ; objectif 1 = plus haut 20 j (R/R ≥ 2 exigé, sinon WATCH) ; objectif 2 = +3 R ; horizon `swing_2_6w`. **Aucun catalyseur requis.** Compte : comparaison des trois options (docs/08 §8.2).
- Alimentation de la watchlist momentum « sous surveillance » : (1) calcul propre (rang RS, docs/05 §5.4) ; (2) valeurs citées par les lettres (D5) ; (3) **listes Momentum Europe/USA de Zonebourse Premium** : lecture authentifiée **une fois par jour** (job `zonebourse_lists_daily` 08:30, usage personnel, conforme à docs/04 §4.7) ou, à défaut, import manuel (coller la liste dans `/watchlist`) ; chaque entrée garde sa source pour le post-mortem.

### D5 — Intégrateur de signaux externes (newsletters, analystes)
- Chaque valeur extraite d'une lettre (Zonebourse, Momentum Capital, Décision Bourse, ABC Premium, recos de brokers RSS) devient un `signal(detector=external, source=…)` avec : sens, niveaux cités s'ils existent, **heure de réception**, cours d'ouverture officiel (`p_open`), cours à l'heure de réception (`p_recv`, horodatage de marché), `gap_open = p_open / close_prev − 1` (le gap proprement dit) et `drift_since_open = p_recv / p_open − 1` (la dérive depuis l'ouverture, c'est-à-dire ce que la lettre « rate »).
- Règles de fusion : un signal externe **confirme** un signal propre (D1–D4) émis le même jour → +5 points en C (max 30) et confiance +1 ; un signal externe **seul** sur une valeur momentum → déclenche un D4 « à l'affût » (watch pullback avec niveaux calculés) ; un signal externe seul sans configuration technique → WATCH.
- Aucun BUY n'est émis **uniquement** sur un signal externe si `drift_since_open ≥ 2 %` (`detectors.d5_external.drift_since_open_max_for_buy`) : c'est précisément le problème à éviter. Le post-mortem (docs/12) mesure la valeur de chaque source.

### D6 — Catalyseurs pré-ouverture (07:00–08:55) et intra-séance
- Sources : RSS (ActusNews, GlobeNewswire, ABC Bourse, TradingSat, Boursorama communiqués), EODHD news (si All-in-One).
- Pipeline : déduplication → rattachement à l'instrument (ISIN/nom via table d'alias, LLM en secours) → **classification LLM (Haiku)** avec schéma JSON : `type ∈ {résultats, guidance, contrat, M&A, dividende, augmentation_capital, gouvernance, reco_broker, réglementaire, autre}`, `direction ∈ {positif, négatif, neutre}`, `magnitude ∈ {forte, moyenne, faible}`, `ampleur_annoncée (0–1)` (d'après le texte de l'émetteur : « supérieur aux attentes », relèvement de guidance…), `résumé ≤ 240 caractères`, `confiance (0–1)`. Une **surprise** chiffrée (écart au consensus) n'est calculée **que** si un consensus daté est disponible (EODHD estimates, plan All-in-One) ; sans consensus, le champ est `null` et seule l'ampleur annoncée est utilisée, avec moins de points. Prompt versionné ; sortie validée ; température 0 ; cache par hash ; 50 items max par cycle.
- Sortie : `premarket_watch` (valeurs à surveiller à 09:00 avec direction attendue) ; alimente D1 (catalyseur requis) et le score C ; les catalyseurs `négatif/forte` sur une **position** déclenchent une alerte immédiate (S4, §6.4).
- Le scan tourne aussi en séance (toutes les 15 min) et de 17:40 à 19:00 (communiqués du soir), afin que le brief du lendemain couvre « les catalyseurs depuis 17:40 la veille ».

## 6.4 Détecteurs de sortie / allègement (sur positions ouvertes)

| Détecteur | Condition | Action proposée |
|---|---|---|
| S1 Seuil franchi | cours (temps réel) ≤ stop courant | **P1 « seuil franchi — vérifier l'exécution chez le courtier »** ; si aucune exécution confirmée (import ou déclaration) sous 5 min en séance → proposition **SELL au marché** ; « seuil franchi » et « stop exécuté » restent deux états distincts (docs/07 §7.4) |
| S2 Objectif 1 atteint | cours ≥ entrée + 2 R | REDUCE 50 % + stop remonté au prix d'entrée (paramètre) |
| S3 Trailing | clôture > entrée + 1 R : stop = max(stop, plus bas 5 j − 0,5 ATR) ; > 2 R : stop = max(stop, MM10 − 0,5 ATR) | Mise à jour du stop (rappel de modification chez le courtier) |
| S4 Thèse invalidée | Clôture < MM20 deux jours consécutifs (D2/D4) ; clôture sous la bougie de cassure (D3) ; catalyseur négatif fort ; rang RS < 50 | SELL/REDUCE selon score |
| S5 Sortie temps | D3 : J+5 sans objectif 1 (`detectors.d3_range_expansion.time_stop_sessions`) ; D1 : J+10 sans progression > 1 R (`detectors.exits.d1_time_stop_sessions`) ; toute position : J+40 sans objectif (`detectors.exits.time_stop_sessions_default`) | REDUCE/SELL |
| S6 Publication imminente | Résultats dans ≤ 48 h et gain < 1 R | REDUCE 50 % ou SELL (paramètre `risk.earnings_policy_on_position`) |
| S7 Liquidation SRD | Position SRD à J−3 de la liquidation (`detectors.exits.srd_liquidation_alert_days_before`) | Décision demandée : solder, proroger (coût affiché), ou transformer en comptant (si cash) |
| S8 Régime rouge | Passage en rouge | Rappel de resserrer les stops (stop = max(stop, MM20)) |
| S9 Meilleure opportunité | Nouveau BUY avec ratio d'admission ≥ `scoring.exceptional_min_ratio` alors que le nombre max de positions est atteint | Proposer l'allègement de la position au ratio courant le plus faible |

## 6.5 Grille de scoring (identique au projet actuel, rendue calculable)

| Bloc | Points | Sous-critères calculables (points) | Part LLM |
|---|---|---|---|
| **C — Catalyseurs & news** | 0–30 | Catalyseur D6 : forte +15 / moyenne +9 / faible +4 ; direction positive requise (négatif = 0) ; surprise vs consensus > 0,6 : +5 (sans consensus : ampleur annoncée > 0,6 : +3) ; reco broker positive < 24 h : +4 ; confirmation externe (D5) : +5 ; catalyseur > 3 jours : −50 %. **Requis (≥ `scoring.d1_min_c`) pour D1 seulement** ; bonus plafonné (`scoring.c_bonus_cap`) pour D2/D3/D4, jamais requis | Classification du catalyseur (type, direction, ampleur) |
| **T — Technique / momentum** | 0–25 | Rang RS ≥ 90 : +8, ≥ 80 : +6, ≥ 70 : +3 ; structure (au-dessus MM50 & MM200, MM50 > MM200) : +5 ; cours > MM20 et MM20 croissante : +2 ; RVOL ≥ 2 : +5, ≥ 1,5 : +3 ; qualité du déclencheur (cassure nette de pivot / OR15 / bougie de retournement) : +5 ; extension excessive (> MM20 + 1,5 ATR) : −4 ; proximité d'une résistance majeure < 3 % : −3 (borné à [0, 25]) | Aucune |
| **F — Fondamentaux** | 0–20 | Croissance CA (dernier exercice/trimestre) > 10 % : +5 ; marge opérationnelle > 10 % : +4 ; révisions de BPA 3 mois positives : +5 ; dette nette/EBITDA < 2 : +3 ; valorisation non extrême (PER < 2 × médiane sectorielle) : +3. Sources : EODHD fundamentals (plan All-in-One, P2) ; en P0, `yfinance.info` en secours (statut dégradé) et champs fondamentaux du screener TradingView. **Si aucune donnée : F n'est pas attribué** ; le score est présenté sur le total atteignable (« 68/80 — fondamentaux non disponibles »), la complétude est affichée, confiance −1 | Aucune |
| **R — Risque / volatilité / agenda** | 0–15 | Distance au stop ≤ 1,5 ADR : +5, ≤ 2,5 ADR : +3, sinon 0 ; pas de résultats sous 10 jours : +4 (date inconnue : +0 et mention) ; liquidité ADV ≥ 5 M€ : +3, ≥ 1 M€ : +2 ; capitalisation ≥ 1 Md€ : +3 ; gap ≥ 15 % : −4 ; devise ≠ EUR : −2 (borné à [0, 15]) | Aucune |
| **M — Contexte secteur / macro** | 0–10 | Régime vert +5 / orange +2 / rouge 0 ; secteur : RS sectoriel ≥ 60e pct +3 ; pas d'événement macro majeur le jour (Fed/BCE/CPI/NFP) +2 ; corrélation au portefeuille (même secteur déjà ≥ 2 positions) −3 (borné à [0, 10]). Secteur : champ `sector`/`industry` du screener TradingView (P0), confirmé par EODHD en P2 | Résumé macro du jour (texte, hors score) |

**Deux scores, un seul calcul.** Le **score global /100** (grille ci-dessus) est conservé pour information et continuité avec le projet actuel. Le **score d'admission** est propre à chaque détecteur : il ne met au dénominateur que les blocs **applicables** à la stratégie.

| Détecteur | Blocs au dénominateur | Bloc C | Ratio d'admission |
|---|---|---|---|
| D1 gap + catalyseur | C + T + R + M (+ F si disponible) | Requis : C ≥ `scoring.d1_min_c` | (C+T+R+M+F) / (30+25+15+10+F_max) |
| D2 cassure, D3 expansion | T + R + M (+ F si disponible) | **Bonus** : min(C, `scoring.c_bonus_cap` = 5) ajouté au numérateur seulement | (T+R+M+F+bonus) / (25+15+10+F_max), plafonné à 1 |
| D4 pullback | T + R + M (+ F si disponible) | Bonus (même règle) ; jamais requis | idem |

**Fondamentaux partiels** : F est calculé sur les sous-critères disponibles ; `F_max` = somme des maxima des sous-critères disponibles si ≥ 2 sur 5 le sont, sinon F est exclu du dénominateur ; la complétude (« F : 3/5 critères ») est toujours affichée et la confiance est réduite de 1 sous 3/5.

Interprétation : **BUY** si ratio d'admission ≥ `scoring.buy_min_ratio` (0,75 ; 0,80 en régime orange), risque faible/moyen, portes du détecteur franchies (docs/07 §7.5), entrée cohérente (stop structurel ≤ distance max, R/R ≥ 2), régime ≠ rouge ; **WATCH** 0,60–0,74 ; **HOLD** position existante sans renfort ; **SELL/REDUCE** via S1–S9 ; **NO TRADE** sinon. Test de référence : un D4 sans aucune actualité, avec T = 23, R = 15, M = 10 et F indisponible, obtient 48/50 = 0,96 → BUY ; le même avec T = 15, R = 10, M = 5 obtient 0,60 → WATCH. Les trois lectures (attractivité / qualité de l'entrée / admissibilité, docs/07 §7.6) sont dérivées de la même grille et affichées avec les deux scores. **Confiance /5** = f(data_status, complétude des blocs, concordance détecteur + externe + catalyseur) ; **risque** = f(distance stop, ADV, capitalisation, gap, devise, small cap).

Chaque score est stocké avec ses sous-scores et ses `explanations[]` (« RS rang 87 → +6 », « résultats dans 6 jours → 0 ») pour que le rapport et le post-mortem soient traçables.

## 6.6 Rôle du LLM (Claude API) dans ce module

- Classification des catalyseurs (D6) et extraction structurée des newsletters (D5) — **Haiku**, JSON strict.
- Rédaction de l'argumentaire synthétique et des « points de vigilance » de chaque proposition, à partir des `explanations[]` et des données — **Sonnet**, sans chiffre non fourni (test : tout nombre présent dans le texte doit exister dans les données d'entrée, à ±0,5 %).
- Jamais : calcul d'indicateur, de niveau, de score, de taille.

## Annexe — Définitions d'indicateurs (toutes testées dans `tests/unit/test_indicators.py`)

- `ATR14` : moyenne de Wilder du True Range sur 14 séances. `ADR20` : moyenne sur 20 séances de (high/low − 1), en %.
- `RVOL_jour` : volume du jour / moyenne des volumes sur 20 séances (hors jour courant). `RVOL_5` / `RVOL_15` : volume cumulé des 5 / 15 premières minutes / moyenne de cette même fenêtre sur 20 séances, calculée à partir des barres 1 min stockées ou du backfill REST 5 min (3 barres = 15 min) fait à 08:45 pour la liste d'ouverture ; à défaut, approximation `volume cumulé / (ADV20 × fraction de séance écoulée)` marquée `approx`.
- Unités : `ATR14` est en devise (€) ; `ADR20` est en % ; une borne « 1,5 ADR sous l'entrée » se lit `entrée × (1 − 1,5 × ADR20)`.
- `RS` (force relative, Minervini) : `0,4·r12m + 0,2·r6m + 0,2·r3m + 0,2·r1m` (rendements) → rang percentile dans l'univers du jour (`rs_rank` 0–100). Variante « vs indice » stockée aussi : `r3m − r3m(STOXX 600)`.
- `MM10/20/50/200` : moyennes mobiles simples des clôtures. `TI65` : `SMA(close,7) / SMA(close,65)`.
- `OR5_high/low`, `OR15_high/low` : plus haut/bas des 5 et 15 premières minutes de la séance continue (09:00:00–09:04:59 / 09:14:59 Paris).
- `gap_open` : `open / close_prev − 1` (open = premier cours officiel Euronext si disponible, sinon premier trade RT, corrigé à l'EOD).
- `pivot_60` : plus haut des 60 séances précédentes (hors jour courant). `consolidation_20` : (max(high,20) − min(low,20)) / min(low,20).
- `distribution_day` : clôture indice ≤ −0,2 % et volume > veille. `breadth_mm50` : part des instruments de l'univers avec close > MM50.
- `R` : (entrée − stop) en € par action ; les objectifs et le P&L sont exprimés en multiples de R.
