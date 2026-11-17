# 40 — Étape 1 : recueil, historique et mesure de toutes les sources (lots S1 à S5)

- **Demande de l'utilisateur (15/11/2026)** : « Je veux que tu t'assures que toutes les sources précédemment identifiées sont bien stockées chaque jour, historisées, comparées quotidiennement aux mouvements de marché et aux événements conjoncturels. L'outil doit travailler méthodiquement et traiter un maximum de données. »
- **Place dans le plan de l'utilisateur** : étape 1 (recueil et intégration en base). L'étape 2 (analyse multi-agent, ADR-020) et l'étape 3 (classement par place selon le rapport gain / risque, par anticipation) s'appuient sur ce socle.
- **Principe** : des faits seulement. Chaque chiffre de ce document est un comptage de lignes stockées ou un calcul sur des cours stockés ; rien n'est estimé. La mesure est **descriptive** : aucun seuil de ce lot ne décide une proposition (règle d'observation P1 et ADR-020 : seule la mesure, une fois assez nombreuse, désignera ce qui mérite d'être suivi).

## 40.1 S1 — Santé de chaque source, chaque jour

- **Registre** (`app/services/source_health.py`, `registry()`) : 21 sources, chacune avec la table où elle arrive, la colonne qui date un élément, la cadence attendue, les places couvertes et le job qui l'alimente.

| Famille | Sources |
|---|---|
| Cours et indicateurs | cours de clôture (16 places Europe et US), indicateurs techniques |
| Macro et conjoncture | régime par place, taux de change BCE, séries macro (VIX, Brent, or, taux, indices), calendrier macro |
| Actualités | flux RSS |
| Momentum | lettres (Gmail), podcasts et médias, dirigeants (AMF), consensus des analystes, fondamentaux, résultats, forums (comptages et contenu), Wikipédia |
| Sources officielles | positions courtes publiées, communiqués officiels (AMF) |
| Signaux de l'outil | radar, signaux des détecteurs et des lettres, journal des événements |

- **Instantané du soir** (table `source_health`, une ligne par jour et par source) : éléments du jour (jour calendaire de Paris), des 7 derniers jours, total, premier et dernier élément, jours ouvrés de silence, état `à jour` / `en retard` / `vide`. Une source est **en retard** quand elle se tait plus de jours ouvrés que sa cadence ne le permet (`max_silent_weekdays` du registre : 2 pour les cours, 5 pour les lettres, 7 pour les podcasts…).
- **Alerte** : un message P3 « source(s) muette(s) » au **premier** soir de retard seulement (pas un message par jour de silence).
- **Où le voir** : page `/sources` (état par famille et bande des 14 derniers jours), ligne « sources à jour n/N » dans la revue du jour, commande `sources-health`.

## 40.2 S2 — Un journal unique des événements

- **Table `market_events`** : chaque élément d'une source qui concerne une valeur devient une ligne de même forme — quand il est devenu public (`event_at`), ce qu'il dit (`kind`, `direction` +1 / −1 / 0 ou rien, `strength`), d'où il vient (`ref_table`, `ref_id`) et le **contexte du jour** (régime de la place, ou de l'Europe à défaut ; VIX de moins de 7 jours ; annonce macro à fort impact à un jour près).
- **Sources versées au journal** : lettres (achat / vente / surveillance), podcasts et médias (achat / vente / citation), nouvelles classées (une ligne par valeur citée), déclarations des dirigeants (acquisition / cession), communiqués officiels (par famille), consensus (variation de l'objectif moyen d'au moins `event_journal.consensus_min_change`), résultats (surprise sur le BPA), radar (étiquette), forums (emballement analysé), positions courtes (nouvelle, hausse, baisse), part vendue à découvert FINRA (lot S5), signaux des détecteurs.
- **Le sens est celui de la source**, jamais une opinion de l'outil : une lettre « achat », une nouvelle classée positive, un achat d'un dirigeant. Une position courte qui apparaît ou grossit est lue −1, qui diminue +1. Un communiqué ou une étiquette du radar n'a pas de sens.
- **Date de publication prudente** : une position courte est publique le jour ouvré suivant sa date (18:00 Paris) ; un fait daté du seul jour (consensus lu le soir, étiquette du radar sans heure) est daté 22:00 Paris.
- **Idempotent** : une ligne n'est jamais écrite deux fois (contrainte `ref_table, ref_id, kind, isin`) ; une source en échec n'empêche pas les autres.

