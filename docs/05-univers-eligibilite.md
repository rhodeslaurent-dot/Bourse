# 05 — Univers d'investissement et éligibilité PEA / SRD

## 5.1 Places et segments couverts

| Place | Code EODHD | Indices de référence | Priorité | Remarques |
|---|---|---|---|---|
| Euronext Paris | PA | CAC 40, SBF 120, CAC Mid 60, CAC Small | **P0** | Seule place où le SRD existe ; univers historique du projet |
| Euronext Amsterdam | AS | AEX, AMX | P0 | Sociétés NL éligibles PEA (siège UE) |
| Euronext Bruxelles | BR | BEL 20, BEL Mid | P0 | |
| Xetra / Francfort | XETRA | DAX, MDAX, SDAX, TecDAX | **P0** | Deuxième profondeur d'univers ; enchère d'ouverture 08:50–09:00 |
| Euronext Milan | MI | FTSE MIB, Mid Cap | P1 | |
| Madrid | MC | IBEX 35, Medium | P1 | |
| Nasdaq Stockholm / Copenhague / Helsinki | ST / CO / HE | OMXS30, OMXC25, OMXH25 | P1 | Devises SEK/DKK → risque de change (docs/07) ; Norvège (Oslo, OL) éligible PEA (EEE) |
| Euronext Lisbonne, Dublin ; Vienne (VI) ; Varsovie (WAR) | LS / IR / VI / WAR | PSI, ISEQ, ATX, WIG20 | P2 | Liquidité plus faible → risque « small cap » relevé |
| Suisse (SIX), Royaume-Uni (LSE) | — | — | **Exclus du périmètre PEA** (hors UE/EEE) ; possibles en CTO uniquement, phase 6 | |
| États-Unis (NYSE, Nasdaq) | US | S&P 500, Nasdaq 100 | Phase 6 (CTO Saxo uniquement) | Gestion du change EUR/USD |

## 5.2 Règles d'éligibilité (à encoder, sources docs/16)

- **PEA** : société ayant son siège dans l'UE ou l'EEE (27 États membres + Islande, Liechtenstein, Norvège) et soumise à l'IS ; ni Suisse ni Royaume-Uni (titres UK inéligibles depuis le 01/01/2021). Plafond de versements 150 000 € (PEA-PME 225 000 €, cumul ≤ 225 000 €). **Pas de SRD, pas de levier, pas de vente à découvert dans le PEA.**
  - Détermination automatique : `country_of_domicile` (EODHD fundamentals / référentiel) → présomption ; **préfixe ISIN** en secours (FR, NL, BE, DE, IT, ES, SE, DK, FI, NO, PT, IE, AT, PL, LU…) ; **confirmation par le drapeau courtier** (badge « PEA » sur la fiche Boursorama, filtre `peaEligibility=1` de la liste Boursorama). Cas pièges : sociétés cotées à Paris mais domiciliées à Jersey/Guernesey/Bermudes/Suisse/UK, ADR. Statut stocké avec `source`, `checked_at`, `confidence` ; une valeur `unknown` n'est jamais proposée pour le PEA. Ce calcul ne couvre que l'éligibilité **réglementaire** ; l'accessibilité effective chez BoursoBank et la disponibilité des types d'ordres sont deux drapeaux distincts confirmés par l'utilisateur (docs/08 §8.4 bis).
