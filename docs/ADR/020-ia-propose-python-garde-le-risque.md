# ADR-020 — L'IA propose ses niveaux, Python garde la main sur le risque (règle 3 modifiée)

- **Statut** : **accepté le 15/11/2026 par l'utilisateur** (« oui tu peux modifier la règle 3 » ; « on reste sur budget max à 50 $, on optimise l'utilisation des différents modèles en fonction de leur coût, de leur valeur ajoutée et du budget »). Règle 3 de CLAUDE.md reformulée en conséquence.
- **Remplace en partie** : la formulation de la règle 3 issue de l'ADR-011 (« le LLM ne calcule pas »). Les autres points de l'ADR-011 (température, cache, garde-fou des nombres pour le classement) restent.
- **Mise en œuvre** : étape 2 du plan de l'utilisateur (analyse), après l'étape 1 (recueil des sources, lots S1 à S5). Cet ADR ne code rien.

## Contexte

- Jusqu'ici, le LLM ne faisait que classer, résumer et rédiger ; prix, stops, tailles et scores venaient du seul Python.
- L'utilisateur emploie depuis plusieurs mois un projet Claude qui propose lui-même entrée, stop, objectif et taille, et juge que cela « fonctionne plutôt bien ». Cette impression n'a pas été mesurée dans l'outil.
- Il demande une analyse multi-agent : l'IA calcule aussi ses niveaux, un contradicteur les attaque, un arbitre compare la proposition de l'IA et celle de Python pour retenir la meilleure.

## Décision

1. **Deux auteurs de niveaux.** Pour une même idée, Python calcule ses niveaux (entrée, stop, objectifs, taille) par les règles en vigueur ; un **analyste IA** propose les siens, avec son raisonnement, à partir des seuls faits stockés (cours, volumes, zones, résultats, actualités, lettres, conjoncture).
2. **Contradicteur et arbitre.** Un **contradicteur IA** attaque les deux propositions (stop dans le chemin d'un support, objectif hors de portée, risque caché, donnée périmée). Un **arbitre IA** retient l'une, l'autre ou une combinaison, et dit pourquoi.
3. **Garde-fous inchangés, appliqués par Python après l'arbitre** — aucun n'est levé par l'IA :
   - règle 1 : aucun ordre n'est passé ; la carte reste un ordre à saisir soi-même ;
   - règle 2 : chaque nombre proposé par l'IA passe un **contrôle de vraisemblance** contre les cours stockés (stop sous le prix d'entrée, objectif au-dessus, niveaux dans la bande `letters.level_price_band` du dernier cours, devise de cotation) ; un nombre invraisemblable fait écarter la proposition, jamais corriger en silence ;
   - règle 7 : **la quantité exécutable est toujours recalculée par Python** sur le niveau retenu, avec le prix maximal d'entrée et le stop glissé ; plafonds de risque, coupe-circuit, règle d'observation (espérance mesurée ≤ 0 → aucun ordre) et R14 s'appliquent comme aujourd'hui ;
   - règle 10 : l'instantané de décision garde les trois propositions (Python, IA, arbitre), les textes des agents et le modèle utilisé.
4. **La mesure tranche.** Chaque proposition des trois auteurs est suivie (lot S3) : résultat à 1, 5 et 20 séances, touches de stop et d'objectif, par place et par contexte. Un auteur ne devient le choix par défaut que sur mesure (nombre de cas, gain moyen, fiabilité), jamais sur impression.
5. **Ce qui reste de l'ADR-011** : sortie JSON validée par Pydantic (listes fermées), température 0 lorsque le modèle l'accepte, sinon réflexion désactivée ou bornée, cache par empreinte du prompt, journal `llm_calls`, plafonds de dépense.

## Budget et choix des modèles

- **Plafond inchangé : 50 $ par mois** (`llm.monthly_budget_usd`), 2 $ par jour (`llm.daily_budget_usd`). Au-delà : repli sur le seul calcul Python, dit sur la carte.
- Répartition selon le coût et la valeur ajoutée (tarifs publics relevés le 15/11/2026, par million de jetons en entrée / sortie) :

| Tâche | Modèle | Tarif | Volume visé |
|---|---|---|---|
| Classement de masse (nouvelles, lettres, podcasts, forums) | Claude Haiku 5.5 | 0,10 $ / 0,50 $ | tout ce qui arrive |
| Analyste et contradicteur | Claude Sonnet 5.5 | 2 $ / 10 $ | liste courte, de l'ordre de 10 valeurs par jour |
| Arbitre | Claude Fable 5.2 | 8 $ / 40 $ | les 3 meilleures idées du jour |

- Économies : traitement de nuit en lot (API Batch, −50 %), cache des parties fixes des prompts, prompts courts sur des faits déjà calculés.
- Ordre de grandeur à vérifier sur une semaine réelle : ≈ 20 à 25 $ par mois pour le multi-agent, le reste du budget couvrant les usages actuels. La liste courte et le nombre d'arbitrages s'ajustent pour tenir les 50 $.

## Conséquences

- L'outil compare enfin, sur les mêmes idées, le jugement de l'IA et les règles Python ; l'utilisateur voit les deux et le choix de l'arbitre.
- Le risque principal (une IA qui « invente » un niveau) est contenu par le contrôle de vraisemblance et par le recalcul Python de la quantité ; le risque d'un mauvais jugement est mesuré, pas présumé.
- Coût : suivi par `llm_calls` et la page des coûts ; aucun dépassement du plafond.

## Mise en œuvre (17/11/2026, docs/42)

- **Positions** (option B) : `position_ai`, revue du soir à deux voix, avis final contrôlé par Python, quantité par Python.
- **Idées du jour** (option A) : `idea_ai`, analyste, contradicteur et arbitre ; contrôle de vraisemblance, taille par Python sur les entrées stockées avec la proposition (`resize`), mesure par auteur (`measure_authors`). Les deux en **observation** : Python reste l'auteur par défaut tant que la mesure ne dit pas autre chose.
