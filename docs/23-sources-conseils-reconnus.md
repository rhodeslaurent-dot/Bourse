# 23 — Sources d'information et conseils reconnus par place

> Demande de l'utilisateur (23/10/2026) : « trouver les autres sources d'informations et de momentum utiles sur les différentes places, récupérer les conseils d'investissement utiles et surtout reconnus, puis les intégrer dans les données d'entrée du logiciel ».
> Cadre : docs/22 Q2 (pas de nouvel abonnement récurrent, sources gratuites d'abord), règle 2 (aucune donnée inventée : source et date sur chaque fait), règle 3 (le LLM classe et résume, il ne calcule pas).

## 23.1 Ce qui est « reconnu » et ce qui ne l'est pas

Une source entre dans le logiciel si elle est **vérifiable** (émetteur identifié, date) et si le signal qu'elle porte a une **valeur documentée**. Par ordre de solidité :

| Rang | Signal | Pourquoi il est reconnu | Limites connues |
|---|---|---|---|
| 1 | **Publications réglementaires** : transactions des dirigeants (MAR art. 19), franchissements de seuils, rachats d'actions, positions courtes nettes (règlement UE 236/2012) | Déclarations obligatoires, publiées par les autorités (AMF, BaFin, AFM, CNMV, FI…) ou par les diffuseurs agréés (EQS, Actusnews, GlobeNewswire) | Les études européennes sont **partagées** : achats de dirigeants plus informatifs que les ventes au Royaume-Uni et en Allemagne, effet surtout sur les petites capitalisations, plus faible une fois le marché corrigé (Nottingham, LSE, TU Wien). Le signal est donc un **complément du bloc C**, jamais un déclencheur seul |
| 2 | **Consensus et révisions des analystes** : objectif moyen, nombre d'analystes, recommandation, relèvements et abaissements | Agrégés par des fournisseurs reconnus (LSEG/Refinitiv, FactSet, S&P) et repris gratuitement par Yahoo et TradingView | Couverture faible sur les petites valeurs ; objectif souvent en retard sur le cours (cf. Randstad, Allfunds le 22/10) |
| 3 | **Lettres et presse financière reconnues** (Zonebourse/MarketScreener, Investir, Le Revenu, Der Aktionär, Börse Online, IEX, Affärsvärlden, Placera, Expansión…) | Rédactions identifiées, historique public | Payantes pour la plupart : seuls les titres et chapôs gratuits (RSS) sont lus ; une recommandation de presse est une **opinion**, classée comme telle (référentiel §1.2) |
| 4 | **Momentum** (force relative, cassures, volume) | Effet momentum documenté depuis Jegadeesh-Titman (1993) | Déjà calculé par l'outil (D2, D3, D4, D7, rang RS) ; aucune source externe nécessaire |

Exclus : forums, réseaux sociaux, « signaux » de sites non identifiés, toute source dont on ne peut pas dater le contenu.

## 23.2 Catalogue par place

Statut : **intégré** = déjà dans le logiciel ; **à brancher** = connecteur prêt, adresse à confirmer depuis le PC (`sources-discover`, `sources-check`, §23.4) ; **à étudier** = format à examiner avant de coder.

| Place | Source | Signal | Accès gratuit | Statut |
|---|---|---|---|---|
| Toutes | Yahoo Finance (yfinance) | consensus, nb d'analystes, recommandation | API non officielle | intégré (`fundamentals_refresh`, option B) |
| Toutes | TradingView screener | capitalisation, secteur, résultats ; **notes d'analystes** et note technique | endpoint non officiel | intégré (faits) ; colonnes d'analystes **à brancher** (noms de colonnes à valider, §23.5 S4) |
| France | Zonebourse / MarketScreener | lettres, changements de recommandation des brokers | e-mail (abonnement existant) | intégré (lettres) |
| France | ABC Bourse, TradingSat (BFM) | actualités, recommandations | RSS | intégré |
| France | Actusnews | communiqués réglementés (dont transactions de dirigeants, rachats) | RSS | intégré (flux) ; **classement réglementaire** ajouté (S1) |
| France | **AMF — BDIF** | déclarations des dirigeants, franchissements de seuils | RSS par type de document | **à brancher** |
| France | **AMF — positions courtes nettes** | positions ≥ 0,5 % | fichier open data (data.gouv.fr), mise à jour quotidienne | **à étudier** (S2) |
| France | Investir, Le Revenu, Boursier.com | conseils (titres) | RSS si disponible | **à brancher** (adresses à découvrir) |
| Allemagne | **EQS News (ex-DGAP)** | « EQS-DD » transactions de dirigeants, communiqués | RSS | **à brancher** |
| Allemagne | Der Aktionär, Börse Online, finanzen.net (analyses), dpa-AFX Analyser (repris par onvista) | conseils, changements de recommandation | RSS / pages | **à brancher** (adresses à découvrir) |
| Allemagne | BaFin | positions courtes ≥ 0,5 % | base publique | **à étudier** (S2) |
| Pays-Bas | IEX.nl ; **AFM** (registres MAR 19 et positions courtes, exports) | conseils ; dirigeants ; positions courtes | RSS ; exports | **à brancher** / **à étudier** |
| Belgique | De Tijd ; FSMA | conseils ; dirigeants | RSS ; pages | **à brancher** |
| Espagne | Expansión, Cinco Días ; **CNMV** | presse ; dirigeants et positions courtes | RSS ; fichiers | **à brancher** / **à étudier** |
| Suède | Affärsvärlden, Placera ; **Finansinspektionen** (Insynsregistret, positions courtes en Excel) | conseils ; dirigeants ; positions courtes | RSS ; exports | **à brancher** / **à étudier** |
| Danemark, Finlande | Børsen, Kauppalehti ; **Nasdaq Nordic / GlobeNewswire** (communiqués) | presse ; communiqués réglementés | RSS | **à brancher** |
| Norvège | E24, Finansavisen ; **Oslo Børs Newsweb** | presse ; communiqués | RSS | **à brancher** |
| Pologne | Bankier.pl, Parkiet ; KNF | presse ; positions courtes | RSS | **à brancher** |
| Portugal, Irlande, Autriche | Jornal de Negócios ; Irish Times Business ; Börsen-Kurier | presse | RSS | **à brancher** |
| Italie | Il Sole 24 Ore, Milano Finanza ; Consob | presse ; dirigeants, positions courtes | RSS ; fichiers | **à étudier** (place absente d'EODHD : S3) |

Les adresses exactes des flux **ne sont pas inventées** : elles sont découvertes et vérifiées depuis le PC de l'utilisateur (le serveur de développement n'atteint pas ces sites), puis reportées dans `config/params.yaml`.

## 23.3 Intégration dans les données d'entrée

1. **Flux RSS** (`data.rss_feeds`) : chaque entrée porte `name`, `url`, `market` (FR, DE, NL…), `kind` (`actualites`, `reco_analystes`, `reglementaire`, `presse_conseil`), `homepage` (pour la découverte) et `reconnu` (pourquoi la source est retenue). Une entrée sans URL (`à renseigner`) est ignorée par `news_scan`.
2. **Classement réglementaire par règles** (sans LLM, confiance 0,9) : un titre formulé selon les modèles officiels est classé avant tout appel au LLM :
   - `transaction_dirigeant` : « Déclaration des dirigeants », « EQS-DD », « Directors' Dealings », « managers' transactions », « Art. 19 MAR »… ; **achat** → positif, magnitude moyenne ; **vente** → négatif, magnitude faible (les ventes ont souvent une raison personnelle ; asymétrie §23.1) ; nature non lisible → neutre ;
   - `franchissement_seuil` : « franchissement de seuil », « Stimmrechtsmitteilung », « major shareholding » → neutre, magnitude faible (fait affiché, non noté) ;
   - `rachat_actions` : « rachat d'actions », « Aktienrückkauf », « share buyback » → positif, magnitude faible.
   Le LLM (prompt `news_classify` v3) connaît ces trois types pour les autres sources.
3. **Score, bloc C** : un **achat de dirigeant** sur la valeur dans les `scoring.insider_window_days` derniers jours (30) ajoute `scoring.insider_buy_points` (5), dans la limite du bonus C (`scoring.c_bonus_cap`) pour D2, D3, D4 et D7 ; une **vente** n'enlève rien au score mais apparaît dans les risques de la thèse.
4. **Thèse** : les transactions de dirigeants, franchissements et rachats récents figurent dans les faits numérotés du dossier (source, date, titre), comme les autres nouvelles.
5. **Contrôle** : `sources-check` affiche pour chaque flux le statut, le nombre d'articles et la date du plus récent ; `/sante` garde la fraîcheur par flux (alerte P4 après 2 jours sans résultat, déjà en place).

## 23.4 Outils (CLI)

- `sources-discover [--name N] [URL]` : lit la page d'accueil d'une source (ou de toutes celles dont l'URL est « à renseigner ») et liste les flux RSS/Atom qu'elle annonce (`<link rel="alternate">`, liens contenant rss / feed / .xml).
- `sources-check [--name N]` : lit chaque flux configuré et affiche statut, nombre d'articles, date du plus récent, premier titre. Rien n'est écrit en base.

## 23.5 Lots

| Lot | Contenu | État |
|---|---|---|
| S1 | Catalogue (§23.2) dans `params.example.yaml`, `sources-discover`, `sources-check`, classement réglementaire par règles, prompt v3, bonus « achat de dirigeant » au bloc C, faits de thèse | fait le 23/10/2026 |
| S2 | Positions courtes nettes (AMF, BaFin, AFM, CNMV, FI…) : chargeur de fichiers par autorité (CSV ou Excel, colonnes reconnues par leurs mots d'en-tête en 7 langues, fichier refusé si une colonne manque), adresse AMF lue dans le catalogue data.gouv.fr, table `short_positions` (migration 0016), job `short_positions_refresh` (19:40), `shorts-check` ; total déclaré par valeur = dernière position de chaque détenteur ≥ 0,5 % (un **plancher**) ; ≥ `data.short_positions.alert_pct` (2 %) → −`score_penalty` (3) au bloc R ; fait de thèse et ligne de fiche | fait le 23/10/2026 ; adresses des autres autorités à relever depuis le PC |
| S3 | Italie hors EODHD : **liste officielle** des actions d'Euronext Milan (fichier téléchargé sur live.euronext.com, chargé par `exchange-list-load` dans `data/exchange_lists/MI.yaml` ; ISIN, symbole, devise ; colonnes reconnues par leurs en-têtes) ; capitalisation et secteur par le screener TradingView (`MIL:`) ; **historique et clôture quotidienne par Yahoo** (`ENI.MI`, `universe.history_sources: {MI: yfinance}`, statut `eod`, source `yfinance` affichée) pour les valeurs de l'univers et les candidates | fait le 23/10/2026 ; liste à charger depuis le PC |
| S5 | Médias grand public (§23.7) : catalogue qualifié, découverte des podcasts par l'annuaire Apple, extraction des valeurs citées, vigilance « pression médiatique », ventes citées en risque, bilan mesuré par source sur `/lettres` | fait le 23/10/2026 ; flux à découvrir depuis le PC |
| S4 | Colonnes d'analystes du screener TradingView en recoupement de Yahoo : `tv-columns-check` teste chaque colonne candidate ; les colonnes acceptées vont dans `data.tradingview_screener.analyst_columns` ; valeurs stockées par jour et par source (`analyst_consensus`) ; TradingView complète un consensus Yahoo absent ; deux objectifs écartés de plus de 15 % → **aucun** objectif d'analystes retenu (affiché « sources divergentes ») | fait le 23/10/2026 ; colonnes à valider depuis le PC |

## 23.6 Critères d'acceptation

- **A23.1** Aucune adresse de source n'est inventée : chaque URL du `params.yaml` de l'utilisateur a été vue par `sources-check`.
- **A23.2** Un titre « EQS-DD : X SE : … Kauf » ou « Déclaration des dirigeants … acquisition » est classé `transaction_dirigeant` positif sans appel au LLM ; une vente est classée négative et faible.
- **A23.3** Un achat de dirigeant récent ajoute au plus `scoring.insider_buy_points` au bloc C, dans la limite du bonus C, avec son explication dans la fiche.
- **A23.4** Une source sans URL n'est jamais interrogée ; une source en échec n'arrête pas les autres.

## 23.7 Médias grand public : podcasts, radio, presse (« C'est votre argent » et équivalents)

Demande du 23/10/2026 : intégrer les conseils relayés par les médias (ex. « C'est votre argent », BFM Business), et leurs équivalents dans les autres pays, après une recherche fine qui qualifie les sources.

### 23.7.1 Ce que dit la recherche académique

| Étude | Support | Résultat |
|---|---|---|
| Barber & Loeffler (1993), *JFQA* | WSJ « Dartboard » | +4 % anormal sur 2 jours, **partiellement retourné en 25 séances** (pression acheteuse + un peu d'information) |
| Engelberg, Sasseville & Williams (2012), *Management Science* | *Mad Money* (CNBC) | +3,5 % le 1er jour (+6,2 % petites capitalisations), **effacé en ~12 jours** |
| Busse & Green (2002), *JFE* | CNBC en direct | Prix ajustés en quelques secondes : aucun profit pour un particulier |
| Ferreira & Smith (2003), *FAJ* | *Wall $treet Week* (gérants professionnels) | Contenu informatif réel à 1–2 ans : un **panel de professionnels** vaut mieux qu'un journaliste |
| Desai & Jain (1995), *JF* ; Dimitrov & Jain (mise à jour) | Barron's Roundtable (gérants) | Gain à court terme, pas d'alpha durable à 1–3 ans ; +4,1 % sur 30 séances sans retournement dans la mise à jour |
| Kerl & Walter (2007), *Review of Finance* ; (2009), *Kredit und Kapital* | magazines allemands (Börse Online, Focus Money, Der Aktionär…) | +2,6 % sur 5 jours ; **retournement sur les petites capitalisations**, effet durable sur les valeurs « value » ; à long terme **les ventes ont de la valeur, les achats peu** |
| Lidén (2006, 2007) | presse suédoise | Achats **trompeurs** (hausse effacée en 20 jours), **ventes justes** |
| Menéndez-Requejo (2005) | Cinco Días (Espagne) | Le marché réagit **avant** la publication |
| Kakhbod et al. (2023), « Finfluencers » | StockTwits | 56 % « anti-talent », plus suivis que les talentueux ; stratégie contrarienne +1,2 %/mois |

Aucune étude ne porte sur les médias français (BFM, Investir, Le Revenu) : l'outil **mesure lui-même** la performance de chaque source (§23.7.4). Cadre réglementaire : MAR art. 20 et règlement délégué 2016/958 (déclaration des conflits d'intérêts par quiconque diffuse une recommandation) ; recommandations AMF-ESMA aux finfluenceurs (janvier 2026).

### 23.7.2 Sources retenues (qualifiées)

| Source | Place | Nature | Usage dans l'outil |
|---|---|---|---|
| **C'est votre argent** (BFM Business, Marc Fiorentino ; gérants Rozier, Higgons, Schang…) | FR | podcast hebdomadaire ; achats et ventes de gérants nommés | **confirmation** pondérée par le bilan mesuré ; une **vente** citée sur une position détenue est signalée |
| **Relevé hebdomadaire « C'est Votre Argent »** (bourse-portefeuille-conseil.fr, site indépendant, proposé par l'utilisateur le 25/10/2026) | FR | un article par émission (« 16/10/2026 – C'est Votre Argent ») avec les mouvements des gérants ; flux RSS du site filtré sur ce titre (`title_includes`) ; **date de l'émission lue dans le titre** et clôture du vendredi comme référence (`broadcast_after_close`) ; le flux ne porte qu'un extrait : **l'article est lu sur le site** (`follow_link`) et **lu par règles** (`parser: gerants` : bloc par gérant, listes « Achat : » / « Vente : », arrêt à « Retrouvez ») — sans LLM, Claude en secours si la mise en page change ; rapprochement des noms normalisé (accents, « & » / « et », formes juridiques), refusé en cas d'ambiguïté | **confirmation** pondérée par le bilan mesuré, comme le podcast ; éditeur à qualifier (mentions légales) ; le flux de la page ne contient que des commentaires (écarté) |
| **Intégrale Bourse** (BFM Business) | FR | podcast quotidien ; invités gérants et analystes | **attention** (catalyseurs), confirmation faible |
| **Avis d'analystes** (TradingSat/BFM, déjà intégré) | FR | changements de recommandation des courtiers | **confirmation** ; une dégradation est un risque (déjà classée `reco_broker`) |
| **Investir — conseil du jour** (Les Echos) ; **Boursier.com** | FR | presse, titres gratuits | **vigilance** « pression médiatique » (§23.7.3) |
| **Alles auf Aktien** (WELT) | DE | podcast quotidien, valeurs nommées | **attention**, confirmation faible |
| **finanzen.net / dpa-AFX Analyser** | DE | notes de courtiers (≈ 80/jour) | **confirmation** |
| **Der Aktionär, Börse Online, €uro am Sonntag** | DE | presse et télévision, objectifs et stops | **prudence uniquement** (historique de « pushing », retournement documenté) |
| **IEX BeleggersPodcast** ; **De Beursvoyeurs** (De Tijd) | NL, BE | podcasts hebdomadaires | **attention** |
| **Estrategias de Inversión** (« Los brokers recomiendan ») | ES | agrégateur de courtiers | **confirmation** |
| **FinanzaOnline** (consensus FTSE MIB) | IT | tableau hebdomadaire Buy/Hold/Sell | **confirmation** |
| **Placera / Affärsvärlden** | SE | analyses köp/sälj (gratuites sur Placera) | **ventes** = risque ; achats = vigilance (Lidén) |
| **Inderes** | FI, SE | recherche avec objectif, **partiellement commanditée** | **information** seulement, libellée « recherche commanditée » |

**Écartées** : Börse vor acht (aucune recommandation) ; *Mad Money* / Jim Cramer (hors PEA, pas d'alpha, ETF « inverse Cramer » fermé à −15 %) ; finfluenceurs YouTube / TikTok / StockTwits ; Questor, *Shares*, Investors Chronicle (payants, hors PEA, historique faible) ; Börse Express « Kaufen oder verkaufen? » (publicité) ; Milano Finanza « Occhio ai Titoli » (rémunéré par l'émetteur) ; portefeuille DNB Carnegie (réservé aux clients) ; Tout pour investir (allocations, pas de valeurs nommées). Le classement des gérants de « C'est votre argent » publié par culture-financiere.com sert de **référence de contrôle**, pas de signal.

### 23.7.3 Intégration

1. **Collecte** : flux RSS des sites et **flux des podcasts** (`kind: media_conseil`, `usage: confirmation | attention | prudence | information`). L'adresse d'un podcast est lue dans l'annuaire officiel d'Apple (`apple_id` ou `itunes_term`, API iTunes Lookup/Search) par `sources-discover` ; jamais devinée.
2. **Extraction** (LLM, prompt `media_extract` v1, sortie validée) : valeurs **citées dans le texte** (nom présent mot pour mot, sinon rejet), sens (achat / vente / neutre), intervenant ; aucun chiffre. Stockage `media_mentions` (source, date de diffusion, valeur, sens, intervenant, extrait, **clôture de la séance précédant la diffusion**).
3. **Pression médiatique** (règle Python, paramètres `media.*`) : un **achat** cité depuis moins de `media.reversal_window_days` (28 jours ≈ 20 séances) et une hausse depuis la diffusion ≥ `media.jump_flag_pct` (3 %) → `WATCH : hausse médiatique … risque de retour` : pas d'achat pendant la fenêtre ; la valeur reste suivie.
4. **Vente citée** : fait de thèse (risque) et ligne de la revue de portefeuille pour une position détenue.
5. **Confirmation** : un achat cité par une source déclarée `usage: confirmation` **et** dont le **bilan mesuré** est favorable (§23.7.4) compte comme une confirmation externe au bloc C ; tant que le bilan est insuffisant, la citation est seulement affichée. Les sources `prudence` (Der Aktionär, Börse Online, Investir, Boursier.com, Placera, Affärsvärlden) ne confirment jamais.
6. **Faits de thèse** : chaque citation (source, date, sens, intervenant, extrait) figure dans le dossier transmis à Claude, en tant qu'**opinion**.

### 23.7.4 Bilan mesuré par source

Pour chaque source et chaque sens : nombre de citations, rendement moyen et médian à `media.track_horizon_sessions` (20) séances depuis la clôture précédant la diffusion, part de gagnantes ; « historique insuffisant » sous `media.track_min_samples` (20). Le bilan est affiché sur `/lettres` et décide de l'usage en confirmation (rendement moyen > 0 et part de gagnantes ≥ 50 %). Aucune optimisation automatique : un changement d'usage reste une décision de l'utilisateur (ADR).

## Références de la recherche du 23/10/2026

- AMF : BDIF et abonnements RSS par type (dont déclarations des dirigeants) ; open data des positions courtes nettes (mise à jour quotidienne).
- ESMA : liste des sites nationaux de publication des positions courtes (ESMA50-164-770) ; Finansinspektionen publie ses positions en Excel.
- Études sur les transactions de dirigeants en Europe : Nottingham (FTSE 100), LSE (petites valeurs britanniques), TU Wien (Allemagne), EFMA 2006 (Allemagne), étude sur 31 pays européens.
- Médias (§23.7) : Barber & Loeffler 1993 (https://ideas.repec.org/a/cup/jfinqa/v28y1993i02p273-284_00.html) ; Engelberg, Sasseville & Williams 2012 (https://ideas.repec.org/r/inm/ormnsc/v58y2012i2p351-364.html) ; Busse & Green 2002 (https://ideas.repec.org/a/eee/jfinec/v65y2002i3p415-437.html) ; Ferreira & Smith 2003 (https://rpc.cfainstitute.org/research/financial-analysts-journal/2003/wall-treet-week-information-or-entertainment) ; Desai & Jain 1995 (https://ideas.repec.org/a/bla/jfinan/v50y1995i4p1257-73.html) ; Kerl & Walter 2007 (https://academic.oup.com/rof/article-abstract/11/1/117/2886582) et 2009 (https://elibrary.duncker-humblot.com/article/6500/long-run-performance-evaluation-of-journalists-stock-recommendations) ; Lidén 2006 (https://ideas.repec.org/a/taf/eurjfi/v12y2006i8p731-748.html) et 2007 (https://ideas.repec.org/a/mfj/journl/v11y2007i3-4p253-285.html) ; Menéndez-Requejo 2005 (https://ideas.repec.org/a/taf/apfiec/v15y2005i7p509-518.html) ; Kakhbod et al., « Finfluencers » (https://www.sfi.ch/en/publications/n-23-30-finfluencers) ; ETF « inverse Cramer » fermé (https://www.etfstream.com/articles/inverse-jim-cramer-etf-closes) ; AMF-ESMA et les finfluenceurs (https://www.lafinancepourtous.com/2026/01/22/finfluenceurs-recommandations-de-lamf-et-de-lesma-pour-une-promotion-responsable/).
- Sources médias : C'est votre argent (https://podnews.net/podcast/i6q0p) ; classement des gérants (https://culture-financiere.com/classement-gerants-cest-votre-argent-12-octobre-2026/) ; Intégrale Bourse (https://mytuner-radio.com/podcast/integrale-bourse-328729492) ; Alles auf Aktien (https://www.mediaimpact.de/download/alles-auf-aktien) ; dpa-AFX Analyser (https://www.dpa-afx.de/en/analyse) ; IEX BeleggersPodcast (https://podimo.com/en/shows/iex-beleggerspodcast) ; De Beursvoyeurs (https://podnews.net/podcast/in4ow) ; Estrategias de Inversión (https://www.estrategiasdeinversion.com/busqueda/todas-las-recomendaciones-de-cellnex/22) ; FinanzaOnline (https://www.finanzaonline.com/rubriche/ftse-mib-tutte-le-raccomandazioni-buy-e-sell-degli-analisti-sui-40-titoli-19-01-2026) ; Placera (https://www.placera.se/nyheter/valkommen-till-nya-placera-2024-11-04) ; Inderes, avertissement (https://www.inderes.fi/en/disclaimer). Les identifiants Apple des podcasts sont **à vérifier** : `sources-discover` les contrôle dans l'annuaire Apple et retombe sur une recherche par mots sinon.
