# 30 — Contenu des messages, croisement des sources et corrélations (lot S4 du radar)

> Demande de l'utilisateur (25/10/2026) : « analyser le contenu des messages pour voir si certains sont intéressants et nécessitent d'être creusés pour détection ou confirmation de signaux faibles ; croiser les sources, vérifier l'information avec l'actualité, analyser les corrélations ». Complète docs/26 (radar, lot S3 : compteurs). Statut : **validé par l'utilisateur le 25/10/2026** (options recommandées du §30.10) ; **S4a développé le 25/10/2026** (lecture, classement, solidité, fiche) ; S4b et S4c à faire.

## 30.1 Ce qui change par rapport à docs/26

docs/26 §26.1 ne lit que des **compteurs**. Le lot S4 lit aussi **le contenu** : un emballement chiffré ne dit pas *de quoi* on parle. Pourtant, une rumeur précise de contrat, répétée par neuf personnes différentes, ne vaut pas un seul auteur qui publie vingt fois « ça va exploser ».

Ce qui ne change pas :
- **information publique seulement** ;
- **jamais un déclencheur seul** (A26.1) ;
- **le LLM classe et résume, Python compte et décide** (règle 3) ;
- **aucun auteur ni pseudonyme conservé**.

## 30.2 Quand lire le contenu (coût, politesse, bruit)

Le contenu n'est pas lu pour toutes les valeurs. Il est lu :

1. **sur déclenchement**, quand `social_watch` ou le job `weak_signals` détecte un `buzz_precoce`, une vitesse au-dessus de `velocity_ratio`, ou un z ≥ `social_z` : messages des dernières 24 h de la valeur, `max_messages_per_value` au plus ;
2. **sur les positions ouvertes**, une fois par jour : nouveaux messages depuis la veille, pour capter un catalyseur négatif qui circule ;
3. **à la demande**, avec un bouton « Lire les échanges » sur la fiche et la page radar.

La lecture couvre les pages de fil (sujets récents), et non plus la seule liste des sujets. Il faut donc 1 à 3 pages de plus par valeur déclenchée, toujours une page toutes les `interval_s` secondes. Plafond : `max_values_per_day`.

## 30.3 Classement de chaque message (LLM, sortie JSON validée, listes fermées)

Les messages sont envoyés par lots de `batch_size` au modèle léger (Haiku 5.5 avec les options de génération 5, ADR-011). Le cache par empreinte rejoue la même réponse. Le pseudonyme n'est **jamais** envoyé au modèle. Pour chaque message :

| Champ | Valeurs |
|---|---|
| `theme` | mêmes types que la presse (`resultats`, `guidance`, `contrat`, `m_and_a`, `augmentation_capital`, `gouvernance`, `reco_broker`, `transaction_dirigeant`, `franchissement_seuil`, `rachat_actions`, `reglementaire`, `dividende`), plus `technique` (chartisme), `sentiment` (aucun fait) et `autre` |
| `nature` | `fait_source` (cite une source vérifiable), `relais_presse` (reprend un article publié), `rumeur` (aucune source), `opinion`, `question`, `promotion` (« va exploser », objectif irréaliste, appel à acheter), `pretendue_info_privilegiee` (« je sais de l'intérieur ») |
| `direction` | `positif`, `negatif`, `neutre`, `ambigu` |
| `precision` | `precise` (date, montant, contrepartie nommée), `vague` |
| `source_citee` | `aucune`, `presse`, `communique`, `regulateur`, `reseau`, `autre` ; le domaine du lien seulement s'il y en a un |
| `resume` | 25 mots au plus, neutre, **sans pseudonyme ni citation** ; un nombre n'y figure que s'il est dans le message (garde-fou des nombres, règle 3) |

Une sortie invalide classe le message « non classé ». Rien n'est jamais deviné.

## 30.4 Agrégats calculés en Python (par valeur et par fenêtre de 2 h / 24 h)

- **Volumes** : nombre de messages par thème, par nature et par direction, et part des messages `precise`.
- **Diversité des auteurs** :
  - nombre d'auteurs **distincts**, compté en mémoire avec une empreinte salée éphémère (le sel change à chaque lecture ; ni l'empreinte ni le pseudonyme ne sont stockés) ;
  - **concentration** : part du premier auteur dans les messages.

  Un emballement porté par un ou deux comptes est le signe classique d'une manipulation.
