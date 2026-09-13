# Plan d'action — lancement de BOURSE-PILOT (v1.1.1)

Objectif : arriver à un MVP utilisable (phase 2) en ~9 semaines à raison de 2–3 soirées par semaine + le samedi matin. Règle : une session Claude Code = un sous-lot ; aucune phase ne démarre avant validation de la précédente.

## Semaine 1 — Préparer (≈ 3 h, sans code) — tout est de votre côté

| # | Tâche | Pourquoi maintenant | Durée |
|---|---|---|---|
| 1 | Créer le dépôt `bourse-pilot` (Git privé) et y copier le dossier v1.1.1 : `CLAUDE.md`, `docs/`, `config/`, `prompts/` | Point de départ de Claude Code | 15 min |
| 2 | **Lancer la demande de clé live Saxo OpenAPI** (developer.saxo → application en simulation → demande d'accès live « usage personnel ») | Délai d'approbation inconnu : c'est le chemin critique de la phase 1 | 30 min |
| 3 | Acheter un nom de domaine (~10 €/an), créer un compte Cloudflare gratuit, y ajouter le domaine | Nécessaire au tunnel + Access | 30 min |
| 4 | Depuis le **PC pro**, ouvrir une page de test derrière Cloudflare Access | Vérifie que le proxy d'entreprise laisse passer (sinon plan B Tailscale) | 10 min |
| 5 | Souscrire EODHD « EOD + Intraday » (29,99 $/mois), récupérer la clé API | Source primaire des cours | 10 min |
| 6 | Créer l'adresse Gmail dédiée, rediriger Zonebourse et Momentum vers elle ; garder 10 lettres récentes comme fixtures | Ingestion IMAP + tests | 30 min |
| 7 | Exporter un CSV BoursoBank (portefeuille + historique) et l'anonymiser | Test du parseur | 15 min |
| 8 | Choisir 10 valeurs de référence (FR/DE/NL) et relever, sur 3 séances, cours et volumes dans l'interface Saxo | Qualification des flux (A0.7) | 3 × 10 min |
| 9 | Vérifier chez BoursoBank et Saxo : ordre à plage de déclenchement, ordre lié (if-done), taux de couverture SRD, heure limite de prorogation, plafond PEA | Paramètres « à confirmer » de `params.yaml` | 30 min |
| 10 | Écrire votre planning de disponibilité réel (heures Réunion) et décider du créneau relatif à l'ouverture (11:00 été / 12:00 hiver) | `availability.schedule` | 10 min |
| 11 | Créer le bot Telegram (BotFather : token + votre `user_id`) et un compte healthchecks.io | Alertes et watchdog | 15 min |
| 12 | Préparer le PC : Docker Desktop au démarrage, `powercfg`, heures d'activité Windows Update 07:00–01:00 Réunion (checklist docs/14 §14.2) | Fiabilité | 20 min |

## Semaines 2–3 — Phase 0 avec Claude Code : socle + qualification des données

1. Session 1 : **Prompt 0** (lecture de la spec, hypothèses, plan) → valider le plan et l'ADR-000.
2. Sessions 2–3 : **Prompt 1** (socle) → critères A0.1 à A0.6 ; faire le **test de reprise réel** (redémarrage, coupure box 10 min, arrêt Docker).
3. Session 4 : **Prompt 2** (qualification) → 3 séances de mesures → **ADR-002** (sources retenues, périmètre RVOL).
4. Session 5 : prompt de **revue adversariale** ; corriger ; tag `v0.1`.

Sortie de phase : vous ouvrez `/sante` depuis le PC pro et le téléphone, vous recevez un message Telegram avec boutons, et vous savez quels flux sont fiables.

## Semaines 4–6 — Phase 1 : portefeuille opérationnel, protections, risque

1. **Prompt 3** en 4 sous-lots (comptes et états → moteur de risque → univers → ingestion), une session par sous-lot, validation à chaque fois.
2. 5 jours de contrôle : positions/cash/ordres Saxo identiques entre l'outil et l'interface ; exemple de dimensionnement (114 actions) reproduit par les tests.
3. Dès cette phase, l'outil enregistre vos décisions et leurs instantanés : c'est le début de la mesure.
4. Revue adversariale ; tag `v0.2`.

## Semaines 7–9 — Phase 2 : MVP (entrées préparées, 3 décisions, brief et rapport)

1. **Prompt 4** en 4 sous-lots (détecteurs EOD → scoring et admission → disponibilité et 3 décisions → jobs et rapport).
2. Souscrire **une** newsletter de test (Décision Bourse, 08:30) à ce moment seulement.
3. **2 semaines en parallèle** de votre processus actuel avec la page `/ecarts` : validation technique, pas de conclusion de rentabilité.
4. Revue adversariale ; tag `v1.0-mvp`. Ensuite seulement : phase 3 (gaps d'ouverture), puis 4 et 5.

## Décisions qui restent les vôtres

- Nom de domaine et compte Cloudflare (semaine 1).
- Budget P0 ≈ 40 €/mois + abonnements existants ; Zonebourse Premium et Décision Bourse : pas avant la phase 2.
- Planning de disponibilité et créneau d'ouverture (semaine 1).
- Confirmation des types d'ordres BoursoBank/Saxo (semaine 1) — conditionne les ordres J+1.
- Go/no-go à la fin de chaque phase, sur les critères de `docs/15`.

## Rythme et garde-fous

- Une session Claude Code = un sous-lot terminé par quelque chose de visible (page, message Telegram, test vert). `/clear` entre deux sessions.
- Commit à chaque critère validé ; `docs/15-status.md` mis à jour en fin de session.
- Ne jamais laisser Claude Code coder sans plan validé ; ne jamais lancer la phase suivante avant les critères de la phase en cours.
- Toute modification de règle passe par un ADR ou un override tracé, jamais par un changement silencieux.