- **PEA-PME** (option) : < 5 000 salariés et (CA ≤ 1,5 Md€ ou bilan ≤ 2 Md€), ou capitalisation < 2 Md€ (ou l'ayant été à la clôture d'un des 4 exercices précédents) ; éligibilité appréciée à la date d'acquisition ; listes Euronext/Easybourse des sociétés déclarées. Non prioritaire (v1 : information seulement).
- **SRD** (Euronext Paris, compte-titres uniquement) : SRD « complet » = capitalisation ≥ 1 Md€ et volume quotidien ≥ 1 M€ ; « SRD long seulement » = volume ≥ 100 k€ ; tous les ETF Paris en long-only. Liste officielle Euronext (PDF, versions anciennes en ligne ; entrées/sorties par avis Euronext, mise à jour observée en fin d'année) → **liste maintenue en base** (`srd_eligibility`) à partir de : filtre `market=SRD` de Boursorama, page abcbourse `marches/cotation_srdlo`, badge « SRD » des fiches, et **vérification finale dans l'interface Saxo** (l'outil affiche « SRD selon Boursorama/ABC — à confirmer chez Saxo » tant que l'utilisateur n'a pas coché la confirmation). Diff mensuel notifié.

## 5.3 Filtres de liquidité et de qualité (paramètres `universe.*`)

| Filtre | Valeur par défaut | Raison |
|---|---|---|
| Volume moyen quotidien en € (20 j) | ≥ 1 000 000 € pour le CTO/SRD ; ≥ 500 000 € pour le PEA | Exécution des stops sans slippage excessif |
| Capitalisation | ≥ 300 M€ (≥ 150 M€ toléré) ; le niveau de risque « élevé » (taille × 0,5, docs/07) s'applique sous `universe.small_cap_threshold_eur` (1 Md€) | Réduction du risque d'illiquidité |
| Prix | ≥ 1 € | Exclusion des penny stocks |
| Historique | ≥ 250 séances | Calcul RS/ATR/MM200 |
| Statut | Pas en suspension, pas d'OPA en cours (sauf détecteur dédié), pas de « SRD long seulement » pour des positions vendeuses (hors périmètre de toute façon) | |
| Exclusions manuelles | Liste `universe.blacklist` (valeurs jugées ininvestissables par l'utilisateur) | |

Univers attendu : ~600–900 valeurs (Paris ~200, Xetra ~150, Amsterdam/Bruxelles ~80, Milan ~80, Madrid ~50, Nordics ~150, autres ~50). Le scan EOD couvre tout l'univers ; le temps réel ne couvre que **positions + liste d'ouverture + watchlist momentum** (≤ 150 symboles).

## 5.4 Sous-univers dynamiques (recalculés chaque soir)

- **Leaders momentum** : rang RS (docs/06) ≥ 80e percentile de l'univers, au-dessus de MM50 et MM200, MM50 > MM200 → « watchlist momentum » (≤ 100 valeurs) sur laquelle les détecteurs de **point d'entrée** (D4 pullback, D3 range expansion) tournent en priorité et qui est suivie en temps réel.
- **Liste d'ouverture** (chaque matin à 08:45) : valeurs ayant un catalyseur classé pertinent depuis 17:40 la veille + valeurs des lettres du matin + valeurs momentum proches d'un pivot (≤ 3 % sous le plus haut 60 j) → ≤ 15 valeurs suivies en temps réel dès 08:55.
- **Univers SRD** : intersection univers × `srd_eligibility=complet` → seul sous-univers routable vers le CTO avec levier.
- **Univers PEA** : intersection univers × `pea_eligible=true (confidence ≥ 0.8)`.

## 5.5 Référentiel instrument

Champs : `isin`, `ticker_local`, `ticker_eodhd`, `ticker_saxo (Uic)`, `ticker_tv`, `name`, `exchange`, `mic`, `currency`, `country_of_domicile`, `sector`, `industry`, `index_memberships[]`, `market_cap_eur`, `adv_eur_20`, `pea_eligible`, `pea_confidence`, `pea_source`, `srd_status ∈ {complet, long_only, none, unknown}`, `srd_confirmed_by_user`, `earnings_next_date`, `earnings_source`, `active`, `updated_at`. Toute correspondance de tickers entre providers est stockée et testée (les tickers Xetra/EODHD/Saxo diffèrent).

## 5.6 Versionnement

Chaque `universe_refresh` produit un instantané (`universe_snapshots`) ; les entrées/sorties sont listées dans le digest hebdo. Les signaux historisés référencent l'instantané utilisé, ce qui évite le biais de survivance dans les statistiques rétrospectives.
