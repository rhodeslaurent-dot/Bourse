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
| Reddit (API officielle, usage personnel) ; Bluesky (API ouverte) | mentions, désaccord | gratuit, compte requis | S3, si S1 mesuré utile |
| Forums Boursorama, ABC Bourse, Zonebourse, Investing | — | **rejetés** : pas d'API, conditions d'utilisation qui interdisent la reproduction (CJUE *Ryanair*, C-30/14), risque de manipulation avéré | — |
| X / Twitter, StockTwits, LinkedIn, TikTok, YouTube | — | **rejetés** : coût (≥ 200 $/mois), couverture européenne nulle ou pas d'accès légal | — |
| Google Trends | attention bruitée | **différé** : bibliothèque non officielle archivée en 2025, API officielle en alpha sur candidature | — |

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