- **Thème dominant** et **solidité** du signal :
  - `fort` : thème factuel (ni `technique` ni `sentiment`), au moins `min_distinct_authors` auteurs, concentration sous `author_concentration_max`, part de `promotion` sous 30 % ;
  - `faible` : signal présent, mais une de ces conditions manque ;
  - `bruit` : aucun thème factuel.

## 30.5 Croisement des sources et vérification par l'actualité (Python, LLM seulement pour « même événement ? »)

**Familles indépendantes.** Les forums et les réseaux comptent pour **une seule** famille, parce qu'ils se recopient. Les familles sont :

| Famille | Sources |
|---|---|
| Particuliers | forum, Reddit, Bluesky |
| Attention | consultations Wikipédia |
| Marché | volume relatif, écart du prix au secteur |
| Presse | `news_items` rattachées |
| Réglementaire | transactions de dirigeants, franchissements de seuil, positions courtes AMF |
| Lettres | lettres d'analyse |

La **concordance** est le nombre de familles en anomalie dans la même fenêtre (24 h ou 3 séances).

**Vérification par l'actualité.** Pour le thème dominant, Python cherche les nouvelles rattachées à la valeur entre −7 jours et maintenant, de même type :
- `deja_publiee` : un article est paru **avant** le premier message. Ce n'est pas un signal faible, c'est un relais.
- `confirmee` : un communiqué ou un article de même type paraît **après**. Le délai d'avance est mesuré.
- `dementie` : la société dément.
- `non_confirmee` : aucune trace pour l'instant.

Si plusieurs candidats existent, le LLM répond seulement « même événement : oui / non / incertain » (liste fermée). Pour une rumeur `precise` non confirmée, une recherche ciblée est faite dans les flux déjà configurés (nom + thème). Aucune source nouvelle n'est ajoutée sans `sources-check`.

## 30.6 Corrélations

1. **Avance / retard.** Les anomalies d'échanges précèdent-elles le volume, la volatilité ou la direction ? On mesure l'excès de rendement et la volatilité réalisée à 1, 5 et 20 séances, par thème × nature × solidité. Les valeurs sont regroupées par taille et secteur, et le t est groupé par date (limites de docs/26 §26.6).
2. **Contagion.** Un buzz sur une valeur, avec des pairs du même secteur (ρ ≥ 0,6 sur 120 séances, `aligned_correlation`) qui bougent ou s'animent aussi, donne un **thème sectoriel**. Sinon, c'est un **fait propre à la valeur**. La fiche le dit.
3. **Retour d'expérience.** Pour chaque couple thème × nature :
   - part des rumeurs ensuite **confirmées** par la presse ;
   - délai médian d'avance ;
   - R moyen des fiches D8 avec et sans contenu `fort`.

   Ces mesures alimentent `radar-measure` puis `/apprentissage`. Elles n'entrent dans le score que par un ADR, avec t ≥ 3 en période de contrôle (docs/26 §26.6).

## 30.7 Effets sur le moteur (règles déterministes)

