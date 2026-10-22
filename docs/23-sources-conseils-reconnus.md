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
| S2 | Positions courtes nettes (AMF, BaFin, AFM, CNMV, FI…) : chargeur de fichiers par autorité, table datée, fait de thèse et vigilance « position courte élevée » | à étudier : format des fichiers à relever depuis le PC |
| S3 | Italie hors EODHD : liste et capitalisations par le screener TradingView, historique par Yahoo (`.MI`), qualifiés A0.7 | décision utilisateur |
| S4 | Colonnes d'analystes du screener TradingView (recommandation, objectif, nombre d'analystes) en recoupement de Yahoo | à valider : noms de colonnes à tester depuis le PC |

## 23.6 Critères d'acceptation

- **A23.1** Aucune adresse de source n'est inventée : chaque URL du `params.yaml` de l'utilisateur a été vue par `sources-check`.
- **A23.2** Un titre « EQS-DD : X SE : … Kauf » ou « Déclaration des dirigeants … acquisition » est classé `transaction_dirigeant` positif sans appel au LLM ; une vente est classée négative et faible.
- **A23.3** Un achat de dirigeant récent ajoute au plus `scoring.insider_buy_points` au bloc C, dans la limite du bonus C, avec son explication dans la fiche.
- **A23.4** Une source sans URL n'est jamais interrogée ; une source en échec n'arrête pas les autres.

## Références de la recherche du 23/10/2026

- AMF : BDIF et abonnements RSS par type (dont déclarations des dirigeants) ; open data des positions courtes nettes (mise à jour quotidienne).
- ESMA : liste des sites nationaux de publication des positions courtes (ESMA50-164-770) ; Finansinspektionen publie ses positions en Excel.
- Études sur les transactions de dirigeants en Europe : Nottingham (FTSE 100), LSE (petites valeurs britanniques), TU Wien (Allemagne), EFMA 2006 (Allemagne), étude sur 31 pays européens.
