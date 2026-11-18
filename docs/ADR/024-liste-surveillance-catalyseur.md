# ADR-024 — Liste de surveillance : une entrée par catalyseur, à côté du momentum

- **Statut** : accepté le 18/11/2026 (remarque de l'utilisateur, mise en œuvre en autonomie).
- **Contexte** : « NVIDIA fait −25 % sur les derniers mois, avec une publication très bonne hier soir et une très forte actualité ; ce n'est pas normal qu'elle ne soit pas en watchlist. » La liste de surveillance (docs/18 §18.2) prend d'office les 100 valeurs les plus fortes de l'univers (force relative ≥ 80, au-dessus des moyennes 50 et 200 jours), plus les valeurs des lettres et les ajouts manuels : une valeur affaiblie qui vient de publier des résultats salués n'y entre jamais, par construction.

## Décision

Une seconde entrée d'office, **catalyseur**, à partir de faits stockés seulement (jamais une opinion sur les chiffres publiés), quelle que soit la tendance passée :

1. **Résultats salués par le marché** : une publication de résultats (communiqué AMF, 8-K item 2.02 à la SEC — `official_filings`, famille `resultats`) dans les `watchlist_catalyst.lookback_days` derniers jours, suivie d'une hausse d'au moins `min_move_pct` entre la dernière clôture avant la publication et la première après (publication avant la clôture : la séance du jour ; après : la suivante), avec un volume d'au moins `min_rel_volume` fois la moyenne des 20 séances d'avant (`app/domain/catalyst.py`).
2. **Radar d'actualité** : les étiquettes `radar_labels` du radar (`attention_corroboree` : attention inhabituelle appuyée par des nouvelles) des `radar_days` derniers jours.

- Valeurs de l'univers seulement ; une valeur déjà dans la liste pour une autre raison n'est pas touchée.
- La ligne dit le fait (« résultats publiés le 17/11 (SEC), +6,0 % le 18/11, volume ×3,0 ») et sort après `keep_days` jours, sauf nouveau fait.
- **Surveillance seulement** : jamais un ordre ni une proposition (une proposition naît toujours d'un signal ; la mesure de l'étape 1 n'a pas validé les surprises de résultats comme déclencheur d'entrée, docs/40 §40.3 bis).
- Recalculée chaque matin avec la liste (`watchlist_refresh`, 07:15 Paris, après les clôtures de la veille, États-Unis compris).

## Conséquences

- NVIDIA (si sa réaction du 18/11 dépasse les seuils) entre dans la liste le 19/11 au matin ; Take-Two seulement une fois dans l'univers (capitalisation connue : correctif du screener du 18/11).
- `gates-coverage --value <ISIN|ticker|nom>` suit une valeur porte par porte et dit pourquoi elle n'est ni proposée ni surveillée.
- Tests : `tests/unit/test_catalyst.py`, `tests/integration/test_catalyst_watch.py`.
