# 21 — Revue de presse des lettres : contexte macro et micro pour Claude

Statut : spécification **proposée le 22/10/2026**, à valider (demande utilisateur : « dans la newsletter Momentum il y a en fin de mail plusieurs liens avec une revue de presse ; aller les consulter et en récupérer les infos macro et micro pour alimenter Claude contextuellement pour ses propositions »). Complète docs/19 §19.6 (page liée unique), docs/20 (revue des lettres) et docs/06 D6 (nouvelles). Règles absolues de `CLAUDE.md` intégralement applicables : le LLM classe et résume, il ne calcule rien ; aucune donnée inventée ; un article inaccessible reste « indisponible ».

## 21.1 Objectif

Les lettres Momentum (Capital, Prisma Media) se terminent par une **revue de presse** : plusieurs liens vers des articles de presse économique. Ces articles apportent :

- un **contexte macro** : taux, inflation, banques centrales, devises, matières premières, géopolitique, secteurs ;
- des **informations micro** : résultats, contrats, avis d'analystes sur des valeurs citées.

L'outil les lit **à la réception de la lettre**, avant la revue par Claude (docs/20 §20.1), pour que l'avis du jour tienne compte de ce contexte.

## 21.2 Repérage des liens

Par source, `data.imap.press_review.<source>` décrit le bloc :

- `start_after` : libellés qui ouvrent le bloc, par exemple « Revue de presse » (à confirmer sur une lettre réelle avec `newsletter-link-check --all-links`). Seuls les liens **situés après** ce libellé dans le HTML sont retenus.
- `exclude_texts` : liens de service écartés (désinscription, préférences, mentions légales, réseaux sociaux, « Lire la suite » déjà traité par §19.6).
- `max_links` : nombre maximal d'articles lus par lettre (défaut 10). Les liens en double (même URL finale) ne sont lus qu'une fois.

## 21.3 Sécurité et sobriété

Les règles sont celles de docs/19 §19.6, appliquées à chaque lien :

- **Domaines** : liste `allowed_domains` de la presse autorisée (https obligatoire). Les redirections de suivi de clics (`tracking_domains`, http toléré) n'envoient jamais de cookie. Une adresse IP ou un domaine inconnu est **refusé** et l'hôte est signalé pour décision.
- **Volume** : une page par lien, `min_interval_s` (3 s) entre deux pages, sans navigation au-delà, sans JavaScript.
- **Usage personnel** : le texte est conservé en base privée (`news_items.body`) pour l'analyse. L'interface n'affiche que le titre, le résumé et le lien vers l'article ; le texte complet n'est jamais affiché ni retransmis.

## 21.4 Contenu retenu

- **Titre** : `og:title`, sinon `<title>`.
- **Date de publication** : `article:published_time`, sinon `<time datetime>`. À défaut, c'est l'heure de réception de la lettre, avec la mention `date_source: lettre`. Une date absente n'est jamais estimée autrement.
- **Texte principal** : même extraction que §19.6 (article, sinon main, sinon corps), 20 000 caractères au plus.
- **Article réservé aux abonnés** : statut `reserve`. Seuls le titre et le chapô (`og:description`) sont retenus, rien d'autre n'est déduit.

## 21.5 Enregistrement et classement

- **Stockage** : une ligne `news_items` par article.
  - Source `presse` ; l'éditeur (domaine) est dans `classification.editeur`.
  - `guid` = URL finale ; déduplication par URL, donc un article cité par plusieurs lettres n'est stocké qu'une fois.
  - `published_at` selon §21.4.
- **Lien avec la lettre** : `newsletter_items.parsed.revue_presse` = liste de {url, statut, news_id, titre}.
- **Valeurs (micro)** : rattachement par les alias du référentiel, comme les flux RSS (`attach_instruments`).
- **Classement** (LLM, prompt `news_classify` v2, température 0) : schéma `NewsClassification` existant, plus deux champs :
  - `portee` ∈ {macro, secteur, valeur} ;
  - `themes` : liste fermée (taux, inflation, banques_centrales, croissance, emploi, devises, matieres_premieres, energie, geopolitique, fiscalite_reglementation, secteur, resultats, m_and_a, autre).
- **Repli sans IA** : classement par règles avec confiance réduite et `portee` = « valeur » si une valeur est rattachée, sinon « macro ».

## 21.6 Usage par Claude et par l'outil

- **Revue des lettres** (docs/20 §20.2) : nouvelle rubrique **« Contexte macro (revue de presse) »**. Elle reprend les articles `portee` macro ou secteur des `press_review.macro_days` derniers jours (3), au plus `max_macro_items` (10), avec titre, éditeur, date, thèmes et résumé. Les articles rattachés à une valeur entrent déjà dans les « nouvelles des 7 derniers jours » de cette valeur.
- **Garde-fou des chiffres** : les chiffres des résumés d'articles font partie du dossier, Claude peut donc les citer. Aucun autre chiffre n'est accepté (§20.4 inchangé).
- **Assistant** (docs/17 §17.4) et **thèse d'investissement** (docs/22) : même rubrique « contexte macro ».
- **Pré-ouverture** : un article micro classé fort ou moyen, non neutre, alimente `premarket_watch` comme une nouvelle RSS (docs/06 D6).
- **Interface** :
  - `/lettres` : sous chaque lettre, « revue de presse : N articles (k lus, m réservés, r refusés) » ; nouvelle section « Contexte macro » (titre, éditeur, date, thèmes, résumé, lien) ;
  - fiche valeur : les articles apparaissent dans les nouvelles.

## 21.7 Moment du traitement et coût

- **Moment** : dans `imap_poll`, juste après l'enregistrement de la lettre et de sa page liée, **avant** la revue à la réception.
- **Échecs** : un article en échec n'empêche ni la lettre ni la revue.
- **Durée** : une dizaine de liens à 3 s d'intervalle prennent environ 30 à 60 s par lettre.
- **Historique** : pas de lecture rétroactive pour les lettres déjà importées (docs/19), car les liens anciens sont souvent expirés ou payants. Une option `--press` de `newsletters-history` reste possible plus tard.
- **Coût** : une classification par article avec le modèle de classement (Haiku), quelques dixièmes de centime l'unité, dans le plafond `llm.*`. Plafond atteint : classement par règles.

## 21.8 Paramètres (`config/params.yaml`)

```yaml
data:
  imap:
    press_review:
      momentum_capital:
        start_after: ["Revue de presse"]          # à confirmer sur une lettre réelle
        exclude_texts: ["désinscri", "desinscri", "préférences", "mentions légales", "Lire la suite"]
        allowed_domains: []                        # à remplir après newsletter-link-check --all-links
        tracking_domains: []
        max_links: 10
        min_interval_s: 3
        timeout_s: 15
letters_review:
  macro_days: 3
  max_macro_items: 10
```

Bloc absent : revue de presse désactivée, réception inchangée.

## 21.9 Critères d'acceptation

- A21.1 Seuls les liens situés après le libellé d'ouverture et vers un domaine autorisé sont lus ; un domaine inconnu ou une IP est refusé sans requête vers la destination (tests sur lettre figée).
- A21.2 Un article lu crée une ligne `news_items` (source `presse`, éditeur, date de publication ou `date_source: lettre`), dédupliquée par URL ; un article réservé ne garde que titre et chapô.
- A21.3 Le dossier de la revue des lettres contient la rubrique « Contexte macro » ; un chiffre d'un résumé d'article est accepté, un chiffre absent est écarté.
- A21.4 `/lettres` affiche le décompte par lettre et la section « Contexte macro ».
