# 36 — Causes des hausses, étape 1 : les causes calculables (30/10/2026)

> Demande de l'utilisateur, 30/10/2026 (résumé) : « Causes des hausses ». Sur un échantillon significatif de hausses de +5 % et plus, trouver ce qui a précédé ou accompagné chaque hausse, classer les causes et les hiérarchiser.
>
> - **Étape 1 (ce document)** : les causes calculables avec les données déjà présentes sur le PC, sans historique des nouvelles.
> - **Étape 2 (à venir)** : l'historique des nouvelles, pour les hausses restées sans cause.
>
> Deux exigences de la demande :
> - mesurer chaque cause **contre les baisses et les séances ordinaires** : une cause qui précède autant les baisses que les hausses ne vaut rien ;
> - ne mesurer que ce qui était **capturable une fois la cause connue** : l'écart d'ouverture d'une OPA ne s'achète pas.
>
> L'étude est **exploratoire** : elle ne change aucune règle. Elle alimente de futurs plans pré-enregistrés (discipline de docs/31). Aucun modèle de langage n'est utilisé.

## 36.1 Méthode : trois groupes, les mêmes règles

**Pourquoi trois groupes.** Partir des hausses mesure la fréquence d'une cause **parmi les hausses**. Cela ne dit pas si elle annonce une hausse : une cause banale (une publication de résultats, un volume élevé) se retrouve avant beaucoup de hausses, mais aussi avant autant de baisses et de séances ordinaires. L'étude tire donc trois groupes sur le même périmètre et la même période, et leur applique exactement les mêmes règles :

| Groupe | Définition | t0 |
|---|---|---|
| **Hausses** | épisodes de docs/34 : montée d'au moins max(5 % ; 2 σ·√durée), repérée par le zigzag des clôtures (seuil de retournement max(3 % ; 1,5 ATR)) | première séance de la hausse (la séance après le creux) |
| **Baisses** | le symétrique, **sur le même zigzag** : sommet → creux, baisse 1 − creux / sommet d'au moins max(5 % ; 2 σ·√durée), σ des 20 séances avant le sommet | la séance après le sommet |
| **Contrôles** | séances tirées au hasard, **stratifiées comme les hausses** : même répartition par groupe de places (P0 / P1 / P2), classe de capitalisation (< 1 Md€, 1–10 Md€, ≥ 10 Md€) et année | la séance tirée |

- Le zigzag n'est connu qu'après coup : il sert d'**étiquette**, jamais de critère (docs/34).
- Tirages avec une graine (`--seed`, défaut 20261030) : 1 500 hausses, 1 500 baisses et 1 500 contrôles par défaut, soit des proportions à environ ± 2,5 points.

**Fenêtre et moments.** Chaque cause est cherchée dans la fenêtre [t0 − 5 ; t0 + 1] et datée par rapport à t0 :
- **connue avant** : connue au plus tard à la clôture de t0 − 1 ;
- **avant l'ouverture de t0** : connue pendant la nuit qui précède t0 (après la clôture de t0 − 1, avant l'ouverture de t0) ;
- **pendant** : de l'ouverture de t0 à la clôture de t0 + 1.

La règle d'horaire est celle des lettres (`letter_known_day`) : une publication après la clôture de la place est connue à la séance suivante. Un test vérifie que « connue avant » coïncide exactement avec cette règle à Paris.

## 36.2 Les causes (liste fermée) et leur priorité

Une unité (hausse, baisse ou contrôle) porte **plusieurs causes à la fois**. La **cause principale** est la première présente dans l'ordre fixe ci-dessous :
1. d'abord l'information sur l'entreprise ;
2. puis les mouvements d'ensemble (le marché avant le secteur : un mouvement de secteur que le marché explique est celui du marché) ;
3. enfin les observables de cours et de volume, qui décrivent un mouvement plus qu'ils ne le causent.

