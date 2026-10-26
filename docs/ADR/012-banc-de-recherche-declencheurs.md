# ADR-012 — Banc de recherche de déclencheurs pré-enregistré

- **Statut** : **proposé le 26/10/2026** (développé en autonomie à la demande de l'utilisateur ; à valider). Ne change aucun paramètre ni détecteur.
- **Contexte** : backtest n° 2 (docs/27) — aucun détecteur de production n'a d'espérance positive au coût complet ; demande du 26/10/2026 d'isoler, par simulation, des déclencheurs à résultat positif par trade. Spécification : **docs/31**.

## Décision

1. La recherche se fait par **enregistrements pré-déclarés** : un fichier `research/prereg/<date>_regN.yaml` fige cellules, seuils A1–A13, hypothèses chiffrées et graine avant toute exécution. Enregistrement n° 1 : `research/prereg/2026-10-27_reg1.yaml`, sha256 `be41078af3575c3c9cfe7d005c11c727bd30f24dd6421a0cef883d66af666113`, **195 cellules** (8 confirmatoires, 118 exploratoires, 32 sorties d'étape 1 au plus, 27 diagnostics, 10 rejeux de portefeuille au plus ; plafond 400).
2. Le banc (`app/domain/research`, pur et testé ; `app/services/research.py` ; commande `research`) tourne **sur le PC** et écrit un **rapport agrégé** sans cours ni nom de valeur (`data/research/`), plus une ligne du registre `data/research/ledger.csv` (empreinte du plan, empreinte de l'ensemble de cellules, cellules exécutées, candidats). Un même plan exécuté avec un autre ensemble de cellules est refusé.
3. La validité du moteur est vérifiée avant usage : équivalence exacte avec `simulate_trade` (tests) et **rejeu du run 2** (`research calibrate`, VALIDE si ≥ 99 % de trades identiques et R brut moyen à ±0,02 R par détecteur) ; le verdict figure dans l'en-tête de chaque rapport.
4. Décision mécanique : CANDIDAT (A1–A13) / SURVEILLANCE / REJET ; au plus 5 candidats figés pour **un seul** descellement de la période scellée (`research unseal`). Un candidat confirmé n'entre en production que par un **nouvel ADR**, à 50 % de `risk.max_risk_per_trade_pct`, après suivi en ombre. Un détecteur existant dont l'intervalle hors échantillon est entièrement négatif appelle un ADR de désactivation ou de refonte.
5. Les hypothèses propres à la recherche (taux de faillite, saut d'OPA, capacité, table de change de repli, seuils de dividendes) vivent **dans le plan**, marquées `assumption: true` ; toutes les valeurs métier sont lues dans `config/params.yaml` (règle 8). Aucun paramètre ajouté à `params.yaml`.

## Conséquences

- « Aucun déclencheur retenu » est un résultat valide ; aucun seuil n'est relâché après coup ; la période scellée n'est pas réutilisée.
- Biais connus affichés : survie (table d'instruments d'aujourd'hui), capitalisations et TTF d'aujourd'hui, période scellée « semi-propre », rang de force relative recalculé.
- Écarts au plan arbitré : docs/31 §31.8 (notamment 195 cellules au lieu des 203 annoncées par une addition erronée).
- Enregistrement n° 2 envisagé : attention Wikipédia (après un job d'historique des consultations), momentum résiduel, saisonnalité.
