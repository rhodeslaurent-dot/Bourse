# 39 — Méthodes d'investissement : ce qui marche vraiment, et ce que l'outil en fait

> **Demande de l'utilisateur du 02/11/2026** : « fais une recherche en autonomie et en multi agents pour trouver les meilleures méthodes d'investissement (action, obligation, matières premières, crypto, autres) puis intègre-le dans l'outil. Un seul objectif : maximiser mes gains en étant positionné au mieux. »
>
> **Méthode** : quatre recherches en parallèle (actions ; multi-actifs, obligations, matières premières ; crypto ; risque, taille et fiscalité française), un audit du dépôt, deux critiques contradictoires (solidité des preuves ; adaptation à l'outil), une synthèse. Les chiffres internes de l'outil cités ici ont été revérifiés dans le dépôt (docs/20, docs/27, docs/29, docs/33, docs/34). Les chiffres externes portent leur source ; ceux qui n'ont pas pu être relus à la source sont marqués « à vérifier ».

*Synthèse du 02/11/2026. Elle s'appuie sur quatre recherches (actions, multi-actifs, crypto, risque et fiscalité), un audit du dépôt et deux critiques contradictoires. Aucun montant ni aucune position de l'utilisateur n'est utilisé. Ce document ne contient aucun conseil personnalisé.*

---

## 1. La réponse honnête en 10 lignes

1. **Aucune méthode ne garantit un gain.** Toutes les primes connues ont déjà connu des années, voire des décennies, de retard.
2. **Un avantage publié perd en moyenne 58 % après sa publication**, et 26 % dès qu'on le teste hors de l'échantillon d'origine (97 prédicteurs, McLean & Pontiff, *Journal of Finance* 2016).
3. **Pour un particulier, le premier ennemi, ce sont les frais et l'impôt.** Le marché vient ensuite. Aux États-Unis (1991-1996), les ménages qui passaient le plus d'ordres gagnaient 11,4 % par an, contre 17,9 % pour le marché (Barber & Odean, 2000).
4. **L'outil a déjà mesuré ses signaux court terme.** Sur 195 cellules testées, il n'a retenu **aucun déclencheur** (docs/33). Les recommandations des lettres, achetées le lendemain, font −2,8 % contre le marché à 20 séances (n 99, t −3,0, docs/20 §20.11).
5. La façon **la mieux prouvée** de « maximiser ses gains » est donc simple : un **cœur indiciel mondial**, logé dans le PEA, acheté chaque mois, jamais vendu sur un signal, avec très peu de frais.
6. La seule « anomalie » qui tient encore après publication en Europe est le **momentum** (acheter ce qui a le plus monté sur 12 mois). Elle reste **non prouvée statistiquement** sur la période récente, et l'outil ne pourra pas la prouver seul (§3).
7. Obligations, or, matières premières, suivi de tendance : ils **réduisent le risque**, ils **n'augmentent pas** le gain espéré. Cela ne répond donc pas à votre objectif.
8. Crypto : **facultative**. Si vous en voulez, 0 à 3 % au maximum, en ETP bitcoin au CTO, **sans levier**. On ne connaît pas le gain espéré. En revanche, des baisses de −73 % (2022) ont déjà eu lieu.
9. L'outil doit **mesurer chaque mois** si la partie active (le satellite) fait mieux que le même argent placé dans l'ETF Monde, après frais et impôt. Tant que ce n'est pas le cas, le satellite reste petit.
10. Le plus gros gain **certain** est **fiscal** : à performance égale, le PEA laisse environ 34 % de gain net en plus qu'un CTO où l'on vend chaque année (§6).

---

## 2. Les méthodes, de la plus solide à la plus faible (preuve nette)

| Rang | Méthode | Classe | Confiance | Ce que l'outil en fait |
|---|---|---|---|---|
| 1 | Cœur ETF Monde au PEA, achat mensuel, jamais piloté | Actions monde | forte (principe), faible (dosage) | Poche R17 ; mesure « partie active contre le cœur » et rappel mensuel (lots M1, M2) |
| 2 | Routage par enveloppe (détention longue au PEA) | Fiscalité | forte | R13 existe déjà ; à corriger (§6) |
| 3 | Frais complets comparés au gain espéré | Tous | forte | Moteur de coûts existant ; mesure mensuelle |
| 4 | Plafond de levier SRD (2×, absolu 2,5×) | Actions FR | forte | Déjà dans params, ne pas relever |
| 5 | Crypto : pas de levier, ce n'est pas une valeur refuge | Crypto | forte | Contrôle « BLOQUÉ » ajouté (lot M2) |
| 6 | Momentum Europe par un ETF, acheté et conservé | Actions Europe | moyenne | Option du satellite (votre décision) |
| 7 | Budget de risque selon la preuve (seuils ADR-012) | Tous | moyenne | Déjà en place, ne pas affaiblir |
| 8 | Rééquilibrage par bandes (±5 points) | Allocation | moyenne | Rappel le premier jour de bourse du mois, si vous fixez la cible (lot M2) |
| 9 | Moins-values du CTO en fin d'année (information) | Fiscalité CTO | moyenne | Bloc de novembre au 28/12 |
| 10 | Kelly fractionnel (quart de Kelly) comme plafond | Taille | moyenne | Principe écrit, sans module pour l'instant |
| 11 | Poche crypto plafonnée (ETP bitcoin, 0 à 3 %) | Crypto | faible (gain) | Facultative, 0 % par défaut |
| 12 | Momentum 12-1 en gestion directe, avec tampon | Actions | faible (non prouvé ici) | À tester sur le banc (plan n° 9) |
| 13 | Dérive après publication des résultats | Actions | faible | On attend le verdict du plan n° 3 |

**Méthodes écartées, et pourquoi en une ligne**
- **Cassures et swing de 2 à 20 séances** : négatives après frais, dans la littérature (Bajgrowicz & Scaillet 2012) comme sur le banc de l'outil (d7 : +0,041 R brut, 0,178 R de frais, −0,137 R net). **Ce rejet est très solide.**
- **Plus haut sur 52 semaines** : +0,023 R net sur le banc, −0,028 R avec des frais stressés, biais de survie probable.
- **Achats des dirigeants** : excès de −0,036 R contre des contrôles appariés (X5, n 674, docs/34 §34.12). Le rapport actions écrivait « −0,086 » : c'est une erreur, corrigée ici.
- **Valeur, qualité, faible volatilité par ETF** : MSCI Europe Value fait seulement +0,3 point par an depuis 1998. Qualité : 8,03 % contre 8,81 % sur 10 ans. Minimum volatilité : 7,16 % contre 9,32 % (en USD). Ce sont des fiches MSCI 2026 lues via un résumé. Une fois les 0,31 % de frais payés, il ne reste rien.
- **Momentum résiduel, révisions des analystes** : pas de données datées dans l'outil, et des preuves venant surtout d'auteurs qui vendent ces stratégies.
- **Positions courtes, entrées en indice** : effet disparu ou nul (Greenwood & Sammon 2025 : de 7,4 % à moins de 1 %).
- **Faber (moyenne mobile 10 mois), double momentum, rotation entre classes, règle combinée à deux poches** : nets de frais et hors échantillon, l'avantage est marginal (Zakamulin 2014). ReSolve (2019) juge que le succès du double momentum vient « très probablement » de la chance. La règle combinée a une dizaine de paramètres et aucune preuve. Surtout, **ces règles vendent le cœur après une baisse**, ce que ce document interdit.
- **Parité de risque, ciblage de volatilité, plancher de drawdown** : sans levier, elles réduisent le gain. Elles ont échoué en 2022 (All Weather −22 %, RPAR −32 %). Elles feraient aussi doublon avec le niveau de risque du jour et le coupe-circuit.
- **Crypto : altcoins, tendance, halvings, ETH** : non testables, ou en voie de disparition, ou fondés sur 4 cycles seulement.

---

## 3. Ce que l'outil peut prouver, et ce qu'il ne pourra jamais prouver

- Le **banc de recherche** compte en **R par trade** (avec stop et objectifs), contre des **contrôles appariés**, y compris sur la tendance. Il ne sait donc pas mesurer une **prime de portefeuille** exprimée en % par an.
- **Le problème de puissance.** Pour démontrer un écart de 2 points par an quand l'écart de suivi est de 6 à 8 %, il faut **40 à 60 ans de données** (estimation des critiques, à recalculer avant tout pré-enregistrement). Même les 10 ans de MSCI Europe Momentum (+2,2 points par an) ne donnent qu'un t d'environ 1.
- **Conclusion pratique.** Sur les facteurs, on décide avec la **preuve externe et le coût**. L'outil mesure la **mise en œuvre** : frais réels, rotation, écart à l'ETF, et satellite contre cœur.

---

## 4. Que faire par classe d'actifs

### Actions (le moteur de gain)
- **Cœur** : un ETF Monde éligible au PEA, à réplication synthétique. Par exemple CW8 (LU1681043599, 0,38 %), WPEA (IE0002XZSHO1) ou DCAM (environ 0,20 à 0,25 %). **Frais à vérifier sur les DIC.**
  - Éligibilité au PEA : Bercy l'aurait confirmée le 09/06/2026 (CMS / Les Echos). Le ministre a renoncé le 26/08/2026 à les exclure par le PLF 2027 (jechange.fr).
  - **Le risque politique demeure.** `universe.etf_pea_isins` est encore vide.
- **Pourquoi un cœur mondial et pas européen** : sur 39 marchés développés, la probabilité de perte réelle sur 30 ans en actions d'un seul pays est d'environ 12-13 %, contre environ 1 % avec les seules données US (Anarkulova et al., JFE 2022). Le rapport actions proposait un cœur européen factoriel, le rapport risque-fiscalité un cœur Monde. **Cette divergence est à consigner dans docs/15-status.md** (règle 8). La synthèse retient le Monde.
- **Inclinaison facultative** : iShares Edge MSCI Europe Momentum (IE00BQN1K786, 0,25 %), **au CTO**. Il est vraisemblablement inéligible au PEA (poids du Royaume-Uni et de la Suisse). Il est capitalisant : l'impôt n'est dû qu'à la vente. Indice : 11,83 % contre 9,60 % pour MSCI Europe sur 10 ans (fiche MSCI au 30/06/2026, via résumé, avant frais). Ce n'est pas garanti. En 2022, l'ETF a fait −15,55 % (chiffre non sourcé).
- **Gestion directe** : seulement en satellite, et seulement si elle bat le cœur (§5). Le PEA BoursoBank Découverte coûte environ 1 % par aller-retour (docs/29). Avec une rotation de 150 % par an, cela fait environ 1,5 % par an, **soit l'essentiel de la prime**. Chez Saxo : 0,08 % par ordre, 2 € au minimum (comparateurs ; page officielle non lue).
- **TTF** : 0,4 % à l'achat des sociétés françaises de plus de 1 Md€, en PEA comme au CTO (loi n° 2025-127, art. 98).

### Obligations
- **Le PEA n'en accepte pas.** En 2022, les obligations d'État de la zone euro ont fait −18,42 % (fiche MSCI), et la corrélation actions-obligations est redevenue positive (Morningstar 2024).
- Elles ne servent pas l'objectif de gain. **Hors outil, et hors périmètre** (docs/00 et docs/22 n'en parlent pas).
- Pour la réserve de sécurité, utiliser plutôt le Livret A ou LDDS (1,7 % depuis le 01/08/2026) et le LEP (2,5 %), d'après info.gouv.fr. **Taux non revérifiés.**

