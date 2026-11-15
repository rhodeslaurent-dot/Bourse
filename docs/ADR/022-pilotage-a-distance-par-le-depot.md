# ADR-022 — Pilotage à distance du PC par le dépôt (liste blanche, une tâche validée à la fois)

- **Statut** : **accepté** le 15/11/2026 (demande de l'utilisateur : tous les échanges, validations et lancements par l'interface cloud).
- **Contexte** : la session cloud ne joint pas le PC (dossier non servi aux sessions cloud par l'application Windows ; aucun message d'une session cloud vers une session du PC). La session locale du PC (Remote Control) peut, elle, lire GitHub et écrire à la session cloud.
- **Décision** : un relais par le dépôt. La session cloud écrit, **après validation de l'utilisateur dans le chat cloud**, une tâche dans `ops/pc_tasks.yaml`. La session locale, en boucle, tire la branche et lance `python -m app.remote_pilot`, qui exécute la tâche en arrière-plan **seulement si toutes ses commandes figurent dans une liste blanche fermée**, puis lui rend le compte rendu à transmettre (docs/41).
- **Raisons** : aucun port ouvert, aucun nouveau service, aucun secret transmis ; une liste blanche contrôlée par du code testé plutôt que la seule prudence d'un agent ; trace complète (tâche versionnée, compte rendu conservé sur le PC).
- **Risques et parades** :
  - quiconque peut pousser sur la branche peut proposer une tâche → seules des commandes de la liste blanche s'exécutent ; **passer le dépôt en privé** et n'y donner l'écriture à personne d'autre reste la vraie protection ;
  - la boucle tire du code puis l'exécute (remarque de la session locale, 15/11/2026) → si un `git pull` modifie l'exécutant (`app/remote_pilot.py`, qui porte la liste blanche), la boucle s'arrête et demande l'accord de l'utilisateur avant de l'exécuter ;
  - une sortie peut contenir une donnée sensible → `.env` n'est jamais lu, les lignes ressemblant à un secret sont masquées, les comptes rendus ne sont pas versionnés ;
  - une valeur personnelle (adresse) ne figure jamais dans le dépôt → la tâche écrit `{params:…}`, remplacé sur le PC seulement.
- **Conséquences** : un délai de lancement de 10 minutes au plus ; le PC doit rester allumé avec la boucle active. À abandonner au profit de l'exécution directe dès que le partage de dossier vers le cloud fonctionne.
