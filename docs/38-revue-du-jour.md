# 38 — Revue du jour : la décision d'abord (lot R1)

> **Demande de l'utilisateur du 02/11/2026.** La page Opportunités était « clairement inexploitable » : trop d'informations diffuses, cinq étiquettes par valeur, aucune ligne « que faire ». À l'inverse, la revue écrite par Claude à partir des lettres (exemple fourni, D14 de docs/37) est « claire, interactive, didactique ». L'utilisateur a « moins d'infos, mais sait exactement quoi arbitrer, pourquoi, et quoi faire ou ne pas faire ».
>
> **Plan validé** (« ok ») : R1 revue du jour, puis R2 fiches simplifiées, puis R3 lecture des fiches et des gérants.

## 38.1 La page (accueil `/`)

L'ancienne page d'accueil (« Trois décisions », risque, contexte macro) reste disponible sur `/decisions`. La revue suit l'ordre de l'exemple de l'utilisateur :

1. **Verdict du jour**, en une ligne : « N achat(s) possible(s) aujourd'hui », « Aucun achat aujourd'hui » ou « Aucune idée aujourd'hui ». Il est suivi d'au plus quatre raisons courtes : coupe-circuit, portail de marché, puis nombre d'idées par décision.
2. **Ce que j'attends de toi**, par urgence :
   - urgent : position non protégée, cours d'une valeur détenue non mis à jour depuis plus de 2 séances ;
   - aujourd'hui : ordre à poser, stop à remonter, vente ou allègement ;
   - à vérifier : cours trop ancien pour décider.
3. **Marché** : les indices (séance, 5 séances) et les indicateurs macro avec leur évolution sur 5 points, le régime, le portail de marché et sa raison, le niveau de risque du jour, les annonces à 3 jours.
4. **Fiches du jour** : un bloc par valeur, la proposition la mieux classée.
   - **Une seule décision**, dans une liste fermée (`app/domain/review.py`) : ACHETER, ATTENDRE UN REPLI, À VÉRIFIER, INEXPLOITABLE, SURVEILLER, REFUS.
   - **Pourquoi** : la raison principale, en une phrase avec ses chiffres (« le dossier est trop faible au score (56 % des points, 80 % requis) »).
   - **Que faire**, en une phrase : poser l'ordre limite à X (titres, stop, validité) ; rien aujourd'hui (une limite à X n'achèterait qu'en cas de repli) ; vérifier le cours en direct ; rien.
   - La source : la lettre, son sens, les niveaux qu'elle écrit et sa citation, ou le détecteur.
   - Un petit tableau par objectif : gain brut et gain / risque brut **au cours de l'analyse** (arithmétique sur les valeurs stockées ; cours inconnu → « — »).
   - Le détail complet des contrôles reste sur la page Opportunités.