| Situation | Effet |
|---|---|
| `buzz_precoce` + solidité `fort` + concordance ≥ 2 familles + `non_confirmee` | **idée renforcée** : priorité dans le rapport et la veille, fiche D8 dimensionnée comme aujourd'hui (jamais un ordre) |
| `buzz_precoce` + solidité `faible` ou `bruit` | idée simple (comme aujourd'hui) ou mention seule |
| `deja_publiee` | pas une idée de signal faible : traité par le flux de nouvelles normal |
| `promotion` majoritaire, ou concentration au-dessus du seuil, sur une petite capitalisation | **emballement** : entrée `BLOQUÉ : emballement` |
| `pretendue_info_privilegiee` | **jamais exploité** : exclu des comptes de solidité, mention « message se réclamant d'une information privilégiée : non exploité (MAR art. 8 et 14) » |
| Position ouverte, thème négatif `fort` `non_confirmee` | vigilance P2 sur la position (« rumeur négative, à surveiller ») ; elle passe en P1 « catalyseur négatif » seulement si la presse confirme |

Les faits de la fiche et de la thèse sont numérotés et citables. Exemple : « forum : 14 messages en 2 h, 9 auteurs distincts, thème contrat, rumeur précise, non confirmée par la presse ». L'instantané de décision (règle 10) conserve ces agrégats et les résumés.

## 30.8 Données conservées

**Option A (recommandée).** Aucun texte de message ni auteur n'est stocké. La table `social_content` (valeur, fenêtre, source) conserve :
- les agrégats du §30.4 et le statut de vérification ;
- au plus `summaries_max` résumés neutres ;
- le lien du **fil**, jamais d'un message ou d'un profil.

Le cache LLM ne garde que l'empreinte et la réponse, qui ne contient pas d'auteur.

**Option B.** Les textes sont conservés 30 jours, sans auteur, pour relire et réanalyser après coup. Mais le texte peut contenir des données personnelles de tiers (prénoms, signatures), et ce n'est pas nécessaire pour la mesure.

## 30.9 Coûts estimés (tarifs de `llm.prices_usd_per_mtok`, tokeniseur de génération 5 ≈ +30 %)

- **Classement par message** (Haiku 5.5 : 0,10 $ / 0,50 $ par million de jetons) : un lot de 20 messages représente ≈ 4 000 jetons en entrée et 1 500 en sortie, soit ≈ 0,001 $.
  - 15 valeurs déclenchées × 60 messages ≈ 45 lots ≈ **0,05 $ par jour**.
  - Pire cas (`max_values_per_day` 30) : ≈ 0,10 $.
- **« Même événement ? »** (Haiku 5.5) : quelques appels par jour, moins de 0,01 $.
- **Plafond** : `weak_signals.content.max_usd_per_day` = 0,30 $, à l'intérieur du budget LLM journalier. Au-delà, retour aux compteurs seuls, avec la mention « contenu non analysé (plafond) ».

Ordre de grandeur : **≈ 1,5 à 3 $ par mois**.

## 30.10 Décisions de l'utilisateur (25/10/2026 : « je valide et suis tes préconisations »)

Retenu : option A (aucun texte ni auteur conservé) ; messages se réclamant d'une information privilégiée **exclus** ; lecture sur déclenchement, positions et à la demande ; découpage S4a → S4b → S4c.

Pour mémoire, les choix proposés :

1. **Données conservées** : option A (recommandée) ou option B (§30.8).
2. **Messages se réclamant d'une information privilégiée** : exclus (recommandé : risque juridique réel, MAR art. 8, puisque celui qui « sait ou aurait dû savoir » est visé ; c'est aussi un procédé classique de manipulation), ou simple mention.
3. **Périmètre** : lecture sur déclenchement + positions + à la demande (recommandé), ou lecture quotidienne de toutes les valeurs suivies (coût × 5, et risque de blocage du forum plus élevé).
4. **Découpage** :
   - S4a : lecture des fils, classement, agrégats, fiche ;
   - S4b : vérification par l'actualité et concordance ;
   - S4c : corrélations et mesure.

   On commence par S4a, après le diagnostic de structure des pages (`social-check --structure`).

## 30.11 Paramètres proposés (`weak_signals.content.*`)

`enabled`, `effective_from`, `model` (par défaut `llm.classify_model`), `batch_size` 20, `max_messages_per_value` 60, `max_values_per_day` 30, `max_usd_per_day` 0,30, `min_distinct_authors` 3, `author_concentration_max` 0,4, `promotion_share_max` 0,3, `families_min_reinforced` 2, `verify_window_days` 7, `summaries_max` 3, `thread_pages_max` 3.

## 30.12 Critères d'acceptation

