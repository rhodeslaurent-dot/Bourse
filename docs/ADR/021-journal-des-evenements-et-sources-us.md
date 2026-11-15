# ADR-021 — Un journal unique des événements, mesuré contre la place ; sources américaines officielles

- **Statut** : **accepté** le 15/11/2026 (demande de l'utilisateur : « lance S1 à S5 en autonomie » ; étape 1 de son plan, docs/40).
- **Contexte** : chaque source (lettres, podcasts, dirigeants, analystes, communiqués, nouvelles, forums, positions courtes, signaux) avait sa table et, pour certaines, son étude ponctuelle. Rien ne disait chaque jour si une source s'était tue, ni ce que les valeurs avaient fait après ses messages, ni dans quel contexte de marché. Les États-Unis n'avaient ni dirigeants, ni communiqués, ni ventes à découvert.

## Décisions

1. **Santé des sources** : un registre en code (`app/services/source_health.py`) décrit chaque source (table, colonne de date, cadence, couverture, job) ; un instantané par jour et par source est **conservé** (`source_health`). Une alerte P3 au premier soir de silence seulement.
2. **Un journal unique** (`market_events`) plutôt qu'une étude par source : même forme pour tous (date de publication, type, sens de la source, force, référence à la ligne d'origine, contexte du jour). Le sens est **celui de la source**, jamais une opinion de l'outil. Idempotent par (`ref_table`, `ref_id`, `kind`, `isin`).
3. **Mesure contre la place, pas contre un indice** : la référence est la **première clôture de la place de la valeur après publication** ; l'écart est pris à la **médiane des autres valeurs de la même place** (et du même secteur sur la place) sur les mêmes séances. Raison : l'outil couvre 16 places dont plusieurs sans indice chargé, et la médiane résiste aux valeurs extrêmes. Une fenêtre traversée par une opération sur titre est **non mesurable**, jamais corrigée.
4. **Descriptif seulement** : aucun seuil du tableau ne décide une proposition (règle d'observation P1 et ADR-020 : la mesure désignera plus tard ce qui mérite l'étape 2).
5. **Sources américaines officielles et gratuites** : SEC EDGAR (index quotidien, en-tête des dépôts, XML du Form 4) et FINRA (fichier quotidien Reg SHO). Stockées dans les tables existantes (`insider_declarations` source `sec_form4`, `official_filings` diffuseur `SEC`) pour que fiches et journal les lisent sans code nouveau ; **exclues explicitement** des études et contrôles propres au flux AMF (couverture mensuelle, re-familiarisation, études des causes et plan 8).
6. **Vie privée et accès loyal** : la SEC exige un User-Agent nommant le demandeur. Il est **réglé par l'utilisateur sur son PC** (`data.sec.user_agent`, `params.yaml` non versionné) ; tant qu'il vaut « à renseigner », la SEC n'est pas lue. Débit ≤ 9 requêtes par seconde imposé par le code ; seuls les dépôts des valeurs suivies sont téléchargés ; d'un 8-K, seul l'en-tête. Le **nom du déclarant** d'un Form 4 n'est jamais lu ni stocké (rôle seulement, liste fermée).
7. **Reddit américain sous une source distincte** (`reddit_us`) : un forum très actif lu sur ses dernières heures ne doit pas empêcher les vrais zéros des forums européens ; une valeur américaine est reconnue par son nom ou par son « $TICKER », jamais par un ticker nu.

## Conséquences

- Coût : 4 tables (migration 0034), 5 jobs (`sources_daily`, `consensus_daily`, `filings_daily`, `us_filings`, `fundamentals_refresh` quotidien) ; aucune dépense d'IA.
- Limite : les formats SEC et FINRA n'ont pas pu être lus depuis l'environnement de développement (réseau fermé) ; ils suivent la documentation officielle et sont vérifiés sur le PC par `us-check` avant le premier chargement.
- Le tableau de mesure compare beaucoup de lignes : un t isolé de 2 arrive par hasard ; il ne sert pas encore à décider.
