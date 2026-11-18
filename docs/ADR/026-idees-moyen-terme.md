# ADR-026 — Idées moyen terme : consensus des analystes et tendance

- **Statut** : accepté le 18/11/2026 (demande de l'utilisateur ; choix « analystes + tendance » du même jour).
- **Contexte** : « je veux aussi des investissements moyen terme s'il y a de belles opportunités (par exemple Tesla) ». Les détecteurs de l'outil sont des configurations de quelques séances à quelques semaines ; aucune idée de plusieurs mois ne sortait.

## Décision

1. **Détecteur `m1_consensus_trend`** (Python pur, `app/domain/detectors.py`), lancé par le moteur à chaque scan :
   - au moins `min_analysts` analystes ; objectif moyen au moins `min_upside` au-dessus du cours. L'objectif est celui que le moteur recoupe déjà (docs/23 S4) : Yahoo et TradingView écartés s'ils divergent de plus de 15 %, ou si la devise n'est pas celle de la cotation ; consensus TradingView lu il y a au plus `max_age_days` jours ;
   - recommandation publiée (Yahoo : `strong_buy`, `buy`…) dans `recommendations_ok` quand elle est connue ;
   - **tendance longue intacte** (clôture au-dessus de la moyenne 200 jours, 50 au-dessus de 200), **ou** résultats récemment salués par le marché (entrée « catalyseur » de la liste, ADR-024) ;
   - pas de course : clôture au plus `max_above_mm50` au-dessus de la moyenne 50 jours.
2. **Niveaux** : limite à la clôture + `entry_max_pct` ; stop sous le plus bas des `stop_lookback` dernières séances, moins le tampon ATR de `risk.stop` (docs/07 §7.2), signalé au-delà de `max_stop_pct` ; objectif = l'objectif moyen des analystes (niveau publié, comme celui d'une lettre ; jamais un multiple de R).
3. **La note numérique de TradingView n'est pas utilisée** tant que son échelle n'est pas déclarée (`rating_best`, `rating_max`). Elle est affichée telle que publiée ; `gates-coverage --value` la montre à côté de la recommandation Yahoo pour la vérifier sur des valeurs connues. Jamais devinée.
4. **Le moteur décide comme pour toute fiche** : admission (blocs T, F, R, M), quantité calculée par Python sur le prix maximal et le stop glissé (règle 7), plafonds, coupe-circuit, coûts par compte, instantané de décision.
5. **Page Opportunités** : section à part « Idées moyen terme — analystes et tendance », juste après ce qui est actionnable. Chaque fiche porte la mention « non mesuré ». Pas d'alerte Telegram : ce sont des idées de plusieurs mois, pas des décisions de l'instant.
6. **Jamais rejoué sur l'historique** : le consensus n'est stocké que depuis novembre 2026 (`LIVE_DETECTORS`, comme D1).

## Conséquences

- **À décider par l'utilisateur** : l'admission est celle du swing (`scoring.buy_min_ratio`, 0,75). Elle pénalise un stop large en ADR (bloc R, risque « élevé »). La plupart des idées moyen terme sortiront donc **à surveiller**, avec leur plan et la quantité calculée, mais sans ordre au carnet. Une admission propre au moyen terme est une décision à prendre, jamais un seuil abaissé en silence.
- Le consensus n'est connu que pour les valeurs que le screener trouve. Couverture du 18/11 : 1 819 sur 4 799 ; elle s'élargit avec le filtre par place.
- Tests : `tests/unit/test_m1_consensus.py`, `tests/integration/test_medium_term.py`.
