# 26 — Radar des signaux faibles publics

> Demande de l'utilisateur (25/10/2026) : un agent qui capte les orientations de marché et les signaux faibles (forums, réseaux spécialisés, sources pertinentes) pour les valeurs suivies et pour de nouvelles idées. Recherche documentaire du 25/10/2026 (sources et études ci-dessous) ; lot S1 développé en autonomie le 25/10/2026 ; **à valider**.

## 26.1 Cadre : public, agrégé, jamais déclencheur

- **Informations publiques seulement.** Une information privilégiée (MAR art. 7) ne peut être ni recherchée ni utilisée (MAR art. 14). Le radar ne lit que des données publiées par leur éditeur (API officielles, flux, données de marché) ; aucune source qui demande une connexion pour « voir avant les autres ».
- **Agrégats, jamais de messages.** Les pseudonymes de forum sont des données personnelles (CNIL, fiche « moissonnage » du 19/06/2025) : le radar ne stocke que des compteurs par valeur et par jour, jamais un message ni un auteur, et ne republie rien.
- **Un signal faible n'est jamais un déclencheur.** Il produit une **vigilance** (`caution`), une **corroboration** d'attention (`confirm`) ou une **idée à examiner** (`idea`) soumise au moteur normal. Il ne change aucun score tant que sa valeur n'est pas **mesurée** (§26.6).
- **Le LLM ne calcule pas.** Tous les indicateurs du radar sont calculés en Python (`app/domain/weak_signals.py`), testés.

## 26.2 Ce que disent les études (résumé)

| Constat robuste | Études | Usage dans l'outil |
|---|---|---|
| Un volume de messages ou d'attention **anormal** annonce du **volume et de la volatilité**, pas une direction | Antweiler & Frank 2004 (*J. Finance*) ; Sprenger et al. 2014 ; Cookson, Lu, Mullins & Niessner 2024 (*JFE*) : l'attention est corrélée entre plateformes, le sentiment ne l'est pas | vigilance, taille, stop |
| L'attention des **particuliers** est **contrariante** à quelques semaines ou mois (hausse courte puis retour) | Barber & Odean 2008 (*RFS*) ; Da, Engelberg & Gao 2011 (*J. Finance*, recherches Google) ; Kakhbod et al. 2023 (56 % des finfluenceurs « anti-compétents ») | jamais d'achat sur un emballement ; vigilance sur une position |
| Sur 87 valeurs **françaises** (2008-2018), Google (bruit) augmente la volatilité, **Wikipédia** (attention informée) la réduit | Hervé, Zouaoui & Belvaux 2019 (*Economic Modelling*) | Wikipédia = source principale du radar |
| Le sens d'un pic de consultations Wikipédia est **instable** selon les périodes | Moat et al. 2013 (*Sci. Rep.*) ; travaux récents contraires | signal d'attention, pas de direction |
| Le « sentiment Twitter » qui prédisait le marché **ne se reproduit pas** | Bollen et al. 2011 ; Lachanski & Pav 2017 | rejeté |
| Les forums sont un terrain de **manipulation** avérée | AMF, affaire Puccio / Bourse Direct (2014, forum Boursorama) ; ESMA 2021 et 2024 (finfluenceurs) | garde-fou d'emballement (§26.4) |

## 26.3 Sources retenues et rejetées

| Source | Signal | Accès | Lot |
|---|---|---|---|
| **Wikipédia** (Wikimedia REST, consultations quotidiennes par article) | attention informée | API officielle gratuite, sans clé, User-Agent descriptif | **S1 (fait)** |
| **Wikidata** (propriété P946 = ISIN) | correspondance ISIN → articles fr / en / de / … | API SPARQL officielle | **S1 (fait)** |
| **Volume anormal** (cours stockés) | volume ÷ médiane 60 séances, mouvement en ATR | interne | **S1 (fait)** |
| **Couverture presse** (nouvelles rattachées, flux et revue de presse) | articles sur 5 jours vs 60 jours | interne | **S1 (fait)** |
| GDELT DOC 2.0 | volume d'articles mondiaux et tonalité | API gratuite anonyme | S2 |
| Calendrier des rééquilibrages d'indices (Euronext, STOXX, MSCI) | flux passifs datés | communiqués, saisie manuelle | S2 |
| Reddit (API officielle, usage personnel) ; Bluesky (API ouverte) | mentions | gratuit, compte requis | **S3 (fait le 25/10/2026)** |
| Forum Boursorama (compteurs) | échanges des particuliers | pas d'API ; conditions d'utilisation restrictives — **risque accepté par l'utilisateur le 25/10/2026** | **S3 (fait)** ; ABC Bourse, Investing : à ajouter si utile |
| X / Twitter, StockTwits, LinkedIn, TikTok, YouTube | — | **rejetés** : coût (≥ 200 $/mois), couverture européenne nulle ou pas d'accès légal | — |
| Google Trends | attention bruitée | **différé** : bibliothèque non officielle archivée en 2025, API officielle en alpha sur candidature | — |

