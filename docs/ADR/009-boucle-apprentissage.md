# ADR-009 — Boucle d'apprentissage sans ré-entraînement ni optimisation automatique

Statut : **accepté** · Date : 2026-10-21 · Spécification : docs/18

## Contexte

L'utilisateur souhaite que l'IA « s'entraîne » sur les propositions passées (prises ou non) pour donner des conseils de plus en plus efficaces, sur une page dédiée, et qu'elle puisse lui poser des questions et proposer des améliorations. Contraintes : le LLM ne calcule pas (règle 3), aucune donnée inventée (règle 2), instantané de décision (règle 10), paramètres datés dans `params.yaml` (règles 5 et 8), aucune optimisation automatique (docs/12 §12.5). Les échantillons resteront petits (quelques dizaines de cas par mois).

## Décision

1. **Pas de ré-entraînement du modèle** : l'API ne le permet pas pour cet usage et l'échantillon conduirait au sur-apprentissage. « Apprendre » = mesurer, ajuster de façon bornée, mémoriser.
2. **Mesure en Python** (`domain/outcomes.py`) : chaque proposition reçoit un résultat simulé conservateur (entrée limite, stop glissé d'abord, horizon 20 séances, clôtures officielles postérieures à la création, séance manquante → indisponible).
3. **Ajustement borné** (`domain/learning.py`) : historique comparable antérieur à la création, espérance rétrécie `n/(n+k)`, verdict par seuils de `learning.*` ; effets limités à l'affichage, au tri et à la règle docs/12 §12.5.3 (BUY → WATCH sur ≥ 60 cas défavorables, désactivable). Tailles, stops et règles de risque inchangés. L'historique vu est figé dans l'instantané.
4. **Revue LLM encadrée** : dossier d'agrégats calculés ; chaque nombre écrit par le LLM doit figurer dans le dossier ; une suggestion de paramètre doit viser une clé existante, une borne calculée, et jamais relever un plafond de risque. Les éléments non conformes sont écartés et listés.
5. **Mémoire** (`trader_memory`) : réponses de l'utilisateur, suggestions acceptées et leçons libres, injectées dans la revue suivante et l'assistant. Une suggestion de paramètre acceptée produit un extrait YAML daté et un brouillon d'ADR ; `params.yaml` n'est jamais modifié par l'application.

## Conséquences

- Les gains sont lents et explicables ; l'utilisateur garde la décision de chaque changement de règle.
- Tant que les échantillons sont « insuffisants », la page affiche surtout des pistes ; c'est voulu.
- Le résultat théorique n'inclut les coûts que lorsqu'une estimation est stockée sur la proposition (sinon « coûts non comptés »).