### Or et matières premières
- **Or** : rendement réel de long terme proche de zéro (Erb & Harvey, FAJ 2013). Après 2025, sa meilleure année depuis 1979, le risque est d'extrapoler.
- Fiscalité des ETC or : PFU plutôt que la taxe de 11,5 %, selon des sources commerciales. **À vérifier au BOFiP.**
- **Matières premières** : la version qui marchait (long-court, avant 2007) est inaccessible. La version accessible, un ETF filtré par la tendance, a un effet net inconnu.
- L'outil ignore les ETC et ETN (`universe_refresh.py:59`). **Recommandation : rester hors outil.** Toute extension demanderait un ADR « proposé » et votre décision.

### Crypto
- **Facultative, 0 % par défaut.** Si oui : 0 à 3 % du portefeuille liquide, **bitcoin seul**, via un ETP physique au CTO Saxo.
  - Exemples : iShares IB1T (XS2940466316), ou CoinShares BITC (0,25 % depuis le 14/01/2025). Les frais réels d'IB1T sont à lire sur le DIC.
- **Poids** : selon BlackRock (2024, conflit d'intérêts), 1 % de bitcoin pèse environ 2 % du risque d'un 60/40, et 2 % en pèse environ 5 %.
- **Baisses** : −73 % en 2021-2022 (Glassnode), environ −50 % d'octobre 2025 à février 2026.
- **Pas une valeur refuge** : la corrélation avec le S&P 500 est passée de 0,01 à 0,36 en 2020-2021 (FMI 2022).
- **Aucun levier**, ni CFD, ni SRD, ni ETP à levier.
- **Prérequis technique** : la colonne `instruments.kind` faisait 8 caractères, alors que le code écrit « crypto_etp » (10). L'insertion aurait échoué. Corrigé par la migration 0032 (lot M0).

### Autres (foncières cotées, SCPI, private equity, produits structurés, options)
- **Non évalués dans cette recherche.** Aucune recommandation, ni pour ni contre. C'est à étudier dans une prochaine session si vous le souhaitez.

---

## 5. La structure : cœur et satellite

- **Réserve** (hors outil) : livrets réglementés ou fonds euros, de l'argent disponible qu'on ne place pas en bourse.
- **Cœur, majoritaire** : l'ETF Monde au PEA.
  - Achat chaque mois par la poche R17 (`pockets.dca`).
  - **Jamais vendu sur un signal ni après une baisse.** Vendre après une baisse est précisément ce qui crée l'écart de 1,2 point par an entre les investisseurs et leurs fonds (Morningstar, *Mind the Gap* 2025).
- **Satellite, minoritaire et borné** : les propositions de l'outil, avec toutes les règles de risque.
  - Sa part **n'augmente que si** sa performance nette (frais et impôt estimé compris) bat celle du même argent placé dans le cœur.
  - Le seuil de preuve est celui du banc (ADR-012 : n ≥ 300, t ≥ 3, DSR ≥ 0,95), et non un seuil plus faible.
- **Crypto facultative** : plafond fixe, rééquilibrée par bandes.
- **La part du cœur est votre choix.** Aucune étude ne fixe le bon ratio. Le rapport risque-fiscalité cite 70 % comme simple exemple, sans preuve.
- **Rééquilibrage** : contrôle mensuel, action seulement si l'écart dépasse 5 points. Utiliser d'abord l'argent nouveau. Ne jamais vendre le cœur. Selon Vanguard (2010, 2022), la fréquence change peu le résultat ajusté du risque.

---

## 6. Taille des positions, enveloppes et impôts 2026

### Taille
- **Rien ne change** : règle 7, niveau de risque du jour (au plus ×1 tant qu'aucune espérance positive n'est mesurée, docs/28), coupe-circuit, plafonds SRD.
- **Kelly** : à garder comme plafond seulement. Pour un investisseur qui parie le Kelly plein, la probabilité de voir un jour son capital divisé par deux est de 50 %. Elle tombe à 12,5 % au demi-Kelly et à 0,8 % au quart de Kelly (formule de Thorp ; théorie avec des paramètres connus).
  - Toutes les espérances mesurées par l'outil étant aujourd'hui ≤ 0, un module Kelly rendrait 0 partout. **On l'écrit comme principe, sans module.**
- **Levier** : ne pas le relever pour « gagner plus ».
  - Le CRD du SRD chez Saxo coûte 0,023 % par jour. Cela fait environ 5,8 % par an si l'on compte 252 séances, ou 8,4 % si l'on compte les jours calendaires (base à vérifier). La prorogation coûte 0,20 % (10 € au minimum).
  - Plafonner le levier a réduit d'environ 40 % les pertes des particuliers très endettés (Heimer & Simsek, JFE 2019).

### Enveloppes (règles 2026)
| Point | Règle | Source | Statut |
|---|---|---|---|
| PFU au CTO | 31,4 % (12,8 % d'IR + 18,6 % de prélèvements sociaux) | LFSS 2026 art. 12 ; service-public F2385 (vérifiée le 20/02/2026) | La CSG s'appliquerait déjà aux plus-values réalisées en 2025 (BOSS via Moneyvox), ce qui contredirait la date d'effet 2026-01-01 de params : **à vérifier** |
| PEA après 5 ans | Pas d'IR, 18,6 % de prélèvements sociaux sur le gain retiré | Meilleurtaux 02/2026, Moneyvox | **À vérifier** (Cerfrance dit l'inverse) |
| Plafond PEA | 150 000 € de versements ; 225 000 € avec le PEA-PME | CMF art. L221-30 | Sûr |
| Retrait du PEA avant 5 ans | Clôture du plan et PFU | — | Ne jamais le faire |
| Moins-values en PEA | Imputables seulement à la clôture d'un plan de plus de 5 ans entièrement liquidé | BOFiP BOI-RPPM-PVBMI-20-10-40 | Sûr |
| Moins-values au CTO | Reportables 10 ans sur des plus-values de même nature | — | Sûr |
| Option pour le barème | Révocable à partir des revenus 2026 | LF 2026 art. 126, selon Comet Expertise | **À vérifier** |
| Assurance-vie après 8 ans | Prélèvements sociaux de 17,2 %, abattement de 4 600 € ou 9 200 €, puis 7,5 % d'IR (primes ≤ 150 000 €) | Linxea, 06/08/2026 | PLF 2027 en discussion |
| Crypto en direct | PFU 31,4 % ; exonération si le total des cessions de l'année ne dépasse pas 305 € ; moins-values non reportables | impots.gouv.fr | Sûr (economie.gouv.fr indique encore 30 %) |
| ETP crypto au CTO | Régime des valeurs mobilières, report sur 10 ans | Presse spécialisée | **À vérifier au BOFiP** |
| TTF | 0,4 % à l'achat | Loi 2025-127 art. 98 | Sûr |

**Illustration arithmétique** (hypothèse de 8 % par an brut sur 10 ans ; ce n'est **pas** une prévision) : 1 € devient **1,943 €** en PEA, **1,706 €** au CTO si l'on vend chaque année, et **1,795 €** au CTO si l'on ne vend pas.

**Correction importante.** « Mettre les stratégies à forte rotation dans le PEA » est **faux avec la formule Découverte** : environ 1 % par aller-retour, contre environ 0,16 % chez Saxo hors TTF. La bonne règle :
- la **détention longue** (le cœur) va au PEA ;
- pour la rotation, on prend le compte qui donne le meilleur résultat net, calculé proposition par proposition (R13).

---

## 7. Ce qu'il ne faut PAS faire

- Acheter sur une cassure, un gap haussier ou une lettre « parce que ça monte » : c'est mesuré négatif.
- Vendre le cœur après une baisse, ou le piloter avec une moyenne mobile.
- Prendre du levier pour « maximiser » : ni SRD au-delà de 2×, ni CFD, ni levier sur la crypto, ni turbo sans espérance prouvée.
- Croire un backtest publié tel quel : retirer mentalement la moitié de l'avantage (−58 % après publication).
- Multiplier les ETF « smart beta » : ils promettent +2,77 % par an sur le papier et font −0,44 % par an une fois cotés (Huang, Song & Xiang, JFQA 2024).
- Extrapoler la crypto ou l'or après une grande année.
- Vendre puis racheter le même titre au CTO pour un but purement fiscal : abus de droit possible (LPF L64), **à vérifier**.
- Retirer de l'argent du PEA avant 5 ans.

---

## 8. Ce qui reste à vérifier (une session avec accès à Légifrance, au BOFiP, à service-public, impots.gouv et home.saxo)

- Le taux de 18,6 % sur le PEA.
- La date d'effet de la CSG à 10,6 % sur les plus-values 2025.
- L'option pour le barème.
- La fiscalité des ETP crypto et des ETC or.
- Les tarifs officiels Saxo et la base de calcul du CRD.
- L'ISIN et les frais de l'ETF Monde retenu.
- L'éligibilité au PEA d'un ETF momentum Europe.
- Le taux de dépôt de la BCE : il ne repose que sur une seule source, peu fiable. **Il ne doit être utilisé nulle part.**
- Les chiffres SPIVA Europe de fin 2025 (81,8 % des fonds actifs sous leur indice sur 1 an, 97,0 % sur 10 ans) : lus dans un résumé.
- L'historique crypto disponible : CoinGecko en version gratuite serait limité, à vérifier.

**Divergences à consigner dans docs/15-status.md (règle 8)**
- Cœur Europe ou cœur Monde.
- « Forte rotation → PEA ».
- Le chiffre X5 des achats de dirigeants.
- Le taux de prélèvements sociaux du PEA.
- La date d'effet de la CSG.
- `pockets.dca.acceleration_index` est noté « [non codé] » alors que le code calcule le drapeau.
- Le commentaire de `verdict` dit « ADR-016 proposé » alors que l'ADR est accepté.
- Morningstar (2 % de bitcoin ≈ 15 % de la volatilité) contre BlackRock (≈ 5 %).

---

## 9. Sources principales

**Littérature générale**
- McLean & Pontiff, JF 2016 : https://ivey.uwo.ca/media/3775549/pontiff.pdf
- Barber & Odean, JF 2000 : https://faculty.haas.berkeley.edu/odean/papers/returns/Returns.html
- Barber, Lee, Liu, Odean, RFS 2009 et JFM 2014 : https://ideas.repec.org/a/oup/rfinst/v22y2009i2p609-632.html
- Morningstar, *Mind the Gap* 2025 : https://www.morningstar.com/business/insights/research/mind-the-gap-2025
- SPIVA Europe fin 2025 : https://www.spglobal.com/spdji/en/documents/spiva/spiva-europe-year-end-2025.pdf
- Anarkulova, Cederburg & O'Doherty, JFE 2022 : https://econpapers.repec.org/RePEc:eee:jfinec:v:143:y:2022:i:1:p:409-433
- DeMiguel, Garlappi & Uppal, RFS 2009 : https://ideas.repec.org/a/oup/rfinst/v22y2009i5p1915-1953.html

**Momentum et facteurs**
- Rouwenhorst, JF 1998 : https://rpc.cfainstitute.org/research/cfa-digest/1998/08/international-momentum-strategies-digest-summary
- Fiche MSCI Europe Momentum : https://www.msci.com/documents/10199/4b089780-97b4-4303-a0cf-086ee31a5a85
- Daniel & Moskowitz 2016 : https://www.nber.org/system/files/working_papers/w20439/w20439.pdf
- Novy-Marx & Velikov, RFS 2016 : https://www.nber.org/papers/w20721
- Huang, Song & Xiang, JFQA 2024 : https://ideas.repec.org/a/cup/jfinqa/v59y2024i6p2515-2546_2.html
- Bajgrowicz & Scaillet, JFE 2012 : https://econpapers.repec.org/RePEc:eee:jfinec:v:106:y:2012:i:3:p:473-491

**Allocation multi-actifs**
- Zakamulin 2014 (résumé) : https://www.cxoadvisory.com/technical-trading/net-performance-of-sma-and-intrinsic-momentum-timing-strategies
- ReSolve 2019 : https://www.advisorperspectives.com/articles/2019/05/06/global-equity-momentum-a-craftsmans-perspective
- Erb & Harvey, FAJ 2013 : https://econpapers.repec.org/RePEc:taf:ufajxx:v:69:y:2013:i:4:p:10-42
- Fiche MSCI obligations d'État zone euro : https://www.msci.com/documents/1296102/a830277f-896a-918b-9e79-087473739e3f
- Vanguard 2010 (rééquilibrage) : https://indexacapital.com/bundles/unaiadvisor/docs/papers/2010-Vanguard-Best-practices-for-portfolio-rebalancing.pdf?v=241

**Taille des positions et levier**
- Thorp 2006 : https://www.Gwern.net/doc/statistics/decision/2006-thorp.pdf
- Heimer & Simsek, JFE 2019 : https://www.nber.org/papers/w24176
- Saxo, tarifs SRD : https://www.home.saxo/fr-fr/rates-and-conditions/srd/commissions

**Crypto**
- BlackRock 2024 : https://www.blackrock.com/institutions/en-us/insights/thought-leadership/portfolio-design/sizing-bitcoin-in-portfolios
- FMI 2022 : https://www.imf.org/en/blogs/articles/2022/01/11/crypto-prices-move-more-in-sync-with-stocks-posing-new-risks
- Glassnode 2022 : https://insights.glassnode.com/2022-bear-of-historic-proportions/
- impots.gouv (actifs numériques) : https://www.impots.gouv.fr/particulier/questions/comment-declarer-les-plus-ou-moins-values-sur-cessions-dactifs-numeriques

**Fiscalité et enveloppes**
- service-public F2385 : https://www.service-public.gouv.fr/particuliers/vosdroits/F2385
- Meilleurtaux 02/2026 : https://placement.meilleurtaux.com/bourse/actualites/2026-fevrier/taux-prelevements-sociaux-applique-pea-passe-desormais-18-6.html
- BOFiP BOI-RPPM-PVBMI-20-10-40 : https://bofip.impots.gouv.fr/bofip/2841-PGP.html/identifiant%3DBOI-RPPM-PVBMI-20-10-40-20230619
- Linxea 08/2026 : https://www.linxea.com/tout-savoir-sur/assurance-vie/fiscalite-assurance-vie-regles-abattements-et-strategies/
- CMS (ETF synthétiques au PEA) : https://cms.law/fr/fra/legal-updates/bercy-rassure-sur-l-eligibilite-des-etf-synthetiques-extra-europeens-au-pea
- jechange 08/2026 : https://www.jechange.fr/banques/news/pea-etf-synthetiques-gouvernement-renonce

**Mesures internes de l'outil** : docs/20 §20.11, docs/27 §27.8, docs/33, docs/34 §34.12-34.14, docs/36, ADR-012 à ADR-016.

**Limites** : plusieurs chiffres viennent de résumés secondaires, parce que l'accès web à de nombreux sites était bloqué pendant la session. Ils sont marqués « à vérifier » quand c'est le cas.
---

## 10. Ce qui est intégré dans l'outil (lots M0 à M2, 02/11/2026)

Rien de ce qui suit ne passe d'ordre (règle 1) ni ne change une proposition, une taille ou un verdict existant. Les nouveaux paramètres sont dans le groupe daté `allocation` de `config/params.example.yaml`.

- **M0 — corrections** :
  - migration 0032 : `instruments.kind` passe de 8 à 16 caractères (« crypto_etp ») ;
  - deux commentaires faux de `params.example.yaml` corrigés (`pockets.dca.acceleration_index`, `verdict`) ;
  - ADR-019 « proposé » : obligations, or, matières premières et suivi de tendance restent hors de l'outil ;
  - divergences consignées dans docs/15-status.md, sans les trancher (règle 8).
- **M1 — partie active contre le cœur** (ADR-018, proposé ; affichage seul) :
  - chaque achat et chaque vente de la partie active (hors poches R17 et R18) est rejoué dans le cœur, à sa clôture du jour ;
  - méthode : équivalent marché public (Kaplan & Schoar 2005) ; coût d'achat du cœur `allocation.core_cost_pct` ; impôt estimé au CTO (PFU en vigueur, pertes reportées) ; rien au PEA avant retrait ;
  - référence : l'ETF de `pockets.dca.isins` s'il a des cours, sinon la série `msci_world` (URTH en USD converti) annoncée comme « proxy déclaré, hors dividendes » ;
  - un cours manquant donne « indisponible », jamais une estimation ;
  - une ligne « Cap » dans la Revue du jour (section Portefeuille) et la commande `core-compare` (lecture seule) ;
  - la série proxy doit couvrir le premier ordre : `macro-backfill --from AAAA-MM-JJ` charge son historique (03/11/2026).
- **M2 — garde-fous et réglages en attente de vos décisions** :
  - crypto : jamais au SRD ni au PEA ; poche fermée tant que `allocation.crypto_max_weight` vaut 0 (« BLOQUÉ : poche crypto fermée ») ; ouverte au-dessus de 0, elle reste bloquée tant que l'outil ne contrôle pas le plafond de poids ;
  - `allocation.unvalidated_cards` : « achat » (par défaut, comportement actuel) ou « information » ; « information » range les fiches des sources non validées par la mesure (aucune à ce jour) dans une section repliée de la revue ;
  - rappel « achat mensuel du cœur » le premier jour de bourse du mois à Paris, seulement si `pockets.dca.isins` est renseigné ;
  - contrôle des bandes (±`allocation.rebalance_band_pts` points) seulement si vous fixez `allocation.core_target_pct` ; il oriente l'argent nouveau et ne propose jamais de vendre le cœur.

- **Règle « observation » (décision de l'utilisateur du 04/11/2026)** : une carte dont l'espérance **mesurée** est ≤ `admission.observation_max_edge_r` (0 R) ne produit **aucun ordre** :
  - mesure : l'historique comparable figé dans l'instantané, sinon le segment de production du backtest (la même que le verdict, docs/38) ; sans mesure, la règle ne s'applique pas ;
  - la carte reste visible (les lettres d7/d2 restent spéculatives, choix du 04/11) avec ses niveaux pour le journal, mais sans quantité, sans risque, sans ordre à plage et sans bouton « Ordre saisi » ;
  - la porte « WATCH : observation (espérance mesurée … R, source, n cas) » n'attend aucun fait : la réévaluation en séance ne la rouvre jamais ;
  - dans la revue : décision « OBSERVATION », « Rien à saisir » ; `null` désactive la règle.

- **C1 — le cœur ETF (décision D1 du 05/11/2026 ; l'ISIN choisi vit dans `config/params.yaml`, non versionné)** :
  - `core-set <ISIN>` (sur le PC, hors conteneur) écrit l'ISIN dans `pockets.dca.isins` de `params.yaml` après contrôle de sa clé, commentaire conservé ;
  - `core-setup` enregistre chaque ISIN de `pockets.dca.isins` depuis la liste des ETF de la place (Paris par défaut), **hors univers**, et charge ses clôtures ; la tâche EOD du jour les tient à jour ;
  - `core-compare` mesure contre les clôtures de l'ETF ; avant sa première clôture (un ETF plus jeune que le premier ordre), la série est **raccordée** aux rendements du proxy déclaré, et la ligne le dit ;
  - le cœur n'est **jamais** une carte de signal, ni compté dans les plafonds ou le risque ouvert, ni « sans protection » (aucun stop : il n'est pas vendu sur un signal ni après une baisse), ni dans le coupe-circuit (migration 0033 : part du cœur dans l'instantané d'équité consolidé) ; sa valeur reste dans l'équité du PEA ;
  - le rappel « achat mensuel du cœur » du premier jour de bourse du mois (lot M2) s'active avec l'ISIN.

**Pas encore fait** (à décider) :
- bloc fiscal de fin d'année au CTO (D9) ;
- plan n° 9 du banc : momentum 12-1 en gestion directe contre ETF (D7, données radiées à charger d'abord) ;
- vérification des chiffres fiscaux et tarifaires « à vérifier » (D8).

## 11. Vos décisions

1. **D1** — *tranchée le 05/11/2026 (ETF consigné dans `params.yaml`)* — Le cœur est-il un ETF Monde éligible au PEA, acheté chaque mois, jamais vendu sur un signal ni après une baisse ? Si oui, quel ISIN mettre dans `pockets.dca.isins` (après lecture du DIC) ?
2. **D2** — Quelle part du cœur fixez-vous (`allocation.core_target_pct`) ? Aucune étude ne donne le bon chiffre.
3. **D3** — Faut-il ranger les fiches des détecteurs et des lettres non validés dans une section « à titre d'information » (`allocation.unvalidated_cards: information`) ?
4. **D4** — Voulez-vous une inclinaison momentum par l'ETF iShares MSCI Europe Momentum au CTO, achetée et conservée, comptée dans la partie active ?
5. **D5** — Voulez-vous une poche crypto ? Non (0 %, par défaut), ou ETP bitcoin au CTO, plafond de 1, 2 ou 3 %, sans levier.
6. **D6** — Les obligations, l'or, les matières premières et le suivi de tendance restent-ils hors de l'outil (ADR-019) ?
7. **D7** — Autorisez-vous le plan n° 9 (momentum direct contre ETF), qui demande d'abord `backfill --inactive-only` sur le PC ?
8. **D8** — Autorisez-vous la lecture de Légifrance, du BOFiP, d'impots.gouv, de service-public et de home.saxo pour vérifier les chiffres « à vérifier » ?
9. **D9** — Voulez-vous le bloc « moins-values du CTO » de novembre au 28/12 (information seulement) ?
10. **D10** — Le Kelly reste-t-il un simple principe écrit (plafond au quart de Kelly), sans module, tant qu'aucune espérance positive n'est mesurée ?
11. **D11** — Faut-il étudier un changement de formule BoursoBank (Découverte ≈ 1 % par aller-retour, docs/29) ?
12. **D12** — Faut-il étudier plus tard les classes non évaluées (foncières cotées, SCPI, capital-investissement, produits structurés, options) ?
