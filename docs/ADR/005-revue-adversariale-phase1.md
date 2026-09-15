# ADR-005 — Revue adversariale de fin de phase 0–1 : constats et corrections

Statut : **accepté** · Date : 2026-09-15 · Revue menée par un agent en lecture seule avec le prompt de `prompts/claude-code-sequence.md`.

24 constats (5 critiques, 17 majeurs, 2 mineurs). Tous les critiques et majeurs sont corrigés ou tranchés ci-dessous ; les tests de non-régression sont dans `tests/integration/test_review_fixes.py`.

| # | Constat | Correction |
|---|---|---|
| C1 | Déclaration + import non rapprochés comptés deux fois | L'import d'un fill non apparié sur une position ayant une déclaration du même sens à ± 24 h marque la déclaration `superseded?` (ne compte plus) et le fill `suspected_duplicate?` ; P3 « écarts à documenter » |
| C2 | Horodatages BoursoBank lus en UTC | Lus en `Europe/Paris` puis convertis (test été/hiver) |
| C3 | Ventes Saxo jamais importées | `closedpositions` → fills de vente ; position détenue dans l'outil et absente chez Saxo → P3 « écart » |
| C4 | FX figé à 1,0, devise jamais écrite | Devise copiée depuis les exécutions ; `fx_to_eur` utilisé ; FX inconnu → position exclue et vue marquée **incomplète** (jamais 1,0) |
| C5 | Calendrier de Paris codé en dur dans le moniteur | Place de l'instrument (référentiel ou carte) ; place inconnue → alerte de seuil seulement, jamais de SELL au marché |
| M6 | Pentecôte manquante pour Xetra | 09/06/2025, 25/05/2026, 17/05/2027 ajoutés (Euronext ouvert) |
| M7 | Place sans calendrier ignorée en silence | P4 listant les places non couvertes ; calendriers P1 (XMIL, XMAD, XSTO…) restent **à encoder** avant d'activer ces marchés |
| M8 | Statut Saxo inconnu → plantage | Table de correspondance (Parked/Working → active, Filled → executed, Rejected/NotWorking → rejected…) ; reconstruction tolérante |
| M9 | Stop déclaré non confirmé compte comme protection | **Conservé, conforme à docs/09 §9.1** (« Stop saisi » met à jour la quantité protégée) ; un statut `rejected` ramené par l'import retire la protection et déclenche la P1 |
| M10 | Double clic à cheval sur une minute | Clé en UTC + fenêtre de 2 min sur (compte, ISIN, sens, quantité, prix) |
| M11 | Vente sans position perdue | Conservée `sans_position?` sans position, comptée, P3 ; jamais rejouée (dédup par `broker_fill_id`) |
| M12 | PMP utilisé comme cours | Position sans cours exclue des agrégats, bannière « RISQUE INCOMPLET », statut et horodatage de marché des cours affichés ; la P1 « non protégée » reste |
| M13 | Risque réservé quasi nul | Stop de la proposition liée ; sinon réserve au budget maximal par trade et mention « stop inconnu » |
| M14 | INTERNALDATE en heure locale | Conversion par epoch (`time.mktime`) ; sans INTERNALDATE, heure du poll |
| M15 | Séance déduite de la date UTC | Date de Paris, avancée à la prochaine séance ouverte de la place |
| M16 | Niveaux LLM non vérifiés | Un niveau extrait par le LLM n'est retenu que s'il figure littéralement dans la lettre ; les règles priment ; rejets journalisés dans `parsed.rejected_llm_levels` |
| M17 | Champ `surprise` exposé au LLM | Retiré du schéma ; stocké à `null` tant qu'aucun consensus daté n'existe |
| M18 | Prix de modèle inconnu → coût 0 | `LlmUnavailable` (repli règles) ; fenêtre journalière en heure de Paris |
| M19 | Écritures ouvertes sans Cloudflare | Sans `CF_ACCESS_*` : toute méthode non-GET → 503 sauf `X-App-Token` ou `ALLOW_UNAUTHENTICATED_WRITES=1` (développement) |
| M20 | Périmètre du régime silencieux, seuils en dur | `regime.distribution_days_sessions`, `distribution_day_drop`, `index_isins` dans params ; `breadth_n` enregistré ; variation > 20 % → P4 `source_switched` |
| M21 | Screener apparié sans la place, capitalisation non convertie | Clé `EXCHANGE:SYMBOL` ; capitalisation retenue seulement en EUR (devise de `market_cap_basic` à qualifier) ; échec du screener → P4 « valeurs périmées » |
| M22 | Jobs `running` éternels après redémarrage | Marqués `interrupted` au démarrage |
| m23 | Paramètres non câblés / défauts métier | `eodhd_from(config)` (seuils de fraîcheur, quota), `unmatched_alert_hours`, plafonds de taille sans défaut dans `SizingInput` |
| m24 | Secrets dans logs/erreurs, garde par `startswith` | `redact()` sur logs, traces et `jobs_runs.error` ; `normpath` avant contrôle Saxo ; scan anti-trading étendu à `alembic/` et `config/` |

Non couvert par cette revue et reporté : `fresh_enough_for_intraday_order` à câbler avant toute fiche d'ordre (phase 2) ; calendriers des places P1/P2 ; tests d'incident restants (changement d'heure sur fenêtres `every_minutes`, e-mail au format inattendu → quarantaine).
