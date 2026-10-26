# 28 — Niveau de risque du jour et unité de risque réglable

> Décisions de l'utilisateur du 25/10/2026 : (1) **le gain d'abord, la maîtrise du risque ensuite**, sans que l'une écrase l'autre ni crée d'immobilisme ; la réussite passée vient de la **dilution** (5 à 7 lignes simultanées, 1 ou 2 pertes compensées par 4 ou 5 gains) ; (2) chaque jour, après l'analyse du portefeuille, du micro, du macro et des opportunités, l'outil **préconise le réglage du risque**, appliqué par défaut, modifiable dans l'interface, avec une **justification claire et simple** ; (3) l'unité de risque (300 €) est réglable dans l'interface.

## 28.1 Unité de risque

`R = capital pilote × risque par trade` (40 000 € × 0,75 % = 300 € par défaut, `params.yaml`). Réglable sur `/risque` dans les bornes `risk.risk_dial.reference_pct_min/max` (0,25 % à 1,5 %) ; chaque changement est daté et motivé (table `risk_settings`) et prime sur `params.yaml`, qui garde la valeur par défaut et les bornes (règle 8 : divergence signalée en docs/15).

## 28.2 Niveau de risque du jour

Multiplicateur de R, calculé par Python à chaque passage du moteur (07:50), **après** la détection sur tout l'univers et **avant** le dimensionnement :

| Composante | Facteur (`risk.risk_dial.*`) |
|---|---|
| Marché | rouge × 0,5 ; risque conjoncturel élevé × 0,7 ; orange × 0,85 ; vert et calme × 1,1 ; sinon × 1 |
| Résultats récents | réduction du coupe-circuit (semaine de reprise, recul depuis le plus haut, mesure manquante : × 0,75) |
| Portefeuille | moins de 5 lignes et au moins 3 opportunités solides (ratio technique ≥ 2,5) × 1,1 ; 7 lignes ou plus × 0,9 |
| Plafonds | jamais sous × 0,5 ; au plus × 1,33, et × 1 tant qu'aucun signal n'a d'espérance **mesurée** positive |

Par ligne : **conviction** × 1,15 si l'espérance mesurée du signal ≥ 0,15 R (jamais de pénalité pour un signal non mesuré) ; réductions propres à la ligne (écart d'ouverture, résultats avant l'horizon × 0,75, SRD restreint) plancher × 0,5 ; **plafond absolu** `risk.risk_dial.max_trade_risk_pct` (1 % du capital).

`taille = floor((capital × risque de référence × niveau × facteurs de ligne − frais) / R_par_action)` — la formule de la règle 7 (prix maximal, stop glissé, marge de change) est inchangée.

**Mise à jour du 26/10/2026 (docs/32).** La composante « marché » du niveau du jour lit le **régime européen** (STOXX 600 et tout l'univers), et non plus le CAC 40. Le régime de la place de chaque valeur s'applique ligne par ligne, par le portail (docs/07 §7.9.4).

## 28.3 Ce qui a changé par rapport au 25/10 matin

- Les réductions « marché » (orange, portail restreint) ne s'appliquent plus ligne par ligne : elles sont dans le niveau du jour ; un **portail restreint par absence d'avis récent des lettres** ne réduit plus la taille (la sélection exigeante R10 reste).
- Le cumul des réductions ne transforme plus un achat en WATCH (plancher × 0,5).
- Seuils recalibrés pour un portefeuille dilué : coupe-circuit −4 R semaine / −8 R mois, glissants −4 R (5 séances) / −8 R (21), recul depuis le plus haut −10 R → × 0,75, −12 R → arrêt ; risque ouvert 5 % quel que soit le portail ; grappe corrélée ≤ 2 R.

## 28.4 Interface et traçabilité

- `/risque` : niveau appliqué et conseillé, **explication en une ligne par composante**, risque par ligne en €, nombre de lignes possibles, changement du niveau du jour (avec recalcul des propositions sans décision), unité de risque, historique 30 jours.
- Accueil : bandeau « Niveau de risque du jour » ; Telegram : une ligne par proposition ; rapport du jour (rubrique « Risques globaux ») : l'explication.
- Chaque proposition garde dans son instantané le niveau appliqué, conseillé, modifié ou non, et l'unité de risque (règle 10).
- `/apprentissage` : **coût des garde-fous** — propositions arrêtées par chaque règle et leur résultat simulé ; ⚠ si une règle arrête surtout des gagnants.

## 28.5 Critères d'acceptation

- **A28.1** Le niveau reste dans [× 0,5 ; × 1,33] et à × 1 au plus sans espérance mesurée (tests).
- **A28.2** Un changement manuel s'applique aux propositions recalculées du jour ; l'unité de risque réglée est utilisée par le moteur (tests).
- **A28.3** Le cumul des réductions de ligne ne descend pas sous × 0,5 et ne crée pas de WATCH (test).
- **A28.4** L'explication du niveau est affichée sur `/risque`, l'accueil, le rapport et Telegram.
