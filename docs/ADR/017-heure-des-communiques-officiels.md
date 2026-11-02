# ADR-017 — Communiqués officiels de l'AMF : sources, table à part et heure de Paris (lot L1)

- **Statut** : **accepté le 01/11/2026** : plan du lot L1 validé par l'utilisateur (« Oui, code-le »), trou de 2022 à combler tout de suite par les paquets de la DILA (« Tout combler tout de suite »). **Révisé le 02/11/2026** : la lecture de l'heure de l'API était fausse ; le recoupement prévu au §« Conséquences » l'a montré (voir « Révision du 02/11/2026 »).
- **Contexte** : étape 2 de l'étude des causes (docs/36). Il faut l'historique daté des nouvelles officielles des sociétés cotées : résultats, chiffre d'affaires, acquisitions, OPA, rachats d'actions… La source publique est le flux AMF diffusé par la DILA, de deux façons :
  - l'**API d'info-financiere.gouv.fr** : jeu `flux-amf-new-prod`, 539 690 fiches au 01/11/2026, sans clé. Elle coupe les connexions depuis l'environnement cloud mais répond depuis le PC ;
  - les **paquets de la DILA** (`amf-gz_JJMMAAAA_HHMM.tar.gz`, 2019 → aujourd'hui) : un XML et un PDF par communiqué, environ 121 Go à cause des PDF.

## Constats (mesurés le 01/11/2026)

1. **Volume par année** dans l'API (date de dépôt) :

   | Année | Fiches |
   |---|---|
   | 2016 | 36 249 |
   | 2017 | 22 604 |
   | 2018 | 15 488 |
   | 2019 | 25 801 |
   | 2020 | 18 871 |
   | 2021 | 27 612 |
   | **2022** | **10 001** |
   | 2023 | 26 598 |
   | 2024 | 25 601 |
   | 2025 | 24 773 |
   | 2026 (au 01/11) | 20 924 |

   - Il y a aussi 86 567 fiches sans date de dépôt, presque toutes de 2008 à 2013 : 4 seulement après 2016.
   - ~~L'année 2022 est incomplète dans l'API.~~ **Faux** : ce décompte par année était erroné pour 2022. Le chargement complet en a lu 31 753 dans l'API, et les 2 186 paquets DILA de 2022 n'ont ajouté que 20 communiqués.
2. **Heure.** La source écrit l'**heure de Paris**, mais son étiquette de fuseau n'est pas fiable :
   - **API** : ~~l'heure de Paris est imprimée avec « +00:00 »~~ — **faux, voir la révision** : l'API donne un instant UTC, calculé avec l'étiquette, et l'étiquette se trouve dans un champ séparé, `timezone_uin_dat_amf` (« 02:00 »).
   - **Paquets DILA, contrôle de 9 paquets** : « +01:00 » en janvier, « +02:00 » en juillet, et encore « **+02:00** » les 28 et 29/10/2026, après le changement d'heure du 25/10. Pourtant les communiqués tombent toujours à 07:00, 07:30 et 18:00 : c'est l'heure de Paris, pas l'heure d'été.
3. **Sentinelles.** L'année 8888 sert de date de marché « absente » : 1 447 fiches de 2008 à 2013, et encore 3 dans un paquet de juillet 2025. Le code ISIN « 999999999999 » désigne un émetteur non coté.
4. **Codes de type.** Les codes sont de six chiffres (trois de type, trois de sous-type). Un code récent, 300005 (document d'enregistrement universel), a pour code secondaire 003000, le rapport financier annuel.

## Révision du 02/11/2026

- **Constat** : 31 763 communiqués de 2022 ont été lus par les deux sources, et leurs heures ne concordaient **jamais**.
- **Preuve**, sur trois communiqués dont la DILA donne l'heure de Paris :

  | Communiqué | Heure de Paris (DILA) | API | Étiquette de l'API |
  |---|---|---|---|
  | 15/01/2026 | 07:00 | « 06:00+00:00 » | 01:00 |
  | 15/07/2025 | 07:55:18 | « 05:55:18+00:00 » | 02:00 |
  | 28/10/2026 | 08:00:09 | « 06:00:09+00:00 » | 02:00 |

  L'API donne donc un **instant UTC**, calculé avec l'étiquette. Quand l'étiquette est fausse (« 02:00 » alors que Paris était revenu à +01:00 le 28/10/2026), l'instant est faux d'une heure.
- **Règle corrigée** (`app/domain/filings.paris_time_from_utc`) :
  - pour l'API, l'heure de Paris est reconstituée (instant + étiquette), puis convertie en UTC comme celle de la DILA ;
  - sans étiquette, l'instant est gardé tel quel ;
  - la DILA ne change pas (`paris_wall_clock`).
- **Application** :
  - une relecture par la même source recalcule les heures, le contrôle d'étiquette et la famille ;
  - l'heure donnée par l'autre source est désormais conservée (`other_market_timestamp`, migration 0031), avec l'heure de dépôt brute (`raw_amf_time`). L'accord des deux sources se recalcule donc à chaque relecture, et les écarts sont imprimés en minutes ;
  - le recoupement est refait sur la semaine du 25 au 31/10/2026 (étiquettes fausses) et sur mars 2022 (changement d'heure du 27/03).
- **Liste officielle des codes** (jeu `sous-type-dinformation` de l'API, 20 codes, lu le 02/11/2026) : elle prime.
  - 015000 = « Informations privilégiées » → famille « autre information privilégiée ». Ce n'est pas une « déclaration sur une rumeur » : cette famille, tirée de ma mémoire et non d'une source, est supprimée.
  - 300007 « Document de référence » → rapport financier ; 000010 « Dépôt effectué sur la banque des communiqués » → autre.
  - **020000** (18 721 communiqués, sans libellé) ne figure dans aucune liste : il reste « inconnu » (famille « autre »), compté. `filings-archive --refamily` reclassera sans réseau quand son sens sera établi.
- **Trous réels de l'API** : juillet à octobre 2018 (17 à 55 communiqués par mois) et mars à juin 2020 (197 à 922).
  - La DILA n'a pas juillet-septembre 2018 non plus (dossiers des diffuseurs sans ces mois ; paquets seulement à partir de novembre 2018) : ces mois restent « non couverts ».
  - Elle a 373 paquets pour mars-juin 2020 (1,4 Go) : ils sont lus pour combler.

## Décision

1. **Source principale : l'API**, mois par mois (un export JSONL). Si l'export est refusé : jour par jour, 100 fiches à la fois. **Complément : les paquets DILA**, pour les mois que l'API n'a pas (2022), lus un à un :
   - seuls les XML sont lus, en mémoire ; rien n'est extrait sur le disque ;
   - le paquet est effacé après lecture ;
   - un paquet lu est noté (`official_filing_packages`) et n'est jamais retéléchargé, sauf `--again`.
   - Aucun PDF n'est téléchargé.
2. **Table à part** `official_filings` (migration 0030), distincte de `news_items` : jamais classée par l'IA, jamais montrée comme une actualité.
   - Une ligne par (diffuseur, numéro).
   - La seconde source ne fait que marquer `also_in` et dire si elle donne la même heure (`also_in_time_ok`). Elle complète ce qui manquait, sans rien remplacer.
3. **Heure : l'heure de Paris est retrouvée, puis convertie en UTC par le code.** La DILA l'écrit telle quelle (`paris_wall_clock`). Pour l'API, elle est reconstituée à partir de l'instant UTC et de l'étiquette (`paris_time_from_utc`, révision du 02/11/2026). Pour une heure d'automne ambiguë, la première est retenue.
   - L'étiquette de fuseau ne déplace jamais l'heure. Elle est seulement comparée (`tz_label_ok`), et les écarts sont comptés à chaque chargement.
   - **Horodatage de marché** = `UIN_DAT_MAR`, la diffusion au marché. À défaut (année 8888, illisible), l'heure de dépôt à l'AMF, notée `time_source = amf`.
   - 00:00:00 = une date sans heure (`time_precision = jour`) : l'étude lira alors le moment « inconnu ».
   - `data_status = eod` (archive), avec `received_at` et `processed_at` (règle 2).
4. **Familles** (liste fermée, `FAMILIES`) déduites du code de type.
   - Un code inconnu est compté. Il prend la famille de son premier code secondaire connu, sinon celle de son type à 3 chiffres quand tous les codes connus de ce type ont la même famille (004001 → rapport financier ; jamais pour 001, qui mêle résultats, opérations et autres informations privilégiées). Sinon : « autre ».
   - Codes ajoutés après l'essai du 01/11/2026 (octobre 2026 : 1 970 communiqués) : 023000 « décision de franchissement de seuil » (famille `franchissement_seuil`), 300005 « document de référence » (rapport financier). Une ligne déjà stockée reprend sa famille à la relecture suivante.
   - Le titre n'est jamais lu pour deviner le type.
5. **Couverture** : chaque mois est comparé à la médiane du même mois des autres années (`data.filings.coverage_min_share`, 50 %).
   - Un mois en dessous est « non couvert » : l'étude y lit « inconnu », jamais « aucun communiqué ».
6. **Rien avant le 01/10/2015** : la période 2007-2015 reste réservée (docs/34, plan n° 7 non ouvert). Le chargement commence par défaut le 01/01/2016 (`data.filings.history_from`).
7. **ISIN** : gardé seulement s'il a la forme ISO 6166 et une clé de contrôle juste. Il n'est jamais deviné d'après le nom.

## Essai sur le PC (01/11/2026, octobre 2026)

- 1 970 communiqués, export mensuel accepté (deux requêtes).
- 483 étiquettes de fuseau fausses, soit la dernière semaine d'octobre : l'API écrit, elle aussi, encore « 02:00 » après le changement d'heure.
- 62 heures de marché absentes (8888) : heure de dépôt utilisée.
- 98 % d'ISIN valides ; les 40 rejetés sont des émetteurs non cotés (« 999999999999 »).

## Conséquences

- **Commandes sur le PC :**
  - `filings-archive --from 2016-01-01` : l'API, de quelques minutes à 3 h ;
  - `filings-archive --source dila --from 2022-01-01 --to 2022-12-31` : environ 2 200 paquets, 20 Go téléchargés un à un et effacés, une nuit ;
  - `filings-archive --coverage-only`.
- **Le recoupement API / DILA sur 2022 vérifie la règle d'heure sur données réelles** (« heure identique / différente »). Si l'API donnait en réalité l'heure UTC, les écarts d'une ou deux heures apparaîtraient là, et cette ADR serait revue. **C'est ce qui s'est produit** (révision du 02/11/2026).
- **Étape 2 de l'étude des causes** (à coder ensuite) : le communiqué officiel, par famille, devient une cause, avec son moment (connu avant, avant l'ouverture, pendant la séance) mesuré sur l'horodatage de marché.
- **Annulation** : la table est indépendante. La supprimer ne touche à rien d'autre (migration 0030, `downgrade`).
