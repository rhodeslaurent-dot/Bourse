# ADR-014 — Désactivation de d3 et d4 ; règle de sortie de d7 ; période scellée

- **Statut** : **proposé le 27/10/2026** (tour 2 de la recherche, docs/33). À valider par l'utilisateur. Remplace en partie la section « Conséquences et sortie » de l'ADR-013, dont le texte reste inchangé.
- **Contexte** : enregistrement n° 1 de la recherche (plan `2026-10-27_reg1`, sha `85d37447…`), exécuté le 26/10/2026, calibration VALIDE (concordance 1,0). Le plan a fixé une règle avant toute mesure : un détecteur existant dont la borne haute de l'intervalle de confiance hors échantillon est sous 0 (`existing_detectors_ci_high_max: 0`) appelle un ADR de désactivation ou de refonte. Résultats hors échantillon (walk-forward du backtest n° 2) :
  - **d3** : R −0,402, IC [−0,776 ; −0,378] → règle atteinte.
  - **d4** : R −0,397, IC [−0,585 ; −0,002] → règle atteinte, de justesse.
  - **d7** : R −0,170, IC [−0,304 ; +0,057] → règle non atteinte.

  Pour d3 et d4, la perte précède les frais : sur tout le backtest n° 2, R brut −0,225 (d3, n 243) et −0,174 (d4, n 420). La géométrie appariée de d3 est négative (−0,124, IC [−0,383 ; −0,064]), et d3 inversé ne rapporte rien (+0,005 à +0,008). Aucune refonte n'a donc d'appui. Le point et l'intervalle de cette règle ne portent pas sur la même quantité : moyenne par trade pour le point, moyennes hebdomadaires pour l'IC. Le rapport imprime désormais les deux, mais d3 et d4 ne sont pas recalculés « pour voir ».

## Décision

1. **d3 et d4 sont désactivés** : `detectors.d3_range_expansion.enabled: false` et `detectors.d4_pullback.enabled: false`. C'est la clé qui existe déjà, pour garder une source unique (règle 8). Ils ne produisent plus aucune configuration, même en WATCH. Le code, les tests et le schéma sont conservés. Une réactivation ne peut passer que par un nouvel enregistrement pré-déclaré et un ADR.
2. **d7 reste en surveillance seulement** (ADR-013), sorties inchangées. **Règle pré-déclarée** : si, au verdict de l'enregistrement n° 2, aucune variante de sortie de d7 mesurée à risque constant n'atteint A2 et A4 sur les données de test, un ADR de désactivation de d7 suit, sans nouveau tour.
3. **Période scellée européenne** (2024-11 → fin des données) : aucun candidat n'a été figé au tour 1, et les diagnostics F0 y ont déjà publié des chiffres. Elle n'est plus disponible pour confirmer quoi que ce soit. Une refonte de d7, ou tout autre déclencheur, ne peut être confirmée que sur des données jamais vues : nouvelles places ou suivi en ombre prospectif.
4. d2 reste en surveillance seulement (ADR-013) : son walk-forward n'a que 8 trades, ce qui ne permet aucune conclusion.

## Conséquences

- Moins de configurations affichées. d3 et d4 n'en produisaient déjà aucune d'actionnable depuis l'ADR-013.
- Aucun paramètre d'entrée, de stop ou d'objectif ne change.
- À l'application : `config/params.example.yaml`, `docs/15-status.md` (décision et critère), test qui vérifie que d3 et d4 ne produisent aucune configuration, `params-init --update --set "detectors.d3_range_expansion.enabled=false" --set "detectors.d4_pullback.enabled=false"` sur le PC.
- Annulation : remettre `enabled: true` par un nouvel ADR.
