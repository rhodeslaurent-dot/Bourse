# 08 — Comptes (PEA BoursoBank, CTO/SRD Saxo) : routage, coûts, calendrier

## 8.1 Les deux comptes

| | PEA — BoursoBank | CTO avec SRD — Saxo Banque |
|---|---|---|
| Titres | UE/EEE éligibles PEA uniquement | Tout, dont SRD Paris, US, Suisse/UK (phase 6) |
| Levier | Aucun (comptant) | SRD : couverture 20 % espèces (levier ≤ 5 théorique, **≤ 2 dans l'outil**) |
| Fiscalité | Retrait > 5 ans : prélèvements sociaux seuls (18,6 % depuis 2026, à confirmer sur avis) ; < 5 ans : 31,4 % et clôture (sauf exceptions) | PFU 31,4 % (12,8 % IR + 18,6 % PS) ou barème ; moins-values imputables 10 ans |
| Coûts spécifiques | Courtage (grille BoursoBank, plafond 0,5 % en PEA) | Courtage Saxo (Classic 0,08 % min 2 €) ; **CRD 0,023 %/jour** sur le notionnel SRD (≈ 0,7 %/mois, ≈ 8,3 %/an) ; **prorogation 0,20 %, min 10 €** ; TTF 0,4 % sur achats nets fin de journée de valeurs françaises > 1 Md€ (depuis 01/04/2025) |
| Import | Export CSV/Excel manuel (hebdo) + saisie Telegram | OpenAPI lecture seule (en séance toutes les 5 min + rapprochement 19:00) |
| Types d'ordres stop | À seuil / à plage de déclenchement (à confirmer) | Stop, stop-limit, stop suiveur, OCO (à confirmer) |
| Usage typique (jamais imposé, cf. §8.2) | Swing 2–6 semaines, moyen terme | Trades courts quand la trésorerie PEA manque, opportunités hors PEA |

## 8.2 Comparaison des trois options de compte (`domain/accounts.compare`)

Le SRD réduit l'immobilisation initiale ; il ne rend pas une opération plus rentable. Il n'y a donc **pas de préférence par défaut** : pour chaque proposition, l'outil compare les trois options admissibles et affiche le tableau.

| Option | Admissible si | Coût estimé sur l'horizon | Contraintes affichées |
|---|---|---|---|
| **PEA** (BoursoBank) | `pea_eligible_regulatory` ∧ `pea_available_boursobank` ∧ `pea_order_types_ok` ∧ cash PEA ≥ montant (ou cash additionnel autorisé) | courtage A/R BoursoBank **+ TTF si due** (la TTF s'applique aussi dans le PEA : achat d'actions françaises > 1 Md€) | Fiscalité la plus favorable ; pas de levier ; retrait avant 5 ans = clôture |
| **CTO comptant** (Saxo) | cash CTO ≥ montant | courtage A/R Saxo + TTF si due | Aucun engagement de couverture |
| **SRD** (Saxo) | `srd_status = complet` ∧ levier après trade ≤ max ∧ couverture disponible ≥ 1,5 × requise ∧ réserve de cash ≥ 20 % ∧ coût ≤ 25 % du gain à l'objectif 1 | courtage A/R + CRD × jours estimés + prorogations probables + TTF si due | Trésorerie immobilisée = couverture seulement ; liquidation ; appel de marge sous un jour |

Critères de comparaison (dans cet ordre, tous affichés) : coût total estimé, trésorerie immobilisée, durée prévue vs cycle de liquidation, risque de couverture (scénario de gap), impact fiscal (PEA vs PFU). Une option non admissible est listée avec la raison. La sortie est une **comparaison** (`options[]`, chacune avec `admissible`, `cout`, `tresorerie`, `risques`), plus une suggestion argumentée ; l'utilisateur choisit dans la fiche d'ordre. Cas particuliers : titre non éligible PEA → CTO comptant ou SRD seulement ; horizon `medium` → SRD déconseillé (coût cumulé et prorogations) mais affiché.

## 8.3 Coût d'un trade SRD (affiché dans chaque proposition CTO)

`coût_total = courtage_aller + courtage_retour + CRD_jour × notionnel × jours_détenus_estimés + prorogations × (taux_prorogation × notionnel, min 10 €) + TTF (0,4 % × montant si valeur FR > 1 Md€ et position conservée au-delà de la journée)`.

Exemple (paramètres 09/2026) : notionnel 6 000 €, 15 jours, 0 prorogation, valeur FR > 1 Md€ : courtage 2 × 4,80 € + CRD 0,023 % × 6 000 × 15 = 20,70 € + TTF 24 € ≈ **54 €**, soit 0,9 % du notionnel — à comparer à l'objectif (2 R). L'outil refuse (BLOQUÉ) un trade SRD dont le coût estimé dépasse 25 % du gain à l'objectif 1. Le traitement exact de la TTF sur les achats SRD (au règlement ou non) est **à confirmer** sur un relevé Saxo (paramètre `tax.ttf_on_srd`) ; en attendant, l'outil retient l'hypothèse prudente (TTF due).

## 8.4 Calendrier SRD (table `srd_calendar`, source Boursorama/abcbourse/Bourse Direct/Saxo, concordants)

Liquidations 2026 (jour de liquidation → règlement) : 27→30 janv. ; 24→27 fév. ; 26→31 mars ; 27→30 avr. ; 26→29 mai ; 25→30 juin ; 28→31 juil. ; 26→31 août ; **25→30 sept.** ; 27→30 oct. ; 25→30 nov. ; 28→31 déc. Règle observée : liquidation = 4e dernier jour de bourse du mois ; **encoder les dates publiées** et calculer 2027 provisoirement (26 janv., 23 fév., 24 mars, 27 avr., 26 mai, 25 juin, 27 juil., 26 août, 27 sept., 26 oct., 25 nov., 28 déc. — à confirmer à parution ; passage européen à T+1 annoncé pour octobre 2027, à vérifier).

Comportement de l'outil : à J−3 de la liquidation, chaque position SRD reçoit une alerte « décision de liquidation » (solder / proroger avec coût affiché / passer au comptant) ; le brief de J−1 rappelle l'heure limite de prorogation Saxo (paramètre `srd.prorogation_deadline_local`, à renseigner). Par défaut, l'outil recommande de **ne pas proroger** une position sous 1 R de gain.

## 8.4 bis Éligibilité PEA en trois niveaux

`pea_eligible_regulatory` (siège UE/EEE, IS — règle service-public F2385) ≠ `pea_available_boursobank` (le titre est effectivement négociable dans le PEA BoursoBank : certaines places ou lignes ne le sont pas) ≠ `pea_order_types_ok` (les ordres nécessaires — limite, à seuil de déclenchement — sont disponibles sur cette place chez BoursoBank). Les trois drapeaux sont stockés avec source et date ; le premier est calculé (docs/05), les deux autres sont **confirmés par l'utilisateur** à la première proposition sur une place donnée (case à cocher, mémorisée par place/segment) et révisés si un ordre est refusé. Un titre n'est proposé pour le PEA que si les trois sont vrais.

## 8.5 Couverture et appel de marge

- Couverture requise = Σ (notionnel × taux selon nature de la couverture) ; couverture disponible = espèces + titres pondérés (paramètres) ; ratio affiché en permanence.
- Alerte si couverture disponible < 1,5 × requise ; BLOQUÉ pour toute nouvelle entrée si < 1,3 × ; rappel que la mise en demeure AMF exige une régularisation sous un jour de bourse.
- Simulation : « si le portefeuille baisse de 5 % / 10 % demain, couverture = ? ».

## 8.6 Synchronisation des comptes

- **Saxo** : (1) **en séance**, interrogation toutes les `accounts.cto.sync_intraday_minutes` (5 min, dans les limites de débit Saxo) des positions, ordres en attente (états, exécutions partielles, stops actifs/exécutés/rejetés), cash et engagements ; (2) **immédiatement** après toute déclaration Telegram/web et **avant toute nouvelle proposition** ; (3) rapprochement complet à 19:00. Rapprochement des exécutions déclarées (provisoires, `declared_uid`) avec les exécutions courtier (`broker_fill_id`) : même compte, même instrument, quantité exacte, prix ± 0,5 %, ± 10 min → liées, les deux références conservées ; sans correspondance sous 24 h → alerte « exécution déclarée non retrouvée » ; exécution courtier sans déclaration → créée et taguée `discretionary?`. Écarts listés dans le rapport.
- **BoursoBank (hebdo, ou après chaque opération)** : import CSV ; rappel Telegram le vendredi si aucun import depuis 7 jours ; saisie manuelle via les boutons « Ordre saisi » / « Exécuté » entre deux imports. L'état PEA est **déclaratif** entre deux imports : chaque proposition PEA affiche « état déclaratif — dernier import le <date> », et l'âge de l'état est soumis à `accounts.portfolio_state_max_age_minutes` (valeur propre au PEA, plus large : `accounts.pea.state_max_age_hours`).
- **Âge maximal de l'état du portefeuille** : une proposition n'est émise que si cash, quantités et ordres en attente du compte cible datent de moins de `accounts.portfolio_state_max_age_minutes` (10 min pour Saxo) ; sinon elle est « à vérifier ». Une cotation récente ne suffit pas si le cash ou les quantités sont périmés.
- Cash par compte : valeur Saxo automatique ; valeur PEA saisie/importée, avec date de dernière mise à jour affichée.
- **Valorisation avec SRD** : valeur liquidative CTO = cash + valeur des positions au comptant + **P&L latent des positions SRD** (jamais le notionnel financé, qui est un engagement, pas un actif payé) − CRD courue ; les engagements SRD (notionnel) et la couverture requise sont affichés à part. Cette formule sert à l'équité (docs/10), au levier et aux plafonds de risque.
- **Priorité 1** : la synchronisation opérationnelle (positions, cash, ordres en attente dont stops, exécutions) est livrée en **phase 1** (docs/15) ; les tableaux de performance viennent après. Sans cash, engagements et stops réels, le moteur de risque ne peut pas fonctionner.
