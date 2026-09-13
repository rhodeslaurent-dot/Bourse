# 12 — Journal, retour d'expérience, post-mortem des signaux (priorité 4)

Objectif : savoir **ce qui marche** (détecteur, source, heure, place, compte) et **ce qui coûte** (retards, dérogations, comportements), pour ajuster les règles par décision explicite (ADR) et non par intuition.

## 12.1 Journal des trades

- Chaque position fermée devient une entrée de journal : détecteur, sources externes, score et sous-scores à l'entrée, régime, heure d'entrée (Paris), jours détenus, R initial, **R réalisé**, motif de sortie, slippage entrée/sortie, coûts, compte, mode, notes libres, tags (`plan_respecté`, `entrée_tardive`, `stop_trop_serré`, `sortie_anticipée`, `discrétionnaire`…), captures d'écran (upload).
- **MAE / MFE** (excursion adverse / favorable maximale, en R) calculés à partir des barres 1 min/5 min de la période ; efficacité d'entrée et de sortie.
- Trades **discrétionnaires** (sans proposition) sont acceptés mais tagués ; leur statistique est isolée.

## 12.2 Post-mortem des signaux (pris ou non)

Tous les `signals` et `proposals` sont historisés ; à J+1, J+5, J+20 l'outil calcule pour chacun : rendement depuis le niveau d'entrée proposé, stop touché (oui/non, quand), objectif 1/2 atteint (oui/non, quand), R théorique. On obtient :

- **Expectancy théorique par détecteur** (tous signaux) vs **expectancy réalisée** (signaux pris) → mesure du biais de sélection de l'utilisateur.
- **Valeur de chaque source externe** : expectancy des signaux issus de chaque newsletter, taux de recouvrement avec les signaux propres, et la **précocité** : `drift_since_open` moyen entre l'ouverture et l'heure de réception (à ne pas confondre avec `gap_open`, mesuré sur la clôture précédente), et écart entre le prix d'entrée obtenu grâce au scan propre et le prix au moment de la lettre (`gain_de_précocité`, en % et en €).
- **Comparaison de trois démarches sur la même période** (le vrai juge de paix, tenu dès la phase 1 pour l'enregistrement, exploité en phase 5) : (a) **la démarche actuelle**, définie précisément et non « les lettres telles quelles » : les valeurs citées par Zonebourse et Momentum, **filtrées par la grille /100 et les règles de risque du projet actuel** (BUY ≥ 75, 0,75 %/trade, 5–15 positions), entrées au cours à l'heure de réception + délai humain, stops et objectifs de la lettre ou de la grille — reconstituée par l'outil chaque jour à partir des signaux D5 et enregistrée comme portefeuille virtuel « référence », (b) détection autonome (D1–D4 sans les lettres), (c) combinaison. Pour chacune : résultat net des coûts, drawdown, risque mobilisé (R engagés), nombre d'occasions **réellement exécutables** dans les créneaux et le mode de disponibilité, temps consacré. Comparer uniquement les valeurs ensuite citées par les lettres favoriserait rétrospectivement l'outil : **tous** les signaux comptent, y compris ceux jamais cités, ceux dont le niveau d'entrée n'a jamais été atteint, ceux survenus pendant une indisponibilité, ceux qui ont expiré ou échoué.
- **Rejeu et hypothèses** : le rejeu applique les coûts (courtage, CRD, TTF), un délai humain (`review.human_delay_minutes`, 5 min) entre l'alerte et l'ordre, et la règle conservatrice **« stop d'abord »** quand une même barre touche le stop et l'objectif sans chronologie connue. Les résultats sont calculés sur l'instantané de décision (`decision_snapshots`), jamais sur des données corrigées après coup.
- **Horizon de validation** : deux semaines de fonctionnement réel valident la technique (alertes reçues, données cohérentes, aucune erreur de dimensionnement) ; le jugement sur l'apport de la démarche demande **≥ 3 mois et ≥ 60 signaux par détecteur**, avec l'intervalle de confiance affiché.
- **Analyse par heure de scan** (09:05 / 09:15 / 09:30 / 10:00 / EOD) : quel créneau produit les meilleurs signaux ; heatmap heure × jour de semaine.
- **Par place / secteur / capitalisation / régime** : où l'élargissement de l'univers apporte réellement des opportunités.
- **Portes de discipline** : performance des trades avec dérogation vs sans.

## 12.3 Comportement (détection simple, sans jugement)

Signaux relevés et affichés dans le digest : entrée dans les 5 séances suivant un stop sur la même valeur ; taille > taille proposée ; trade hors créneau ; proposition BUY ignorée puis prise plus haut le lendemain (« FOMO ») ; quantité exécutée restée non protégée > 15 min (délai exécution → protection) ; sortie avant objectif sans signal de sortie ; série de 3 pertes suivie d'une taille accrue.

## 12.4 Revue hebdomadaire (samedi 08:00) et mensuelle

- Contenu calculé : KPI de la semaine/mois (docs/10), post-mortem des signaux, signaux manqués (BUY ignorés ayant atteint 2 R), comportements, coûts, état des abonnements, changements d'univers.
- Rédaction LLM (Sonnet) d'un digest en français : ce qui a marché, ce qui n'a pas marché, **3 questions** à se poser, propositions d'ajustement **formulées comme des ADR à valider** (ex. « relever le seuil RVOL de D1 à 2,5 : +0,3 R d'expectancy sur 40 signaux, échantillon faible »). Le LLM reçoit uniquement des agrégats calculés ; il ne recalcule rien.
- Mémoire de trading (`trader_memory`) : notes de l'utilisateur et décisions passées, injectées dans le digest suivant pour la continuité (« la semaine dernière tu avais décidé de… »).

## 12.5 Boucle d'amélioration des règles

1. Toute modification de paramètre passe par `/parametres` (override tracé : ancienne valeur, nouvelle, date, motif) ou par une nouvelle version de `params.yaml` ; les signaux historisés gardent la version utilisée.
2. Une source externe est reconduite si, après 1 mois (≥ 20 signaux), elle apporte soit une expectancy théorique positive, soit ≥ 3 signaux uniques (non couverts par les détecteurs) à expectancy positive ; sinon l'outil propose la résiliation.
3. Un détecteur dont l'expectancy théorique est < 0 sur 60 signaux passe en mode « WATCH seulement » jusqu'à révision.
4. Aucune optimisation automatique de paramètres (anti-sur-apprentissage) ; les propositions sont humaines, tracées, réversibles.

## 12.6 Pages

`/journal` (liste, filtres, fiche trade avec graphique et niveaux, MAE/MFE), `/revue` (digests, post-mortem par détecteur/source/heure, précocité, comportement, propositions d'ADR avec boutons « accepter / refuser / plus tard »).
