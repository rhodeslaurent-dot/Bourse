# ADR-011 — Modèles Claude de génération 5 et formulation de la règle 3

- **Statut** : **proposé** le 25/10/2026 (rédigé en autonomie). **Décision réservée à l'utilisateur** : la règle 3 de CLAUDE.md est une règle absolue ; elle n'est pas modifiée par cet ADR tant qu'il n'est pas accepté.
- **Contexte** : audit docs/25, levier L2.

## Contexte

- L'outil appelle `llm.classify_model` (Haiku 4.5, 1 $ / 5 $ par million de jetons en entrée / sortie) et `llm.write_model` (Sonnet 4.6, 3 $ / 15 $), avec `temperature: 0` transmis par `extra_body` (SDK épinglé, `app/llm/gateway.py`).
- Génération 5 (documentation de l'API, relevée le 25/10/2026) :
  - **Haiku 5.5** : 0,10 $ / 0,50 $ (invites ≤ 100 000 jetons), soit **10 fois moins** que Haiku 4.5 ; **Sonnet 5.5** : 2 $ / 10 $, soit un tiers de moins que Sonnet 4.6.
  - Une **température non par défaut renvoie une erreur 400** (de même `top_p`, `top_k`, et le pré-remplissage de la réponse).
  - **Réflexion adaptative active par défaut** : ses jetons sont facturés comme des jetons de sortie. Elle se désactive sur Haiku 5.5 (`thinking: {type: "disabled"}`, effort ≤ `high`) et sur Sonnet 5.5 (`{type: "between_tools"}`, effort ≤ `high`).
  - **Nouveau tokeniseur** : le même texte compte environ **30 % de jetons en plus**.
- La règle 3 dit : « sortie JSON validée par Pydantic, **température 0**, cache par hash de prompt, plafond de dépense journalier ». Avec la génération 5, « température 0 » est impossible.

## Ce que la température 0 apporte réellement ici

La température 0 rend une réponse **plus stable**, pas **reproductible** (l'API ne garantit pas l'identité de deux réponses). Dans l'outil, la reproductibilité vient déjà d'ailleurs :

1. **schéma fermé** : sorties Pydantic à listes fermées (type, sens, ampleur, verdict, confiance…), textes bornés ;
2. **cache par empreinte** : une même entrée (modèle, prompt versionné, texte) renvoie la réponse stockée — rejouable à l'identique ;
3. **garde-fou des nombres** : tout nombre absent des faits est écarté ; aucun prix, stop, taille ou score ne vient du modèle ;
4. **journal** : `llm_calls` (modèle, version du prompt, empreinte, coût) et instantanés de décision (règle 10).

## Options

| Option | Effet | Coût estimé | Risque |
|---|---|---|---|
| **A — Statu quo** (4.5 / 4.6, température 0) | aucun changement | coût actuel | modèles plus anciens ; retrait futur inévitable |
| **B — Reformuler la règle 3 et migrer** après évaluation | « température 0 **lorsque le modèle l'accepte** ; sinon, réflexion désactivée et déterminisme assuré par 1–4 ci-dessus » | classement ≈ −85 % (prix ÷ 10, jetons × 1,3) ; rédaction ≈ −13 % (prix × 2/3, jetons × 1,3), réflexion désactivée | variabilité légèrement plus forte d'un appel non mis en cache ; à mesurer |
| C — Migrer seulement le classement (Haiku 5.5), garder Sonnet 4.6 pour la rédaction | gain principal sans toucher aux textes longs | classement ≈ −85 % | deux régimes de règle à documenter |

## Évaluation à mener avant la décision (aucun appel lancé sans votre accord)

- **Jeu de 200 nouvelles** déjà classées par Haiku 4.5 (`news_items`, `classified_by = llm`) et **20 revues de lettres** : rejouer avec Haiku 5.5 / Sonnet 5.5, réflexion désactivée.
- Mesures : accord par champ (type, sens, ampleur, portée) avec la sortie 4.x ; part des réponses rejetées par Pydantic et par le garde-fou des nombres ; stabilité (3 répétitions sur 30 entrées, cache désactivé) ; coût réel.
- Les désaccords sont **tranchés par vous** sur un échantillon (étiquettes humaines) : l'ancien modèle n'est pas une vérité.
- Coût de l'évaluation : de l'ordre de 1 à 2 $ (estimation à confirmer avant lancement).
- Critère : accord ≥ 90 % sur type et sens, aucun champ hors schéma, garde-fou des nombres pas plus souvent déclenché.

## Changements de code prévus si B ou C est accepté (non faits)

- `app/llm/gateway.py` : pour un modèle de génération 5, ne plus envoyer `temperature` et envoyer la désactivation de la réflexion adaptée au modèle ; coût calculé sur les jetons réellement facturés (réflexion comprise).
- `config/params.yaml` : nouveaux modèles et tarifs datés dans `llm.prices_usd_per_mtok` ; `llm.temperature` documenté « si accepté ».
- Revalidation du SDK épinglé (la contre-expertise recommande de le garder épinglé) : test sur le vrai SDK comme pour la correction du 21/10.
- CLAUDE.md règle 3 reformulée (option B), signalée dans docs/15.

## Recommandation

**Option C d'abord** (classement en Haiku 5.5 après l'évaluation : c'est là que le volume et le gain sont), puis B pour la rédaction si l'évaluation est bonne. La décision vous appartient.