## 40.3 S3 — Ce qui a suivi chaque événement, mesuré chaque soir

- **Référence** : la clôture de la **première séance de la place de la valeur** qui se termine après la publication (on pouvait agir à cette clôture). Une lettre reçue à 10:00 se mesure depuis la clôture du jour ; une déclaration publiée à 19:00 depuis celle du lendemain.
- **Mesure** (table `event_outcomes`) à `event_journal.horizons` séances (1, 5, 20) : rendement de la valeur, **médiane des autres valeurs de sa place** sur les mêmes séances (au moins `event_journal.venue_min_members` valeurs), médiane de son **secteur sur la place** (au moins `event_journal.sector_min_members`), et les écarts.
- **Population de la médiane** (`event_journal.median_population`, 16/11/2026) : **toutes les actions de la place** cotées aux deux dates, qu'elles soient restées dans l'univers ou non. La première mesure prenait l'univers d'aujourd'hui : des sociétés qui ont survécu et grandi par construction, contre lesquelles toute valeur prise au moment de l'événement (y compris celles qui ont baissé, puis quitté l'univers ou la cote) paraissait en retard — d'autant plus que l'horizon est long (−0,1 % à 1 séance, −0,9 % à 20 séances pour les communiqués).
- **Témoin** (table `event_controls`, `event_journal.control_offsets`, conçu et **fixé le 16/11/2026 avant toute lecture d'un résultat témoin**) : pour chaque événement et chaque horizon, la même valeur sur le même nombre de séances, **60 séances avant** et **60 séances après** sa clôture de référence, mesurée exactement comme l'événement (médiane de toute la place, dividendes inclus). Une fenêtre qui contient un autre événement de la même source sur la valeur (y compris publié après la clôture de la veille) n'est pas un témoin (`unavailable`, dit). Le témoin absorbe la dérive de la population de la source et la tendance propre de chaque valeur.
- **Règle de lecture pré-enregistrée** : une source (type et sens) est **retenue** pour l'étape 2 seulement si son **écart au témoin** (moyenne orientée après l'événement moins moyenne orientée de ses témoins, t de Welch) a **|t| ≥ 3** avec **au moins 300 événements et 300 fenêtres témoins**, **au même signe à 5 et à 20 séances**. Toute autre ligne reste descriptive. La règle ne change pas après lecture ; une modification ultérieure est datée et justifiée ici.
- **Contraste dans une même source** : après les événements « hausse » (achat, surprise positive) moins après les événements « baisse » (vente, surprise négative), en écart brut, avec un t de Welch. Les deux groupes partagent la dérive de leur population : le contraste en est indemne, alors que chaque moyenne orientée ne l'est pas. C'est la lecture à privilégier tant qu'il n'y a pas de témoin.
- **Dividendes inclus** (16/11/2026, après la première mesure en production) : le rendement de la valeur et celui des valeurs de la place se lisent sur la clôture **multipliée par le facteur d'ajustement** `adj_close / close` aux deux bouts de la fenêtre. Sur les clôtures brutes, un détachement de dividende comptait comme une perte. Un facteur chargé avant qu'un dividende suivant soit connu reste à 1 (chargement du jour) : la correction peut rester en deçà, jamais au-delà ; sans facteur, le rendement brut est gardé. `events-measure --redo` efface toutes les mesures et les refait sur les mêmes clôtures.
- **États** : `final`, `pending` (fenêtre pas encore close : remesuré le soir suivant), `unavailable` (opération sur titre ou cours aberrant dans la fenêtre — saut du facteur d'ajustement de plus de 20 % ou cours ×4 / ÷4 —, ou cours manquants trop longtemps). Une mesure finale n'est jamais réécrite.
- **Contexte historique** (17/11/2026) : le régime de la place n'existe que depuis le 20/10/2026 et le VIX stocké depuis 2023 — la plupart des événements depuis 2016 avaient un contexte « inconnu ». `events-build --complete-context` ajoute, **sans jamais remplacer une valeur stockée**, le VIX de la séance précédente (7 jours au plus) et la **tendance de l'indice de la place** (`indice_mm50` : au-dessus / en dessous de la moyenne de ses 50 dernières clôtures, lue la veille ; l'indice européen pour une place sans indice), calculée sur l'indice seul, donc sans biais de population. Le journal du soir l'ajoute aux nouveaux événements ; le VIX retenu est désormais celui de la séance précédente (la clôture du jour, 22:15 Paris, n'était pas connue à 10:00).
- **Tableau de mesure** (`scoreboard`) : par source, type et sens, l'écart moyen à la place **lu dans le sens de la source** (une « vente » qui a raison donne un écart positif), la médiane, le taux de réussite, le t, et la même chose **par contexte** : régime de la place, VIX < 20 ou ≥ 20, annonce macro proche ou non, et contre le secteur.
- **Où le voir** : page `/sources` (choix de l'horizon), commande `events-scoreboard --horizon 5 --context 10`.

### 40.3 bis — Résultats du 17/11/2026 (production : 374 953 événements, 2016 → 2026)

Mesure dividendes inclus, médiane de **toute** la place, témoin ±60 séances. Règle pré-enregistrée (16/11/2026) : écart au témoin (les deux fenêtres réunies) avec |t| ≥ 3, ≥ 300 événements et ≥ 300 témoins, même signe à 5 et 20 séances. **Vérification ajoutée après coup, non pré-enregistrée** : chaque fenêtre témoin lue seule (une hausse avant l'événement tombe dans la fenêtre « avant »).

| Source (sens) | Écart au témoin, 5 / 20 séances (t) | Témoin « avant » seul, 20 séances | Témoin « après » seul, 20 séances | Verdict |
|---|---|---|---|---|
| Ventes de dirigeants (baisse attendue) | +0,7 % (3,6) / +3,8 % (9,3) | +5,6 % (10,2) | +1,7 % (3,5) | **retenue à 20 séances** ; à 5 séances, seulement contre « avant » (+0,3 %, t 1,1 contre « après ») |
| Déclarations « autres » de dirigeants | −0,8 % (−3,2) / −2,2 % (−5,3) | −3,0 % (−6,4) | −1,4 % (−2,6) | retenue par la règle, **fragile** |
| Communiqués de résultats (AMF) | −0,3 % (−4,1) / −1,0 % (−4,3) | −0,8 % (−2,9) | −1,2 % (−3,7) | retenue par la règle, **fragile** (témoins = périodes sans communiqué) |
| Communiqués d'information privilégiée | −0,1 % (−2,4) / −1,5 % (−5,3) | −1,4 % (−3,6) | −1,5 % (−4,1) | non retenue (5 séances) ; à suivre à 20 séances |
| Achats de dirigeants | +0,2 % (3,5) / +0,1 % (0,4) | −0,1 % | +0,3 % (1,2) | non retenue |
| Signaux Momentum Capital | −0,5 % (−3,2) / −0,7 % (−1,8) | −1,1 % (−2,4) | +0,2 % (205 témoins) | non retenue |
| Lettres d'achat Momentum | −0,6 % (−2,1) / −2,0 % (−3,1) | — | — | non retenue (244 témoins < 300) ; achat − vente −2,2 % (t −3,8) |
| Surprises de résultats, rachats d'actions, positions courtes, autres communiqués | — | — | — | non retenues (rachats : signe inversé selon le témoin) |

- **Contraste dans une même source** (indemne de la population) : achats − ventes de dirigeants **+1,9 % à 20 séances (t 7,3)**, +0,5 % à 5 (t 3,3) ; surprises positives − négatives +0,2 % à 5 séances (t 3,6), +0,1 % à 20 (t 1,3).
- **Lecture** : le seul résultat qui tient contre les deux témoins et dans le contraste est la **vente de dirigeant**, suivie d'environ 1,7 point de sous-performance sur 20 séances par rapport aux périodes suivantes de la même valeur — avant frais, sur une fenêtre de 20 séances : une information de prudence (fiche, portefeuille), pas un déclencheur. Les dirigeants vendent après une hausse (+4,3 % par rapport à la place dans la fenêtre « avant »).
- **Par contexte (descriptif, sous-groupes lus après coup)** — écart au témoin à 20 séances : ventes de dirigeants **+4,5 % (t 9,3) quand l'indice de la place est au-dessus de sa MM50**, +2,7 % (t 3,7) en dessous ; +4,8 % (t 9,2) avec un VIX < 20, +1,5 % (t 2,5) au-dessus. Déclarations « autres » : −3,3 % (t −6,3) avec un VIX < 20, +0,1 % au-dessus. Communiqués de résultats : −1,1 % (t −3,9) indice au-dessus de sa MM50, −0,8 % (t −2,0) en dessous. Les effets se concentrent en marché calme et haussier — là aussi où la hausse précédant l'événement (fenêtre témoin « avant ») est la plus forte : à confirmer contre le seul témoin « après » avant d'en faire un critère.
- **Biais corrigés en route** (16/11/2026) : clôtures brutes (dividende lu comme une perte, 0,1 point) ; médiane de l'univers actuel (survivants : tous les écarts tirés vers le bas) ; la médiane de toute la place tire au contraire les écarts vers le haut — d'où le témoin.

## 40.4 Job et commandes

- **Job `sources_daily`** : 22:40 Paris **tous les jours** (lettres, podcasts et forums arrivent aussi le week-end ; une source muette doit se voir le lundi) : journal des 3 derniers jours → mesure → santé → alerte P3 éventuelle.
- **Rattrapage de l'historique** (une fois, sur le PC) : `events-build --from 2016-01-01` puis `events-measure` (par lots de 20 000 événements, les plus anciens d'abord).
- **Contrôle** : `sources-health`, `events-scoreboard`.

## 40.5 S4 — Fréquences de recueil

| Source | Avant | Depuis le lot S4 | Job |
|---|---|---|---|
| Consensus des analystes (screener différé : nombre d'analystes, objectif moyen, note publiée) | une fois par semaine (`universe_refresh`, lundi) | **chaque soir** 22:20 Paris, lundi à vendredi, univers et positions | `consensus_daily` |
| Fondamentaux (Yahoo) | dimanche, toujours les 800 mêmes premières valeurs | **chaque nuit** 05:10 Paris, 800 valeurs par **rotation** : positions d'abord, puis jamais lues, puis les plus anciennes — tout l'univers en une semaine environ | `fundamentals_refresh` |
| Communiqués officiels AMF | à la main (`filings-archive`) | **chaque soir** 21:50 Paris, tous les jours, 3 derniers jours (`data.filings.daily_lookback_days`), idempotent | `filings_daily` |
| Actualités (RSS) | toutes les 15 min de 09:00 à 18:45 | toutes les 15 min de 09:00 à **21:45** (séance de New York) | `news_scan` |

- Sur le PC, `params-init --update` ajoute les nouveaux jobs et **déplace les deux horaires modifiés** (`fundamentals_refresh`, `news_scan.day`) seulement s'ils sont encore à leur ancienne valeur par défaut : un horaire choisi par l'utilisateur est gardé.
- Le registre de santé attend désormais un consensus et des fondamentaux au plus tard 3 jours ouvrés après le précédent.

## 40.6 S5 — Couverture des États-Unis (ADR-021)

| Source | Ce qui est lu | Où c'est stocké | Sens dans le journal |
|---|---|---|---|
| **SEC Form 4** (déclarations des dirigeants) | achats (code P) et ventes (code S) au marché d'un dépôt, additionnés ; attributions, exercices et retenues fiscales gardés comme « autre » ; vente ou achat **programmé** (plan 10b5-1) signalé | `insider_declarations`, source `sec_form4` — **rôle seulement** (dirigeant, administrateur, actionnaire de plus de 10 %, autre), jamais le nom | achat +1, vente −1 ; un échange programmé a son propre type (`dirigeant_cession_plan`) |
| **SEC 8-K** (événements importants) | les items publiés (résultats, accord important, acquisition, départ de dirigeant, dépréciation, changement de contrôle…), rangés dans les familles des communiqués AMF ; l'heure d'acceptation de la SEC | `official_filings`, diffuseur `SEC`, source `sec_8k` | sans sens (comme un communiqué) |
| **FINRA Reg SHO** (ventes à découvert du jour) | volume déclaré vendu à découvert, volume total, volume exempté | `short_volume_daily` | un pic (part à au moins `event_journal.short_volume_z` écarts de sa base de 20 jours) : −1 |
| **Reddit américain** | r/stocks, r/investing, r/StockMarket, r/ValueInvesting, r/wallstreetbets (`weak_signals.social.reddit.subreddits_us`) : comptage par valeur et par jour, nom ou « $TICKER » | `attention_daily`, source `reddit_us` | via le radar |

- **Job `us_filings`** : 06:20 Paris, tous les jours, sur les jours précédents (l'index SEC du jour J est complet dans la nuit ; le fichier FINRA paraît après la clôture américaine).
- **Seules les valeurs suivies** (univers et positions cotées aux États-Unis) : l'index quotidien dit lesquelles ont déposé, et seuls ces dépôts sont téléchargés ; d'un 8-K, seul l'en-tête (les annexes ne sont jamais lues).
- **Identité demandée par la SEC** : `data.sec.user_agent` (« Prénom Nom adresse »), réglé par l'utilisateur sur le PC ; tant qu'il vaut « à renseigner », la SEC n'est pas lue et la ligne du job le dit. FINRA ne demande rien.
- **Décision de l'utilisateur du 15/11/2026 (« oui sec »)** : l'identité déclarée est « Laurent » suivi de l'e-mail de `config/params.yaml`, écrite sur le PC par la tâche `2026-11-15-03` du relais (docs/41) ; l'adresse n'apparaît dans aucun fichier versionné.
- **Contrôle et rattrapage** : `us-check` (réglages, valeurs suivies, lecture d'essai d'un jour, rien n'est stocké), `us-load --from AAAA-MM-JJ` (chargement d'une période, idempotent).
- **Formats vérifiés sur de vrais fichiers le 15/11/2026** (`us-check --sample 3` sur le PC) : index quotidien SEC du 13/11 (7 471 dépôts, dont 1 382 Form 4 et 302 8-K), Form 4 (vente au marché, donations « G » rangées en « autre », rôle, heure d'acceptation), 8-K (items et famille), fichier FINRA (12 268 lignes).
- Les lignes SEC sont **exclues** des études et contrôles propres au flux AMF (couverture mensuelle, re-familiarisation, études des causes et plan 8).

## 40.7 Limites connues

- La médiane de la place est celle des valeurs **de l'univers** présentes aux deux dates ; une place peu couverte (moins de 10 valeurs) ne donne pas d'écart, et c'est dit.
- Le contexte est celui **connu le jour de l'événement** (régime de la veille au plus tard 5 jours, VIX de moins de 7 jours) : jamais reconstruit après coup (règle 10).
- Le t affiché est descriptif : beaucoup de lignes sont comparées à la fois, et un t isolé de 2 arrive par hasard. Le tableau ne sert pas encore à décider ; il dira, quand il sera assez nourri, quelles sources méritent l'étape 2.