| # | Cause | Définition | Moment | Source et couverture |
|---|---|---|---|---|
| 1 | opération sur titres ou détachement de dividende | changement du facteur d'ajustement (cours ajusté ÷ cours) de plus de 0,2 % à la séance de détachement | avant t0 : connue avant ; à t0 : avant l'ouverture ; t0 + 1 : pendant | cours EODHD ; inconnue si le cours ajusté manque. Au-delà de 20 % (division, opération) : unité **écartée** et comptée, comme docs/34 |
| 2 | publication de résultats | une publication de la table `earnings_events` (et les dates **confirmées** du calendrier déjà passées) | avant l'ouverture → nuit précédente ; en séance → pendant cette séance ; après la clôture **ou heure inconnue** → nuit suivante (lecture prudente) | couverte à moins de 100 jours d'une publication chargée (règle de docs/34) ; ailleurs « inconnue », jamais « absente » |
| 3 | achat de dirigeant (AMF) | déclaration retenue par la règle **X5 de l'enregistrement n° 5** (toute fonction, acquisition en bourse d'actions, ≥ 10 000 €, prix lu cohérent avec le cours) | heure de publication AMF | valeurs françaises cotées à Paris seulement, dans la période de l'archive et un mois où l'archive a des déclarations ; ailleurs « inconnue » |
| 4 | recommandation d'une lettre | « achat » ou « à surveiller », nom ou ISIN écrit dans la lettre (`letters_study._collect`) | heure de réception | de la première à la dernière lettre stockée |
| 5 | nouvelle stockée | une nouvelle de `news_items` citant la valeur | heure de publication | de la première à la dernière collecte de l'outil (couverture récente seulement) |
| 6 | mouvement de marché | la moitié liquide de la place, équipondérée (règle de l'étude des lettres), explique au moins 50 % du rendement de la valeur sur les 3 premières séances du mouvement, dans le même sens | pendant | sans objet pour un contrôle (aucun mouvement à expliquer) |
| 7 | mouvement de secteur | même règle avec les valeurs du même secteur (secteur d'aujourd'hui, tout le périmètre européen, au moins 5 valeurs) | pendant | sans objet pour un contrôle |
| 8 | saut d'ouverture | \|ouverture de t0 ÷ clôture de t0 − 1 − 1\| ≥ max(3 % ; 1,5 ATR14 ÷ clôture), dans un sens ou l'autre (le même prédicat pour les trois groupes ; le sens est rapporté à part) | pendant : **non capturable à l'ouverture**, achetable au mieux à l'ouverture suivante | cours |
| 9 | volume anormal avant le mouvement | RVOL (définition de production : volume ÷ moyenne des 20 précédents) ≥ 2 à l'une des clôtures t0 − 3 … t0 − 1 | connue avant | cours |
| 10 | configuration technique déjà présente | une configuration d'un détecteur de production (paramètres réels, zones, rang de force relative du jour) à l'une des clôtures t0 − 3 … t0 − 1, détecteur par détecteur ; d2 et d7 (surveillance, ADR-013), d3 et d4 (désactivés, ADR-014) sont rapportés à part avec leur politique ; d8 n'est pas rejouable (radar des forums) | connue avant | cours |
| — | aucune cause trouvée | aucune des causes ci-dessus | — | — |

Le rapport donne aussi la part des hausses **sans information ni mouvement de marché ou de secteur** (causes 1 à 7 absentes). C'est la cible de l'étape 2 : l'historique des nouvelles.

Les conventions chiffrées (fenêtre, 50 %, 3 séances, seuils du saut et du volume, 5 valeurs, 0,2 % et 20 %, 100 jours, délai de 10 séances, horizons 5 / 10 / 20, masque sous 20) sont celles de l'étude (`CauseParams`, `app/domain/move_causes.py`). Elles sont imprimées dans chaque rapport. Comme pour l'étude de docs/34, elles ne sont pas dans `config/params.yaml` : elles ne pilotent rien en production.

## 36.3 Deux vues

**A. Attribution** (on part des mouvements : cela classe des hypothèses).
- Pour chaque cause, la part des hausses, des baisses et des contrôles où elle est présente, avec l'intervalle de Wilson. La part est calculée sur les unités que la source couvre.
- Deux comparaisons :
  - le **lift** : part des hausses ÷ part des contrôles ;
  - le **rapport hausse / baisse** : proche de 1, la cause ne distingue pas une hausse d'une baisse.
- La part par moment (connue avant, avant l'ouverture, pendant).
- La **part du mouvement encore capturable une fois la cause connue** : (clôture du sommet − ouverture qui suit la connaissance de la cause) ÷ amplitude du mouvement. Elle vaut 0 si la cause n'est connue qu'après le sommet.
- La cause principale, les configurations par détecteur, et la part des sauts dans le sens du mouvement.
- Les découpages par groupe de places, capitalisation, année, volatilité (classes de docs/34), amplitude et forme du mouvement.

**B. Étude d'événements** (on ne regarde pas la suite pour choisir : c'est elle qui dit si une cause se joue).
- On prend **toutes** les occurrences de chaque cause dans le périmètre :
  - publications de résultats, achats de dirigeants, recommandations des lettres ;
  - sauts d'ouverture haussiers et baissiers, volumes anormaux ;
  - configurations des détecteurs, sur 120 valeurs tirées avec la graine, à toutes leurs séances (`--setup-values`) : rejouer les détecteurs sur tout le périmètre prendrait des heures.
- Une occurrence par valeur et par cause toutes les 10 séances : la première, sans regarder la suite.
- Au-delà de 20 000 occurrences par cause, tirage avec la graine.
- Achat à **l'ouverture qui suit la connaissance de la cause** ; pour un saut d'ouverture, l'ouverture suivante. Mesures à 5, 10 et 20 séances :
  - rendement net de l'aller-retour complet : CTO comptant, et PEA quand la valeur est éligible (`moves._costs`, taille au budget de risque avec un stop à 2 ATR) ;
  - excès sur la moitié liquide de la place, équipondérée ;
  - moyenne, intervalle à 95 %, t, et **t des moyennes hebdomadaires** : les événements d'une même semaine ne sont pas indépendants, le t ordinaire est optimiste ;
  - taux de la barrière de docs/34 (+max(5 % ; 2 σ·√10) avant −2 ATR en 10 séances, depuis l'ouverture), comparé au **taux de base des contrôles** (lift).
- **Point dans le temps.** Tout ce qui entre dans une occurrence est connu avant l'achat : ATR et seuil du saut à la clôture précédente, RVOL et configurations à la clôture, coûts et σ à la clôture précédente.
- **Non mesurés en partie B :**
  - le marché et le secteur : ce ne sont pas des événements d'une valeur, et leur équivalent achetable est l'indice ;
  - les nouvelles stockées : couverture trop courte (étape 2) ;
  - les opérations sur titres et les dividendes : mécaniques.

**Masques et tests multiples.**
- Toute statistique sur moins de 20 observations est masquée (règle de docs/34 §34.10).
- Le rapport compte les cellules lues et liste celles dont le t hebdomadaire atteint 3 en valeur absolue (Harvey, Liu & Zhu 2016). Ce sont des **pistes à pré-enregistrer**, jamais des découvertes.

## 36.4 Sceaux, registre et plans gelés

Les sceaux sont repris de docs/34 §34.1 :
- refus de toute date antérieure au 02/01/2017 ;
- aucun cours avant le 01/10/2015 ;
- New York, Londres et Zurich non lues ;
- groupes européens P0 / P1 / P2 seulement ;
- une ligne `etude_causes_exploratoire` au registre de la recherche (`data/research/ledger.csv`) à chaque exécution.

**Questions déjà posées par un plan gelé.** La partie B, pour les résultats et les achats de dirigeants, pose la question même des enregistrements n° 3 (réaction aux résultats) et n° 5 (achats des dirigeants), gelés avant toute lecture de ces données. Ces deux lignes de la partie B sont donc **masquées tant que le plan correspondant n'a pas été exécuté** (une ligne `run` de son empreinte au registre). Seuls les nombres d'occurrences sont imprimés.
- Raison : la lecture confirmatoire vient en premier (docs/31, docs/34 §34.5 et §34.12).
- Il suffit de relancer l'étude après `research run` des plans n° 3 et n° 5.
- La partie A (attribution) reste lisible, comme le profil « démarrées sur une publication » de `moves-study`.

## 36.5 Ce que l'étude ne couvre pas, et ses limites

**Non couvert** (écrit en tête du rapport) :
- l'historique de la presse : seuls les communiqués officiels AMF des valeurs françaises cotées à Paris sont lus (étape 2, §36.9), les nouvelles stockées ne couvrant que la collecte récente de l'outil ;
- les changements de recommandations et d'objectifs d'analystes : aucune source datée à l'époque (docs/34 §34.8) ;
- les déclarations de dirigeants hors de France ;
- les podcasts, vidéos et réseaux sociaux : aucun historique ;
- les entrées en indice, les rachats d'actions et les annonces d'OPA, sauf si une nouvelle stockée les cite.

**Limites :**
- la capitalisation et le secteur sont ceux d'aujourd'hui ;
- les valeurs radiées sont absentes (biais de survie) ;
- les cours ne sont pas ajustés : une fenêtre qui traverse une opération sur titres est écartée, de t0 − 22 (entrées de l'ATR, de σ et du RVOL) à la fin du mouvement, ou à la sortie la plus lointaine pour un événement ;
- l'heure d'une publication de résultats vient de la source (Yahoo) : inconnue ou contradictoire, elle est lue de la façon la plus prudente ;
- les contrôles sont stratifiés comme les hausses, pas comme chaque famille d'événements : la composition d'une famille (par exemple les achats de dirigeants, surtout de petites valeurs parisiennes depuis 2021) peut différer de celle du taux de base. L'excès sur la place corrige le marché, pas cette composition ;
- une cause « pendant » décrit souvent le mouvement lui-même : un rapport hausse / baisse élevé y est en partie mécanique. Seule la partie B dit ce qui se jouait.

## 36.6 Comment l'étude alimente les plans

1. Une cause qui précède plus les hausses que les baisses et les séances ordinaires (lift > 1 et rapport hausse / baisse > 1), **connue avant ou avant l'ouverture**, avec un rendement net positif en partie B au-delà du taux de base, et un t hebdomadaire ≥ 3, devient une **hypothèse**.
2. L'hypothèse s'écrit dans un plan pré-enregistré (docs/31) : déclencheur, entrée, sortie, cellules et seuils figés avant toute lecture. Le plan est exécuté sur des données qui n'ont pas servi à le choisir, puis confirmé sur la période scellée 2007-2015 ou vers l'avant.
3. Les hausses sans information ni marché définissent l'étape 2 : quelles nouvelles faut-il historiser, et sur quelles valeurs et quelles années.

Aucun résultat de cette étude ne change un paramètre, un détecteur ou une règle sans ADR.

## 36.7 Écarts à la demande, et pourquoi

- **Contrôles et marché / secteur.**
  - « Mouvement de marché » et « mouvement de secteur » sont des explications d'un mouvement : ils sont « sans objet » pour un contrôle, qui n'a pas de mouvement.
  - La comparaison utile est donc hausses contre baisses. Le rapport donne en plus le rendement moyen de la place sur les premières séances, pour les trois groupes.
- **Saut d'ouverture dans un sens ou l'autre.** Le même prédicat s'applique aux trois groupes, sinon le rapport hausse / baisse serait mécanique. Le sens est rapporté à part. En partie B, sauts haussiers et baissiers sont deux familles distinctes.
- **« Taille » des contrôles = classe de capitalisation.** Un contrôle n'a pas d'amplitude de hausse. L'amplitude (classes de docs/34) reste un découpage des mouvements.
- **Résultats à heure inconnue** placés après la clôture : jamais supposés connus plus tôt, ni achetés à l'ouverture du jour.
- **Partie B des résultats et des achats de dirigeants** masquée jusqu'à l'exécution des plans n° 3 et n° 5 (§36.4).
- **Configurations en partie B** sur un échantillon de valeurs (`--setup-values`, 120 par défaut, comme les 120 valeurs du contrôle de docs/34). En partie A, elles sont rejouées pour chaque unité.
- **Délai de 10 séances** entre deux occurrences d'une même cause sur une même valeur : sans lui, les volumes anormaux et les configurations consécutifs compteraient plusieurs fois la même situation.
- **Calendrier des résultats** : seules les dates **confirmées** et déjà passées complètent l'historique ; une date estimée n'est jamais un événement.

## 36.8 Sur le PC

**Commande** (de préférence le soir) :

```
docker compose exec -T app python -m app.cli moves-causes --from 2017-01-02
```

Options :
- `--to` (défaut : hier) ;
- `--rises 1500 --falls 1500 --controls 1500` : tailles des tirages ;
- `--setup-values 120` : valeurs dont les détecteurs sont rejoués à chaque séance ;
- `--events-max 20000` : occurrences mesurées par cause ;
- `--seed 20261030` ;
- `--out data/research`.

**Sortie :**
- `data/research/causes_<horodatage>.md` (rapport en français) et `.json` : agrégats seulement, ni cours, ni nom, ni ISIN ;
- une ligne `etude_causes_exploratoire` au registre.

**Durée** : de l'ordre de 20 à 40 minutes pour environ 2 000 valeurs.
- La lecture des cours prend quelques minutes.
- Le rejeu des détecteurs sur les 120 valeurs tirées prend 5 à 10 minutes.
- **Mémoire** : environ 1 Go.

**Avant de lancer :**
- les historiques chargés améliorent la couverture : `earnings-history --from 2016-01-01`, `insider-archive --from 2021-11-01`, `mentions-recheck --apply` pour les lettres ;
- la couverture de chaque source par année est imprimée en tête du rapport.

**À me transmettre** : le `.md`. Une fois les plans n° 3 et n° 5 exécutés, relancer la commande pour lire les deux lignes masquées de la partie B.

## 36.9 Étape 2 : historique daté des communiqués officiels (lot L1, ADR-017)

**Source** : le flux AMF des informations réglementées, diffusé par la DILA. Ce sont les communiqués officiels des sociétés cotées en France, avec leur type, leur titre, leur heure et leur ISIN.
- L'**API d'info-financiere.gouv.fr** est lue mois par mois (539 690 fiches au 01/11/2026).
- Les **paquets de la DILA** servent à recouper l'heure et à combler mars-juin 2020. Juillet-octobre 2018 manque aux deux sources : ces mois restent « non couverts ». 2022 n'était pas incomplet : le décompte par année de l'API était faux.
- Seules les métadonnées sont gardées, dans la table `official_filings`. Aucun PDF n'est téléchargé.

**Heure** : l'étiquette de fuseau de la source n'est pas fiable. Les paquets écrivent encore « +02:00 » après le changement d'heure du 25/10/2026.
- La DILA écrit l'heure de Paris.
- L'API donne un instant UTC calculé avec cette étiquette, donc faux d'une heure quand l'étiquette est fausse.
- Le code retrouve l'heure de Paris (pour l'API : instant + étiquette) et la convertit lui-même en UTC (ADR-017, révisé le 02/11/2026). L'heure retenue est celle de la diffusion au marché ; à défaut, celle du dépôt à l'AMF.
- Les deux sources sont recoupées : l'écart est imprimé en minutes.

**Familles** (liste fermée, d'après le code de type) : résultats, chiffre d'affaires, acquisition ou cession, autre information privilégiée, rapport financier, mise à disposition d'un document, droits de vote et capital, offre publique, rachat d'actions et contrat de liquidité, gouvernance, franchissement de seuil, autre (dont le code 020000, absent de la liste officielle). Un code inconnu prend la famille de son code secondaire, sinon celle de son type à 3 chiffres quand ce type n'a qu'une famille ; il est compté.

**Couverture** : un mois sous 50 % de la médiane du même mois des autres années est « non couvert ». L'étude y lira « inconnu », jamais « aucun communiqué ».

**Sceau** : rien avant le 01/10/2015 ; le chargement commence par défaut le 01/01/2016.

**Commandes sur le PC :**

```
docker compose exec -T app python -m app.cli filings-archive --from 2016-01-01
docker compose exec -T app python -m app.cli filings-archive --source dila --from 2022-01-01 --to 2022-12-31
docker compose exec -T app python -m app.cli filings-archive --coverage-only
```

- `--max-packages 3` : un essai sur quelques paquets ;
- `--again` : relit des paquets déjà lus ;
- `--refamily` : reclasse les familles d'après les codes stockés, sans réseau.
- La sortie est faite d'agrégats seulement : le tableau mois par mois, les sources et leur accord sur l'heure, les heures, les ISIN, les familles et les codes de type.

**Dans l'étude (étape 2, décision de l'utilisateur du 02/11/2026)** : `moves-causes` lit l'archive. Chaque famille retenue devient une cause de §36.2, mesurée contre les baisses et le groupe témoin.
- **Familles retenues** : résultats, chiffre d'affaires, acquisition ou cession, offre publique, franchissement de seuil, autre information privilégiée.
- **Routine écartée** : droits de vote, rachats d'actions, mises à disposition de documents, gouvernance, code 020000.
- **Valeurs** : françaises (ISIN FR) cotées à Paris. Ailleurs, la cause est « inconnue ».
- **Mois** : chaque mois de la fenêtre [t0 − 5, t0 + 1] doit être couvert par l'archive (§ ci-dessus). Sinon, « inconnu ».
- **Premier communiqué seulement** : les rectificatifs et compléments sont exclus.
- **Moment** : heure de diffusion au marché, placée sur les séances de la valeur comme les lettres (avant 9 h → avant l'ouverture ; pendant la séance ; après la clôture → avant l'ouverture suivante). Une date sans heure est placée après la clôture de son jour, jamais plus tôt.
- **Priorité** : les communiqués viennent après les autres informations. Une hausse que l'étape 1 expliquait garde sa cause principale ; seules les hausses restées « sans information » peuvent en recevoir une.
- **Partie B** : chaque famille est un événement acheté à l'ouverture suivante. Les résultats et le chiffre d'affaires restent **masqués** tant que l'enregistrement n° 3 (réaction aux résultats) n'a pas tourné, comme les publications de résultats.

**Première lecture (rapport `causes_20261102_152510`, 2017 → 2026, 1 500 hausses, 1 500 baisses, 1 500 contrôles)** :
- **Couverture** : les communiqués ne couvrent que les valeurs françaises cotées à Paris, soit environ 15 % des unités (≈ 230 hausses). Avant 2019, les résultats sont presque tous codés « autre information privilégiée » (35 communiqués « résultats » retenus en 2017, 114 en 2018, contre ≈ 1 500 par an ensuite) : la famille « résultats » est incomplète en 2017-2018.
- **Partie A** : aucune famille ne distingue nettement une hausse d'une baisse ou d'une séance ordinaire.
  - Résultats : 10,1 % des hausses, 7,8 % des baisses, 6,7 % des contrôles (lift 1,51, hausse/baisse 1,29), avec des intervalles qui se chevauchent.
  - Chiffre d'affaires : plus fréquent avant les baisses (hausse/baisse 0,37), sur un petit effectif.
  - Acquisition ou cession, franchissement de seuil, autre information privilégiée : aussi fréquents dans les trois groupes.
  - Comme cause principale, les communiqués expliquent 4,4 % des hausses et 4,8 % des contrôles. 73,5 % des hausses restent sans information ni mouvement de marché.
- **Partie B** (acheté à l'ouverture qui suit le communiqué) : aucun excès positif.
  - L'excès sur la place est négatif après les résultats (−0,44 % à −0,87 % de 5 à 20 séances, t hebdomadaire −1,3 à −2,3) et après le chiffre d'affaires (−0,37 % à −0,44 %, t > −2).
  - Il est **nettement négatif après une « autre information privilégiée »** : −0,25 %, −0,52 %, −0,65 % à 5, 10 et 20 séances, t hebdomadaire −4,5, −5,4, −5,0, sur 18 274 occurrences.
  - Barrière de docs/34 plus souvent atteinte (lift 1,37 à 1,44) après résultats, chiffre d'affaires et opérations : plus de grands mouvements, dans les deux sens.
- **Lecture** : un communiqué officiel n'est pas un déclencheur d'achat. La dérive négative après une information privilégiée générique peut n'être qu'un **effet de profil** (petites valeurs, augmentations de capital), comme l'excès du plan n° 7 (§36.x). Il faut d'abord la mesurer contre les contrôles appariés du banc, avant toute règle de prudence.

## 36.x Suite donnée aux pistes (01/11/2026)

- **Saut d'ouverture baissier, acheté à l'ouverture suivante** (excès +0,42 % à 5 séances, t hebdomadaire 4,6, sur la moitié liquide de la place) : pré-enregistré comme plan n° 7 (docs/34 §34.14), gelé le 01/11/2026. Mesuré par le banc sur la même découverte 2017-2026 (contrôles appariés : même jour, même groupe, même tercile de liquidité et de volatilité, même tendance), l'excès est **nul** (t +0,35, +0,42, −0,84 à 5, 10 et 20 séances ; placebos nuls). Le rebond tient au profil des valeurs qui sautent, pas au saut. Piste close ; la période scellée n'a pas été ouverte (décision de l'utilisateur).
- **Achats de dirigeants** (excès +0,51 % à 5 séances, net CTO ≈ 0) : plan n° 5, non concluant, estimations négatives contre les contrôles appariés (docs/34 §34.12). Aucun déclencheur.
- Leçon de méthode : un excès sur un indice de place peut n'être qu'un effet de profil (volatilité, tendance, liquidité). Toute piste de cette étude doit d'abord passer, sur la découverte elle-même, la mesure contre contrôles appariés du banc, avant de mobiliser la période scellée.
