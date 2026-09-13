# Cahier des charges — Outil personnel d'aide à l'investissement boursier (« BOURSE-PILOT »)

Version 1.1.1 — 9 septembre 2026 — Rédigé pour être exploité directement avec Claude Code.

Mise en cohérence v1.1.1 (sans nouvelle fonctionnalité) : ratio d'admission par détecteur (le bloc catalyseurs n'entre au dénominateur que pour les gaps, sinon un pullback sans actualité ne pouvait jamais être acheté) ; fondamentaux partiels ; convention de glissement (sortie sur stop, entrée seulement si non bornée) et marge de change qui réduit la quantité ; exemple recalculé (114 actions) et aligné sur les tests ; définitions déterministes du risque initial / courant (borné à zéro) / réservé et du drawdown ; ordres J+1 à plage de déclenchement et protection liée « if-done » ou règle de disponibilité ; « exécuté sans protection » = P1 dans tous les modes ; synchronisation Saxo en séance et âge maximal de l'état du portefeuille ; exécutions déclarées rapprochées des exécutions courtier ; TTF comptée dans le PEA ; P1 regroupées par incident ; calendrier par place partout ; validation de configuration par fonctionnalité ; pré-qualification documentaire puis qualification par prototype ; référence « démarche actuelle » définie précisément.

Changements v1.1 (après relecture croisée) : règle de fraîcheur fondée sur l'horodatage de marché ; dimensionnement sur le prix maximal d'entrée avec frais ; plafond de risque ouvert cumulé, secteur, facteur et scénario de gap ; stops structurels explicites et types d'ordres de protection distingués ; machine à états ordre → exécution → protection (un ordre saisi n'est pas une position) ; portes d'admission par détecteur (catalyseur requis pour les gaps seulement) et trois lectures attractivité / entrée / admissibilité ; comparaison explicite PEA / CTO comptant / SRD et équité sans notionnel financé ; mode de disponibilité (disponible / réunion / absent) et écran « 3 décisions du jour » ; mesure sur trois démarches (newsletters / autonome / combinaison) avec rejeu conservateur ; qualification des flux de données avant le moteur ; phases réordonnées (portefeuille et protections avant les entrées préparées, gaps d'ouverture après validation du flux).

## Ce que contient ce dossier

| Fichier | Rôle |
|---|---|
| `CLAUDE.md` | À copier **tel quel à la racine du futur dépôt**. Règles permanentes pour Claude Code (interdits, conventions, commandes, définition de "terminé"). |
| `docs/00` → `docs/16` | Le cahier des charges, découpé par thème. Chaque fichier est autonome et référencé par numéro dans les prompts. En cas de divergence : `config/params.yaml` > `docs/07` > `docs/06` > autres (CLAUDE.md, règle 8). |
| `config/params.example.yaml` | Tous les paramètres métier **datés** (fiscalité, SRD, risque, seuils des détecteurs). Sert de contrat entre la spec et le code. |
| `prompts/claude-code-sequence.md` | La séquence de prompts à donner à Claude Code, phase par phase, avec les critères de sortie. |
| `CDC-COMPLET.md` | Concaténation de tous les documents en un seul fichier (lecture humaine / import dans un projet Claude). |

## Comment l'utiliser avec Claude Code

1. Créer un dépôt vide (`bourse-pilot/`), y copier `CLAUDE.md`, le dossier `docs/`, `config/` et `prompts/`.
2. Ouvrir Claude Code à la racine et lancer le **prompt 0** de `prompts/claude-code-sequence.md` (lecture de la spec + plan). Ne pas laisser Claude Code coder avant d'avoir validé son plan de la phase.
3. Dérouler les phases dans l'ordre (0 → 6) : socle + qualification des données → portefeuille, protections, risque → entrées préparées (MVP) → détection d'ouverture → KPI et fiscal → post-mortem. Une session Claude Code par phase ou sous-lot ; `/clear` entre deux ; commit à chaque critère d'acceptation validé.
4. Chaque phase se termine par les tests de `docs/15` et par une revue « adversariale » (prompt de revue fourni).
5. Toute décision non couverte par la spec est consignée dans `docs/ADR/` (Architecture Decision Records) par Claude Code, jamais prise silencieusement.

## Principes non négociables (rappel)

- **Aucun passage d'ordre automatique.** L'outil propose, l'utilisateur exécute manuellement chez BoursoBank (PEA) et Saxo Banque (CTO/SRD).
- **Aucun cours, aucune donnée inventée.** Toute valeur affichée a une source, un horodatage et un statut (temps réel / différé / EOD / indisponible).
- **Le LLM ne calcule jamais** un prix, un stop ou une taille de position : il classe, résume, rédige. Les calculs sont déterministes et testés.
- **Robustesse avant sophistication** : un signal fiable à 9h05 vaut plus que dix indicateurs à 10h.
- **1 heure de travail manuel par jour maximum** pour l'utilisateur ; tout ce qui dépasse doit être automatisé ou supprimé.

## Démarrage rapide (développement)

```
uv sync                                   # dépendances (Python 3.12)
cp config/params.example.yaml config/params.yaml
uv run python -m app.cli check-config     # validation par fonctionnalité
uv run python -m app.cli migrate && uv run python -m app.cli seed-calendar
uv run uvicorn app.main:app_factory --factory --reload   # http://localhost:8000/sante
uv run pytest -q && uv run ruff check . && uv run mypy app/domain
```

En exploitation (PC Windows) : `docker compose up -d --build` avec `.env` renseigné — voir `RUNBOOK.md`.
État d'avancement : `docs/15-status.md` ; décisions : `docs/ADR/`.