## 26.3 bis Forums et réseaux sociaux (lot S3, décision de l'utilisateur du 25/10/2026)

**Objectif** : repérer des **débuts de mouvement avant publication** — des échanges de particuliers qui s'emballent alors que le prix n'a pas encore bougé et qu'aucune information n'a été publiée — pour **compléter le momentum par des investissements d'opportunité**. Information publique uniquement (un forum est public) ; le radar ne cherche jamais une information non publique.

- **Sources** : forum Boursorama (première page du forum de chaque valeur suivie, dates des messages), Reddit (API officielle, application « script » : nouveaux messages et commentaires de r/vosfinances, r/bourse, r/eupersonalfinance, r/Finanzen, r/wallstreetbetsGER), Bluesky (API officielle, mot de passe d'application). L'utilisateur **accepte le risque** lié aux conditions d'utilisation des forums (blocage d'accès possible) ; lecture lente (une page toutes les 2 s), 200 valeurs au plus (positions, watchlist, propositions récentes, puis meilleure force relative).
- **Ce qui reste interdit** : stocker un message, un auteur ou un pseudonyme (données personnelles de tiers) — seuls des **compteurs par valeur et par jour** sont enregistrés (`attention_daily`, sources `forum_boursorama`, `reddit`, `bluesky`) ; un zéro n'est enregistré que si la page ou la liste couvre bien la journée (jamais d'estimation).
- **Étiquette « buzz précoce »** : z des échanges ≥ `weak_signals.social_z` (2,5), **aucune nouvelle publiée** (48 h), **prix encore immobile** (variation sur 5 séances < 1,5 ATR) → **idée à examiner** (rapport, accueil), jamais un achat en l'état. Sur une petite capitalisation avec un volume × 3 et sans nouvelle : « emballement », entrée bloquée (protection contre la manipulation, affaire Puccio).
- **Réactivité (demande de l'utilisateur du 25/10/2026 : « une info circule et on capte le signal avant l'impact sur le marché »)** :
  - **veille toutes les 20 minutes**, de 07:00 à 22:40 (heure de Paris), tous les jours, week-end compris (job `social_watch`), sur 60 valeurs suivies au plus ;
  - **vitesse des messages, sans historique** : messages des 2 dernières heures comparés au rythme des 24 heures précédentes, lu sur la même page (`weak_signals.velocity_ratio` 3, `velocity_min_messages` 5). Si la page ne remonte pas aussi loin (forum très actif), on compare au rythme de la semaine passée (compteurs stockés) ; à défaut, aucune conclusion : un forum chargé n'est jamais lu comme une accélération ;
  - à la détection : **alerte Telegram immédiate** (P2, selon le mode de disponibilité) et **passage ciblé du moteur** avec le détecteur **D8 « buzz précoce »** (limite juste au-dessus de la clôture, prix maximal +1,5 %, stop sous la structure, objectif = résistance suivante). Admission au ratio net, taille au niveau de risque du jour, plafonds et coupe-circuit habituels : une **fiche d'opportunité dimensionnée**, jamais un ordre. Une entrée intraday exige un cours de moins de 3 minutes (règle 2), sinon la fiche est « à vérifier ».
  - **Base dès le premier jour** : rattrapage de 60 jours (pages anciennes du forum, recherche Reddit), automatique pour 20 valeurs chaque matin, ou manuel avec `social-backfill`. La base de 30 jours ne sert plus qu'au z quotidien (plus robuste), pas à l'alerte rapide.
- **Mesure** : `radar-measure` dira si ces idées rapportent ; D8 a sa propre ligne dans l'historique des détecteurs.
- Commandes : `env-setup reddit`, `env-setup bluesky`, `social-check --isins …` (contrôle de lecture des pages et du format des dates, sans afficher de message).
- **Suite proposée (docs/30, lot S4)** : lecture du contenu des messages (thème, nature, précision, diversité des auteurs), croisement des familles de sources, vérification par l'actualité et corrélations — à valider.

