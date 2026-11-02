# ADR-018 — Mesurer la partie active contre le même argent placé dans le cœur

- **Statut** : **proposé** le 02/11/2026 (lot M1 de docs/39). La mesure est affichée dès maintenant : elle ne change aucune proposition, aucune taille, aucun verdict. Son **usage** (élargir ou réduire la partie active) reste à décider par l'utilisateur.
- **Contexte** : la recherche du 02/11/2026 (docs/39) conclut que la façon la mieux prouvée de maximiser ses gains, pour un particulier, est un cœur indiciel mondial acheté chaque mois. La partie active (les propositions de l'outil) ne se justifie que si elle fait mieux que ce cœur, frais et impôt compris. Les mesures internes vont dans le même sens : aucun déclencheur retenu sur 195 cellules (docs/33), d7 −0,137 R net (docs/27), lettres −2,8 % contre le marché à 20 séances (n 99, docs/15). Aucun outil ne comparait jusqu'ici la partie active au cœur.
- **Décision proposée** :
  1. **Méthode** : équivalent marché public de Kaplan & Schoar (2005), calculé en Python pur (`app/domain/allocation.py`, mypy strict) :
     - chaque achat et chaque vente de la partie active est rejoué dans le cœur, à sa clôture du jour (ou de la séance précédente) ;
     - le rapport « partie active / cœur » est supérieur à 1 quand la partie active fait mieux.
  2. **Partie active** : toutes les exécutions (la ligne confirmée quand une déclaration a été rapprochée), sauf la poche cœur (`pockets.dca.isins`, R17) et la poche fiscale (`pockets.tax_harvest`, R18).
  3. **Cœur** : l'ETF de `pockets.dca.isins` s'il a des cours stockés. Sinon, la série macro `allocation.proxy_series` (URTH en USD converti en EUR), annoncée « proxy déclaré, hors dividendes ».
  4. **Coûts et impôt** : coût d'achat du cœur `allocation.core_cost_pct` (0,5 %, plafond PEA, prudent) ; PFU en vigueur (`tax.pfu_total_rate`) sur les plus-values réalisées au CTO, au prix de revient moyen pondéré, pertes reportées 10 ans ; rien au PEA avant retrait.
  5. **Jamais d'estimation** : un cours manquant (cœur ou ligne) donne « indisponible » avec sa raison (règle 2).
  6. **Affichage** : une ligne « Cap » dans la Revue du jour ; le détail par `core-compare --to AAAA-MM-JJ` (lecture seule). Aucun montant n'est écrit dans un fichier versionné.
- **Limites connues** :
  - les dividendes ne sont comptés d'aucun côté ; un ETF cœur capitalisant ferait mieux que la série proxy ;
  - l'impôt est compté à la date de fin, sans placement entre-temps ;
  - le SRD est traité comme le comptant (date d'imposition à la liquidation non modélisée) ;
  - la mesure n'a de sens statistique qu'après de nombreux mois : l'écart de suivi est grand (docs/39 §3).
- **Conséquences** :
  - élargir la partie active sur la foi de cette mesure exigerait un ADR ultérieur, avec les seuils du banc (ADR-012), jamais un seuil propre ;
  - groupe `allocation` absent ou `enabled: false` → aucune ligne, fonctionnalité signalée sur /sante.
