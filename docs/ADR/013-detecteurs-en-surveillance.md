# ADR-013 — Détecteurs d2, d3, d4 et d7 en surveillance seulement

- **Statut** : **accepté le 26/10/2026** (décision de l'utilisateur : option B).
- **Contexte** : backtest n° 2 (docs/27 §27.8) sur 2018-10 → 2026-10, au coût complet. En production, l'espérance est **négative** :
  - d7 −0,137 R (n 2 716, t −2,9) ; d4 −0,382 R ; d3 −0,342 R ; d2 −0,246 R (n 17) ;
  - sur la période de contrôle, l'intervalle de confiance de d4 et de d7 est entièrement négatif.

  Le diagnostic (`backtest-report`) montre un signal à peine positif avant coûts (d7 +0,04 R), perdu par les coûts et par les sorties : stops serrés, objectifs lointains, gains rendus.

## Décision

1. `scoring.watch_only_detectors: [d2_breakout, d3_range_expansion, d4_pullback, d7_trend_quality]`. Une configuration de ces détecteurs est **évaluée et affichée**, mais toujours en **WATCH**, avec la mention « espérance historique négative au coût complet (backtest n° 2) — surveillance seulement (ADR-013) » et son explication en clair.
2. La mention est posée **avant** toute autre porte. Aucune issue (limite en attente d'un repli, absence d'objectif…) ne peut rendre une telle proposition actionnable : elle n'entre jamais au carnet d'ordres.
3. Ne sont pas concernés : d8 (buzz précoce), les configurations issues des lettres, et les positions déjà ouvertes (stops et suivi inchangés).
4. Les propositions continuent d'être enregistrées, et leurs résultats mesurés (`outcomes_update`, `/apprentissage`) : la mesure en direct se poursuit.

## Conséquences et sortie

- Moins de propositions actionnables tant que la recherche (docs/31) n'a pas livré de remplacement. C'est un choix assumé : ne pas engager de capital sur des règles démontrées perdantes.
- La décision est revue au **tour 2 de la recherche**. Chaque détecteur est soit gardé avec de nouvelles sorties (variante à espérance positive confirmée sur la période scellée), soit remplacé par un déclencheur candidat, soit supprimé, par un nouvel ADR.
- Annulation : retirer un détecteur de la liste (`params-init --update --set` ou édition de `config/params.yaml`).