### Mise en service sur le PC (25 et 26/10/2026)

- **Structure du forum relevée** (`social-check --structure --threads`). La liste des sujets porte une date de création par sujet (`c-source__time`) et l'heure du dernier message (`c-table__last-message`). Les fils portent les messages (`c-message`), leurs auteurs, leurs textes et leurs heures ; les citations d'un autre message sont ignorées. Ces classes sont les valeurs par défaut.
- **Mesure corrigée** : les comptages quotidiens et leur base de 60 jours comptent les **sujets créés par jour**, une date fixe (`date_class`). L'heure du dernier message (`activity_class`) sert à la veille de 20 minutes et à savoir jusqu'où lire les pages. Défaut corrigé : compter chaque sujet au jour de son dernier message faisait paraître les jours passés calmes, donc toutes les valeurs « en buzz ».
- **Comptages faibles** : les forums ont leur propre plancher de dispersion (`weak_signals.social_min_mad` 0,3) et un minimum de 5 sujets ou messages sur 3 jours (`social_min_recent`). Deux sujets en trois jours sur un forum calme ne sont plus un « buzz ».
- **Couverture** : le forum Boursorama ne couvre que Paris ; les autres places dépendent de Reddit et Bluesky (à configurer) ou d'un forum par place (à étudier).
- **Premier passage** (`radar-check --views`, 26/10/2026) : 1 115 articles Wikipédia pour 1 635 couples valeur × langue, 45 étiquettes, dont 2 « buzz précoce » plausibles.
- **Contenu des messages** : lot S4a (docs/30).
- **Mesure sur l'historique** (décision de l'utilisateur du 26/10/2026, option B) : juger les étiquettes sur un vrai historique.
  - **Wikipédia depuis juillet 2015** (début des statistiques de consultation) : `radar-history wiki` charge en une fois les consultations quotidiennes des articles des valeurs suivies (`--from`, défaut 2015-07-01).
  - **Forum Boursorama depuis `weak_signals.social.history.forum_from`** (2019-01-01) : lecture **lente**, la nuit, par le job `forum_history` (toutes les 30 min de 0 h à 5 h, Paris), au plus `pages_per_run` pages par passage et `values_max` valeurs de Paris (positions, watchlist, propositions, meilleures forces relatives). Les sujets sont comptés au **jour de leur création**, chaque numéro de fil une seule fois (un sujet qui descend d'une page pendant la lecture n'est pas recompté). Un jour ne vaut zéro que si les pages lues le couvrent. Un refus du site (HTTP 403 ou 429) arrête la lecture pendant `block_hours` (24 h) : jamais de relance en boucle. L'avancement par valeur est dans `forum_history_progress` ; `radar-history status` l'affiche.
  - Ces séries servent à l'enregistrement n° 2 du banc de recherche (docs/31) : aucune étiquette ne change de rôle sans ADR.
  - **Limite du site (constat du 27/10/2026, `radar-history probe`)** : la liste des sujets ne se pagine que sur une quarantaine de pages ; au-delà, le site renvoie la page 1. Pour un forum très actif (FR0013506730 : 40 sujets par page, page 20 ≈ octobre 2025), l'historique ne remonte donc qu'à environ un an. Pour un forum calme, il remonte beaucoup plus loin. Trois pages de suite sans nouveau sujet arrêtent la lecture de la valeur. La couverture réelle de chaque valeur (`plus_ancienne_activite`) borne les zéros comptés : aucun jour non couvert n'est compté comme zéro.

## 26.4 Calculs (Python, `app/domain/weak_signals.py`)

- **z robuste** : `z = (x − médiane) / (1,4826 × MAD)` sur une base de `weak_signals.baseline_days` (60) jours qui **exclut** la fenêtre récente ; `x` = moyenne des `recent_days` (3) derniers jours ; consultations en log (`log(1 + vues)`), plancher de MAD pour les pages peu vues (`min_mad`). Moins de `min_history_days` (30) jours → « historique insuffisant », jamais d'estimation.
- **Volume anormal** : `rvol = volume du jour ÷ médiane des 60 séances précédentes` ; `mouvement = |clôture − clôture veille| ÷ ATR14`. « Volume sans mouvement » = `rvol ≥ volume_rvol_min` (2,5) et `mouvement < quiet_move_atr` (0,5).
- **Presse** : nombre de nouvelles rattachées à la valeur sur 2 jours (corroboration) et sur 5 jours vs moyenne 60 jours.
- **Étiquettes** (une par valeur et par jour, la plus forte) :

