# ADR-027 — Coupe-circuit : mesure sur le résultat, pas sur l'équité ; rebasage sur décision

- **Statut** : point 1 accepté le 18/11/2026 (correctif) ; point 2 **en attente de la décision de l'utilisateur**.
- **Contexte** : toutes les propositions de la semaine portaient « BLOQUÉ : coupe-circuit — mesure indisponible ». Le diagnostic du 18/11 (`gates-coverage --circuit`, tâche PC 18-14) montre deux causes :
  1. **Les 15 instantanés consolidés depuis le 22/10 sont tous incomplets**, pour une seule raison : les liquidités du PEA n'ont jamais été déclarées. Le PEA est déclaratif (BoursoBank, sans API). Or la mesure du coupe-circuit porte sur le résultat (réalisé + latent), qui ne dépend pas des liquidités. Depuis la décision du 04/11 (une mesure manquante bloque toute entrée), plus aucune entrée n'était possible.
  2. **La fenêtre glissante voyait −16,4 R en 5 séances.** Jusqu'au 18/11, une ligne Saxo était rattachée à l'ISIN d'une autre société et valorisée à ses cours ; une fois rattachée à la bonne (correction du 18/11), son latent a « chuté » d'un coup. C'est un changement de valorisation, pas une perte de marché ; il reste dans les instantanés d'avant le 18/11.

## Décision

1. **Complétude du résultat distincte de celle de l'équité** (migration 0039, `kpi_daily.pnl_incomplete`). Un instantané au résultat incomplet est un instantané où une ligne n'a ni cours ni taux de change. Des liquidités inconnues laissent l'équité partielle, mais le résultat entier. Le coupe-circuit ne lit que le résultat. Un instantané écrit avant la migration n'a pas cette information : il est lu par son ancien indicateur, jamais supposé complet. La règle du 04/11 reste en vigueur : une mesure manquante bloque toute entrée.
2. **Rebasage optionnel, sur décision de l'utilisateur seulement** : `risk.circuit_breaker.baseline_from`. Les instantanés d'avant ce jour ne sont pas lus (ni pour la semaine, ni pour le mois, ni pour les fenêtres glissantes). Une période commencée avant est mesurée depuis le premier instantané complet de ce jour ; la carte le dit (« mesure rebasée au … »). Valeur par défaut : `null`, tous les instantanés sont lus.

## Conséquences

- **Sans rebasage** :
  - semaine : la mesure revient au premier instantané complet d'avant une semaine, donc celui du vendredi 20/11 pour la semaine du 23/11 ;
  - mois : elle revient en décembre, avec l'instantané du 30/11 ;
  - fenêtre glissante : −16 R tant que les instantanés d'avant le 18/11 y figurent, soit jusqu'au 25/11 environ.
  - En attendant, toute entrée reste bloquée.
- **Avec un rebasage au 18/11** : la mesure reprend dès le nouvel instantané du 18/11 (relu après la migration). Les pertes réelles éventuelles entre le 01/11 et le 18/11 ne sont plus comptées dans le mois. `gates-coverage --circuit` montre le résultat jour par jour, en R, pour juger de ce qui serait oublié.
- **Recommandation** : déclarer les liquidités du PEA, pour que l'équité soit complète.
- `gates-coverage --circuit` : instantanés de référence, causes (liquidités inconnues ou ligne sans cours), résultat en R jour par jour, rebasage. Aucun montant affiché.
- Tests : `tests/integration/test_circuit.py`, `tests/integration/test_performance_page.py`.