- **A30.1** Aucun auteur ni pseudonyme dans une table, un journal, un message Telegram ou une requête au LLM (test sur une page de fixture contenant des pseudonymes).
- **A30.2** Sortie de classement validée par listes fermées ; une sortie invalide classe le message « non classé » (test).
- **A30.3** Un nombre absent du message est retiré du résumé (garde-fou, test).
- **A30.4** `pretendue_info_privilegiee` ne contribue jamais à une idée ni à la solidité (test).
- **A30.5** Concentration ou promotion au-dessus du seuil sur une petite capitalisation → `BLOQUÉ : emballement` (test).
- **A30.6** Statuts `deja_publiee` / `confirmee` / `dementie` / `non_confirmee` sur fixtures datées (test).
- **A30.7** `radar-measure` par thème × nature × solidité, avec n, t groupé par date et délai d'avance sur la presse.
- **A30.8** Plafond de dépense journalier respecté ; au-delà, compteurs seuls avec la mention (test).
- **A30.9** Aucun contenu ne crée seul une proposition BUY (A26.1 étendu, test).

## 30.13 Mise en œuvre de S4a (25/10/2026)

- **Code** : `app/domain/social_content.py` (agrégats, solidité, effet — pur, testé), `app/data/providers/social.py` (`forum_messages` : pseudonymes de la page et mentions remplacés par « [membre] », liens réduits au domaine, auteur → empreinte salée éphémère ; `thread_links`, diagnostics `link_patterns` et `repeated_classes`), `app/llm/schemas.py` (`ForumBatchLabels`, listes fermées, valeur inconnue → `non_classe`), invite `forum_classify_v1`, `app/services/social_content.py` (lecture, plafonds, garde-fou des nombres, table `social_content`, migration 0022).
- **Déclenchements** : `social_watch` (valeurs étiquetées, avant le passage ciblé du moteur ; lignes « Forum : … » dans l'alerte), job `weak_signals` (positions ouvertes, P2 « rumeur négative » si thème négatif solide), bouton « Lire les échanges » sur `/valeur/<isin>`, commande `social-content --isins …`.
- **Moteur** : `echanges_forum` dans les détails de la fiche ; `BLOQUÉ : emballement — échanges portés par peu de comptes ou promotionnels sur une petite capitalisation`. L'effet `renforce` est une mention, jamais un achat (A30.9) ; la priorité « idée renforcée » attend la concordance des familles (S4b).
- **Réglage sur le PC** : la structure des pages de fil Boursorama n'est pas connue d'avance. `social-check --isins … --threads` affiche les formes des liens et les classes répétées (jamais un texte) ; on règle ensuite `weak_signals.social.boursorama.thread_link_regex`, `message_class`, `author_class`, `text_class`, `message_date_class`. Tant que ce n'est pas fait, statut « structure » : rien n'est deviné.
- **Critères** : A30.1 à A30.5, A30.8 et A30.9 testés (`tests/unit/test_social_content.py`, `tests/integration/test_social_content.py`) ; A30.6 et A30.7 relèvent de S4b et S4c.

### Structure du forum Boursorama relevée le 25/10/2026 (`social-check --structure --threads`)

- **Liste des sujets** (`/bourse/forum/1rP<ticker>/`) : environ 40 sujets par page ; liens des fils `/detail/<n>/` ; deux dates par sujet sans heure (`c-source__time`) ; heure du **dernier message** dans `c-table__last-message` (« 14:32 » pour aujourd'hui, « 24 oct. 14:32 » sinon, affichée deux fois par ligne). Réglage `date_class: c-table__last-message` : chaque sujet compte une fois, au jour et à l'heure de son dernier message. Les comptages du radar mesurent donc des **sujets actifs** par jour, et la vitesse sur 2 h devient mesurable (il n'y avait aucune heure auparavant). Les vrais messages sont comptés dans les fils (lecture du contenu).
- **Fil** : message `div.c-message`, auteur `div.c-profile-card__name`, texte `p.c-message__text`, heure `span.c-source__time` (« 14:32 », « 24 oct. 2026 »), citation d'un autre message `div.c-message--quote` (ignorée : jamais relue dans la réponse). Les classes sont comparées **exactement** (`c-message` ne désigne pas `c-message__text`).
- Ces classes sont les valeurs par défaut de `config/params.example.yaml` ; si Boursorama change sa page, `social-check --threads` le montre et les comptes passent à « indisponible », jamais à une estimation.