5. **Carnet d'ordres** : les limites « à poser » (ACHETER) et « prêtes pour un repli » (ATTENDRE UN REPLI), avec l'écart du cours.
6. **Portefeuille** : positions, cours et date du dernier cours (en rouge s'il est ancien), stop, verdict de la revue de portefeuille.
7. **Sources expertes** (gérants, podcasts, 10 derniers jours) :
   - un achat de gérant va en liste de surveillance : il ne publie ni stop ni objectif, donc il ne crée jamais d'entrée à lui seul (docs/23 §23.7) ;
   - « confirme la fiche du jour » quand la valeur a une fiche ;
   - une vente sur une valeur détenue invite à relire son stop.
8. **Sources** : lettres reçues, cours de clôture, heure de construction.

## 38.2 Règles de décision (`app/domain/review.py`)

Elles s'appliquent dans cet ordre :

1. **Inexploitable par construction** : le déclencheur publié par la lettre est au-dessus du prix maximal d'achat de l'outil. Aucun prix ne respecte à la fois la lettre et le calcul, et un repli ne lève pas la contradiction. C'est le cas d'Elior le 02/11/2026, vu par la revue de l'utilisateur.
2. **REFUS** : un blocage absolu (« BLOQUÉ : … ») ou une proposition NO_TRADE.
3. **À VÉRIFIER** : cours trop ancien (règle 2), jamais de fiche d'ordre.
4. **ACHETER** : BUY sans blocage ; ordre limite, ou ordre à plage pour une cassure.
5. **ATTENDRE UN REPLI** : la seule raison est « en attente d'un repli » ; c'est la règle du carnet d'ordres.
6. **SURVEILLER** : toute autre raison (score, stop trop large, pas d'objectif, résultats, détecteur en surveillance…).

Les raisons suivent un ordre fixe : blocage absolu, coupe-circuit, plafond, annonce, stop trop large, pas d'objectif, objectif trop proche, gain / risque, résultats, détecteur en surveillance, risque élevé, score, déclencheur, repli. Une raison inconnue est donnée telle qu'écrite, jamais omise.

## 38.3 Fiches de la page Opportunités (lot R2)

Chaque fiche montre d'abord :
- **une seule étiquette**, la décision de §38.2 ;
- **Pourquoi**, en une phrase chiffrée ;
- **Que faire**, en une phrase.

Tout le reste est replié derrière « analyse complète » : le niveau du verdict gradué, l'action du moteur, le statut, « à vérifier », les lettres qui confirment ou contredisent, l'historique comparable, la source, les niveaux, les prix, les objectifs et les contrôles. Les boutons de décision et le détail de l'instantané ne changent pas.

## 38.4 Envois

Le job `brief_premarket` (08:45 Paris) envoie la revue :
- sur Telegram (P3, ≤ 12 lignes : le verdict, deux raisons, trois choses à faire, quatre fiches) ;
- par e-mail : la revue d'abord, les 10 rubriques du rapport ensuite.

Le rapport en 10 rubriques reste archivé (`daily_reports`) et consultable sur `/rapport`.

## 38.5 Ce que R1 et R2 ne faisaient pas encore

- Lire les niveaux des fiches Momentum / Next Momentum : fait par R3 (§38.6).
- **Pas de rédaction par l'IA dans R1.** Le texte est déterministe. Une synthèse rédigée (règle 3 : chiffres absents des faits refusés) pourra venir ensuite si l'utilisateur la souhaite.

## 38.6 Lot R3 : lire les fiches et les gérants

**Constat du 02/11/2026.** Les fiches écrivaient leurs niveaux en toutes lettres :
- « On visera dans un premier temps le sommet historique à 105,20 euros » ;
- « une clôture au-dessus de 1,87 € », « pour cible la zone 2,05 – 2,15 € », « un stop d'invalidation sous 1,76 € ».

L'outil affichait pourtant « Aucun niveau chiffré dans la lettre ». La règle ne lisait qu'un nombre écrit juste après le mot (« objectif 60 »), et seulement dans le paragraphe qui nomme la valeur. L'IA, quand elle tourne, complète ; elle ne doit pas être indispensable.

**Lecture par règles** (`app/domain/letter_rules.py`, sans IA, testée) :
- un **mot-repère** donne son sens aux nombres qui le suivent dans la même phrase, à 60 caractères au plus :
  - objectif : objectif, cible, vise / visera, en ligne de mire ;
  - déclencheur : déclencheur, clôture au-dessus de, cassure de, franchissement de, au-delà de ;
  - invalidation (stop) : stop, invalidation, invalidé ;
  - support, résistance, achat / entrée ;
- le **repère le plus proche** l'emporte : « achat sur repli, objectif de 60 » donne un objectif ;
- une **liste d'objectifs** est numérotée dans l'ordre écrit (« 105,20 puis 106,45, 110,05 », « zone 2,05 – 2,15 ») ;
- « cassure de la résistance des X » donne un déclencheur ;
- ne sont **jamais des prix** : un pourcentage, un nombre de titres ou de séances, le jour d'une date, une heure, les chiffres d'un ISIN ou de « MM50 ».

**Attribution à la valeur** :
- un paragraphe qui nomme une seule valeur lui donne ses niveaux ;
- un paragraphe qui en nomme plusieurs répartit ses niveaux phrase par phrase ; une phrase qui nomme deux valeurs ne donne rien ;
- une fiche dont le titre nomme la valeur et dont le texte suit : les trois paragraphes suivants qui ne nomment aucune valeur restent à elle ;
- un paragraphe de marché (CAC 40, Bund, Brent…) arrête la fiche ;
- un titre « neutre » puis un paragraphe « achat » : la valeur prend le sens « achat ».

**Garde-fou** (`letters.level_price_band`, daté) : un niveau hors de [0,5 ; 2] × la dernière clôture de la valeur est écarté. Ce peut être un niveau d'indice lu dans la même fiche, une année ou un nombre. Il est signalé dans `newsletter_items.parsed.levels_out_of_band`, jamais corrigé. Sans clôture connue, rien n'est écarté.

**Conséquence sur la revue** : une fois le déclencheur lu, le moteur calcule son prix maximal. S'il est sous le déclencheur publié, la revue affiche « INEXPLOITABLE » (§38.2). Un test de bout en bout part du texte de la lettre et arrive à cette décision.

**Gérants** (« C'est votre argent ») :
- leurs achats et ventes des 10 derniers jours sont affichés (R1), qu'ils viennent de l'émission ou du relevé hebdomadaire ;
- la revue dit en plus, pour chaque source, son dernier épisode ou relevé (date d'émission), et s'il a été lu, avec ses achats et ventes ;
- si rien n'a été lu, elle le dit : pas d'épisode, pas encore lu (IA indisponible ou page illisible), ou lu sans valeur citée.

**Commandes** (lecture seule sauf `--fill`) :
- `letters-trace --date AAAA-MM-JJ --ia --rules` : ce que l'IA a lu, et ce que les règles actuelles lisent dans les lettres stockées (gratuit, aucun texte affiché) ;
- `letters-trace --date AAAA-MM-JJ --fill` : complète les valeurs restées **sans aucun niveau**. Une valeur qui a déjà des niveaux les garde ; propositions et instantanés ne changent pas (règle 10). La prochaine analyse du moteur lit les nouveaux niveaux.

**Corrections après le diagnostic du PC (02/11/2026, soir)** :
- `letters-trace` a montré que l'IA avait bien lu la lettre Momentum (Publicis « achat », Elior « à surveiller »), mais sans relever aucun niveau, alors que la citation de Publicis contenait « 105,20 ». La lecture par règles comble ce manque.
- **Mots courants pris pour des sociétés** : « momentum » (le nom de la lettre elle-même) était lu comme Momentum Group (Suède) et devenait une recommandation d'achat. De même « note », « evolution », « grande », « block », « text », « target », « week ». Ces mots ne sont plus cherchés seuls dans un texte libre (`app/domain/names.py`). L'IA, elle, peut toujours nommer ces sociétés.
- **Le lecteur décide du sens** : quand l'IA a lu la lettre, une valeur trouvée par les seules règles reste une **mention**, jamais une recommandation. Les règles donnaient « achat » à toutes les valeurs d'un paragraphe contenant le mot « achat ». Ses niveaux restent tels qu'écrits ; la liste est gardée dans `newsletter_items.parsed.rules_only_mentions`. Sans IA, les règles décident, comme avant.
- **Épisodes des gérants jamais enregistrés** : les épisodes du 30/10 étaient dans les flux, mais aucun n'était enregistré. La lecture des valeurs se faisait dans la même transaction que l'enregistrement, donc une erreur annulait les deux.
  - Les épisodes sont désormais enregistrés d'abord.
  - Chaque épisode est lu à part ; une erreur est écrite sur l'épisode (`media_erreur`) et l'épisode est relu au passage suivant.
  - L'erreur apparaît dans `media-check --feed`, dans la revue, et sur la ligne de fraîcheur du flux.

**Limite** : une fiche entièrement derrière un lien (capital.fr) n'est pas lue. Le suivi des liens « Lire la suite » ne vaut que pour Zonebourse (docs/19 §19.6). À décider après le diagnostic sur le PC.
