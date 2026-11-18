# ADR-025 — Scan d'ouverture : écart + catalyseur (D1), fiche et alerte Telegram

- **Statut** : accepté le 18/11/2026 (demande de l'utilisateur ; choix « page + alerte Telegram » du même jour).
- **Contexte** : « toujours pas d'opportunités alors que les marchés bougent et qu'il y a des publications… une grande valeur fait +7 % à l'ouverture après de très bons résultats : c'est incertain, avec un risque de perte significatif, mais il y a une opportunité sur un mouvement ; je suis ensuite juge du parti pris et de la stratégie ». Le détecteur D1 de docs/06 (écart d'ouverture + catalyseur, « phase 3 ») n'était pas construit : le job `open_scan` lisait `times_from`, que le planificateur n'a jamais lu, et rien ne fournissait la séance du jour aux détecteurs.

## Décision

1. **Deux jobs** toutes les 10 minutes après l'ouverture : `open_scan_eu` (09:05–10:55 Paris, places européennes) et `open_scan_us` (09:35–10:55 heure de New York, fuseau déclaré dans le job ; calendrier XNYS vérifié par le service). Seules les places ouvertes à l'instant sont lues (calendrier par place).
2. **Candidats** : valeurs de l'univers avec un **catalyseur depuis la clôture précédente** (`detectors.d1_gap_catalyst.catalyst_hours`) — résultats publiés (`official_filings`, famille `resultats` : AMF, 8-K 2.02) ou actualité classée positive, forte ou moyenne. Un appel de cours du jour EODHD par valeur (au plus `max_candidates`).
3. **Détecteur D1** (`app/domain/detectors.py`, Python pur) : écart ≥ `gap_min_large` (valeurs à plus de 5 M€ échangés par jour) ou `gap_min_other` ; rythme de volume ≥ `rvol15_min` ; écart **tenu** (dernier cours au-dessus de l'ouverture et à moins de `hold_max_below_high_pct` du plus haut). Entrée : cassure du plus haut du jour, au plus `entry_max_pct_above_or_high` au-dessus ; stop sous le plus bas du jour moins `risk.stop.buffer_atr_mult` ATR (docs/07 §7.2), signalé au-delà de `stop_max_adr_mult` ADR ; objectifs = niveaux exploitables au-dessus (R4, jamais un multiple de R).
4. **Approximation déclarée** (dans la preuve de chaque signal) : le « volume relatif des 15 premières minutes » est approché par volume du jour / (volume moyen 20 séances × max(part de séance écoulée, `opening_volume_share`)) — l'ouverture concentre une part du volume bien supérieure à sa part du temps ; le plus haut « d'ouverture » est le plus haut du jour au moment du scan.
5. **Le moteur décide comme pour toute fiche** : admission (C, T, R, M), **catalyseur classé ≥ `scoring.d1_min_c`** exigé pour D1 (docs/06 §6.5, jusqu'ici non appliqué), taille calculée par Python sur le prix maximal et le stop glissé (règle 7), plafonds, coupe-circuit, fraîcheur du cours (règle 2 : cours différé → « à vérifier », pas de fiche d'ordre), instantané de décision (règle 10). Une fiche par valeur et par jour.
6. **Alerte Telegram P2** (≤ 12 lignes) pour chaque fiche nouvelle, achat ou surveillance : le mouvement, le catalyseur, le plan (entrée, prix maximal, stop), la taille calculée ou la raison simple de l'absence d'ordre, le cours différé à vérifier chez le courtier, « non mesuré : incertain, risque de perte significatif — à toi de juger ». La fiche expire après `expire_minutes_after_scan`, au plus tard à `expire_latest_local` (heure de la place).
7. **Modes** : seulement dans `availability.d1_allowed_modes` (`disponible` par défaut, docs/02 §2.3) ; sinon le job ne fait rien et le dit dans sa note.
8. **Jamais rejoué sur barres journalières** : D1 a besoin de la séance en cours ; il est rangé à part (`LIVE_DETECTORS`), hors des backtests et études qui ne pourraient que le montrer à « 0 signal ».

## Conséquences

- Les mouvements d'ouverture sur publication arrivent sur la page Opportunités et par Telegram ; l'utilisateur juge. Rien n'est mesuré : D1 n'a pas d'espérance mesurée, la fiche le dit.
- Une valeur affaiblie (sous ses moyennes) obtient le plus souvent une fiche de **surveillance** (score d'admission sous le seuil) avec ses niveaux, sans ordre : le seuil reste `scoring.buy_min_ratio`, jamais abaissé en silence.
- Coût : au plus `max_candidates` appels EODHD par passage, 12 passages par marché et par jour.
- Tests : `tests/unit/test_d1_gap.py`, `tests/integration/test_open_scan.py`.
