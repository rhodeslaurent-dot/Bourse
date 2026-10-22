# ADR-010 — Règles de gestion du référentiel de l'utilisateur, calculées en Python pur

Statut : **accepté** · Date : 2026-10-22 · Spécification : docs/22 §22.3, docs/06 §6.7, docs/07 §7.9, docs/08 §8.7

## Contexte

Le cahier des charges v1.1.1 (09/09/2026) précède six semaines de pratique. Le référentiel de gestion de l'utilisateur (22/10/2026), non versionné car le dépôt est public, a corrigé plusieurs règles à la suite d'erreurs datées :
- stop placé dans une zone de support ;
- frais absents du ratio gain/risque ;
- prix maximal « validé » par une cassure ;
- plafond de risque qui évinçait les signaux frais ;
- TTF appliquée selon la place de cotation au lieu du siège de l'émetteur.

Le 22/10/2026, l'utilisateur a validé les règles R1 à R21 avec ses décisions :
- R9 : plafond notionnel de 20 % ;
- R10 : portail de marché en option C, modulé par le risque conjoncturel, sans blocage absolu ;
- R12 : Brent traité comme R10 ;
- R13 : SRD si son gain net dépasse celui du comptant ou si la trésorerie manque ;
- R14 : exception si l'opportunité est intéressante et faiblement exposée à l'annonce ;
- R19 : cours relevé à chaque analyse ;
- R21 : dérogation aux plafonds seulement avec son accord explicite.

## Décision

Chaque règle est une fonction pure de `app/domain`, sans accès au réseau ni à la base, avec des paramètres datés dans `params.yaml`. Les cas du référentiel sont rejoués en tests (`tests/unit/test_management_rules.py`).

| Module | Règles |
|---|---|
| `costs.py` | R11 : frais d'achat et de vente, TTF, taxes étrangères, change, portage SRD, impôt de l'enveloppe ; aucun crédit d'impôt supposé sur une perte |
| `admission.py` | R4 : prix maximal = [(objectif + k × stop) ÷ (1 + k)] × (1 − frais), sans exception. R5 : structure intacte. R15 : fiche testable |
| `stops.py` | R1 : placement sous la zone. R2 : invariant. R3 : cliquet, avec la priorité invariant > placement > cliquet |
| `zones.py` | Supports et résistances détectés (pivots regroupés, gaps non comblés) ; objectifs exploitables, jamais un multiple de R |
| `heat.py` | R8 : plafond selon le portail, ligne, budget réservé, pondération SRD, convention A. R9 : notionnel. R21 : éligibilité et borne d'une dérogation |
| `conjuncture.py` | R10 : niveau de risque conjoncturel et portail (ouvert / restreint / restreint+). R12 : SRD et Brent avec hystérésis |
| `events.py` | R14 : exposition à l'annonce mesurée sur les écarts d'ouverture passés de la valeur |
| `ranking.py` | Classement : espérance nette en R, puis ratio net, puis gain net |

`params-init --update` ajoute les nouveaux blocs et les nouvelles clés (risk, risk.stop, srd, data) au `params.yaml` existant, sans toucher aux valeurs.

## Conséquences

- Les nouveaux modules ne sont pas encore branchés sur les propositions : c'est l'objet du lot 2.3. Les règles existantes (`risk.check_candidate`, objectifs 2R/3R) restent actives jusque-là.
- Aucune règle du référentiel n'est contournable par l'interface. Seuls les plafonds peuvent être dépassés, par une dérogation R21 explicite, tracée dans `decisions` et l'instantané, bornée à × 1,5 du plafond et jamais appliquée au risque par trade.
- Les montants du référentiel (600 €, 300 €) sont exprimés en fraction du capital pilote, pour suivre ses révisions.