| Étiquette | Règle | Effet |
|---|---|---|
| `emballement` | attention `z ≥ pump_z` (3) **et** petite capitalisation **et** aucune nouvelle rattachée sur 48 h **et** `rvol ≥ pump_rvol` (3) | `BLOQUÉ : emballement non corroboré (attention et volume anormaux, aucune information publiée)` sur toute entrée ; rappel du stop sur une position |
| `attention_sans_nouvelle` | `z ≥ attention_z` (2) sans nouvelle sur 48 h | vigilance sur la fiche ; sur une position : « ne pas renforcer » |
| `volume_sans_mouvement` | volume anormal sans mouvement de prix, sans nouvelle | vigilance (accumulation ou information non publique possible : **ne rien supposer**) |
| `attention_corroboree` | `z ≥ attention_z` **avec** nouvelle publiée | fait affiché ; **idée à examiner** si la valeur n'a pas de proposition et que sa force relative ≥ `idea_min_rs` (70) |

## 26.5 Intégration

- **Job `weak_signals`** (06:40, jours de marché, après la publication des consultations de la veille) : correspondances Wikidata (une fois, puis chaque lundi pour les nouvelles valeurs), consultations des 90 derniers jours au premier passage puis des 7 derniers ; calcul des étiquettes ; table `attention_daily` (valeur, jour, source, langue, vues) et `weak_signal_hits` (valeur, jour, étiquette, z, rvol, presse, détails).
- **Moteur** : l'étiquette du jour est lue par l'évaluation ; `emballement` bloque l'entrée ; les autres sont des faits de la fiche et de la thèse (numérotés, citables).
- **Rapport du jour** : rubrique « Radar » — vigilances sur les positions, idées à examiner (5 au plus), emballements.
- **Comité (docs/24)** : faits du dossier, jamais un vote.

## 26.6 Mesure avant tout usage dans le score

Chaque étiquette est datée de **sa séance** (celle des cours utilisés) et la fenêtre de nouvelles va de la veille de cette séance à l'heure du calcul (une publication du vendredi compte encore le lundi matin). `radar-measure` calcule, pour chaque étiquette, le rendement **depuis la clôture de la séance suivante** (l'étiquette n'est connue que le lendemain matin) à 5 et 20 séances, en excès de la médiane de l'univers, avec n et t. Une étiquette n'entre dans le score que par un ADR, avec t ≥ 3 sur une période de contrôle (comme docs/27). Le blocage « emballement » est une **protection** : il s'applique sans attendre cette mesure.

Limites signalées par la contre-expertise du 25/10/2026, à traiter au lot S2 : étiquettes d'un même jour corrélées et fenêtres qui se chevauchent (t optimiste → t groupé par date) ; médiane de l'univers favorable aux petites capitalisations (→ comparaison au même quantile de taille et de secteur) ; la littérature prédit **volume et volatilité**, pas une direction (→ cible supplémentaire : volatilité réalisée sur 5 séances ÷ ATR, et R des propositions avec ou sans étiquette).

## 26.7 Paramètres (`weak_signals.*`)

`effective_from`, `enabled`, `wiki_languages` (par place : fr pour Paris, de pour Xetra…, toujours en), `user_agent`, `baseline_days` 60, `recent_days` 3, `min_history_days` 30, `min_mad` 0,15, `attention_z` 2, `pump_z` 3, `pump_rvol` 3, `volume_rvol_min` 2,5, `quiet_move_atr` 0,5, `news_hours` 48, `idea_min_rs` 70, `max_ideas` 5, `request_interval_s` 0,05.

## 26.8 Critères d'acceptation

- **A26.1** Aucun signal faible ne crée seul une proposition BUY (test).
- **A26.2** `emballement` bloque l'entrée avec la mention `BLOQUÉ` (test).
- **A26.3** Historique insuffisant ou page introuvable → « indisponible », jamais d'estimation (test).
- **A26.4** Aucun message ni auteur stocké : seuls des compteurs (revue du schéma).
- **A26.5** `radar-measure` donne l'excès de rendement par étiquette avec t et n.
