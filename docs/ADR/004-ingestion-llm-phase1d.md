# ADR-004 — Sous-lot 1D : ingestion (RSS, IMAP), classification LLM, enregistrement des décisions

Statut : **accepté (provisoire)** · Date : 2026-09-15

## Décisions

1. **LLM via le SDK officiel `anthropic`** (`client.messages.parse` avec schéma Pydantic → JSON validé), température 0, `max_tokens` court, **modèles lus dans `params.yaml › llm`** (spec : `claude-haiku-4-5` pour classer, `claude-sonnet-4-6` pour rédiger ; `claude-sonnet-5` existe et peut être choisi par paramètre). Cache par hash (modèle + prompt versionné + texte) dans `llm_cache` ; **plafond journalier** (`llm.daily_budget_usd`, nouveau) et mensuel ; coût calculé avec `llm.prices_usd_per_mtok` (tarifs datés, jamais utilisés pour un calcul métier). Au-delà du plafond, sans clé ou sur erreur API → **repli par règles** (`app/llm/rules.py`) avec confiance 0,35 et `classified_by = rules` visible.
2. **Prompts versionnés** dans `app/llm/prompts/<nom>_vN.md` ; la version est stockée avec chaque item classé et chaque appel.
3. **RSS** : `feedparser` + `httpx` avec User-Agent navigateur ; un contenu qui n'est pas un flux (HTML d'erreur) lève une erreur, jamais un import vide silencieux ; deux jours sans résultat → P4.
4. **IMAP** : `imaplib` dans `app` (ADR-000 H5) ; parseur `.eml` pur testé sur des **fixtures synthétiques anonymisées** (les 10 lettres réelles de A1.6 sont à déposer par l'utilisateur dans `tests/fixtures/imap/`) ; heure de réception = `INTERNALDATE`.
5. **Signaux externes (D5) en phase 1** : chaque valeur extraite d'une lettre → `signals(detector=external)` + proposition **WATCH** + `decision_snapshot` figé (cotation avec statut, features, régime, état du portefeuille, version des paramètres). Aucun BUY sur lettre seule ; la porte `drift_since_open ≥ 2 %` est déjà évaluée et affichée `BLOQUÉ`. Les BUY arrivent avec les détecteurs D2/D3/D4 (phase 2).
6. **`p_recv`** en phase 1 = cotation EODHD REST différée au moment du poll, avec son horodatage de marché et son statut ; la précocité sera mesurée sur le flux temps réel en phase 3 (ADR-002).
7. **Ordre d'évaluation** des communiqués du soir : après 17:40 Paris, la valeur alimente la liste d'ouverture du lendemain (`premarket_watch`).
