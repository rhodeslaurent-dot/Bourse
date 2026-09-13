# 11 — KPI de suivi fiscal (priorité 3)

Avertissement à afficher dans l'UI : l'outil fournit des **estimations** à partir de paramètres datés ; la référence reste l'IFU du courtier et la déclaration. L'outil n'est ni un conseil fiscal ni un logiciel de déclaration.

## 11.1 Paramètres fiscaux datés (`config/params.yaml › tax`, sources docs/16)

| Paramètre | Valeur en vigueur (09/2026) | Date d'effet | Statut |
|---|---|---|---|
| Prélèvements sociaux sur revenus du capital | **18,6 %** (CSG 10,6 % + CRDS 0,5 % + prélèvement de solidarité 7,5 %) | 01/01/2026 (LFSS 2026) | Vérifié (service-public F21618, màj 15/04/2026) |
| PFU (« flat tax ») | **31,4 %** = 12,8 % IR + 18,6 % PS | 01/01/2026 | Vérifié |
| Option barème progressif | IR au barème + 18,6 % PS ; abattements pour durée de détention uniquement sur titres acquis avant 2018 ; révocabilité annuelle annoncée pour les revenus 2026 | — | À vérifier dans la loi |
| PEA > 5 ans | PS seuls (18,6 % à confirmer sur PEA) ; PEA < 5 ans : 31,4 % et clôture (sauf exceptions) | — | Taux PEA à confirmer |
| Report des moins-values | 10 ans, les plus anciennes d'abord | — | Vérifié |
| Méthode de prix de revient | **PMP** (prix moyen pondéré d'acquisition, art. 150-0 D CGI ; BOFiP BOI-RPPM-PVBMI-20-10-20-40) : inchangé par une cession partielle, recalculé à chaque achat | — | Vérifié |
| TTF | 0,4 % sur achats nets fin de journée, actions FR capitalisation > 1 Md€ | 01/04/2025 | Vérifié |
| Fait générateur SRD | Règle admise : date de dénouement / règlement-livraison (une vente SRD après la liquidation de décembre est imposable l'année suivante) | — | À vérifier (BOFiP BOI-RPPM-PVBMI-30-10-10) |
| Loi de finances 2026 | CDHR (RFR ≥ 250 k€/500 k€) ; pas de hausse du PFU ; PLF 2027 en discussion à l'automne 2026 | — | Surveiller |

Tout changement = nouvelle ligne avec date d'effet, jamais une modification en place ; les calculs utilisent le paramètre en vigueur à la date du fait générateur.

## 11.2 Calculs (par année civile, `domain/tax`)

**CTO Saxo**
- PMP par instrument et par compte, recalculé à chaque achat (frais inclus dans le prix de revient), historique conservé.
- Plus/moins-value par cession = (prix de vente net de frais − PMP) × quantité ; devise ≠ EUR : conversion au cours du jour de chaque opération (achat et vente).
- Positions SRD : plus-value reconnue à la date de règlement ; les prorogations ne sont pas des cessions ; CRD et frais de prorogation traités comme frais déductibles ? → **paramètre `tax.srd_costs_deductible` avec note « à confirmer avec le courtier/IFU »** ; l'outil affiche les deux calculs.
- Agrégats YTD : plus-values brutes, moins-values de l'année, moins-values reportables (stock par année d'origine, expiration à 10 ans), plus-value nette imposable, **impôt estimé au PFU** et **simulation au barème** (TMI saisie par l'utilisateur ; affichée comme simulation indicative, jamais comme recommandation : la comparaison réelle dépend de l'ensemble des revenus du foyer, de la CSG déductible et des autres revenus de capitaux), acomptes éventuels.
- Simulation : « si je clôture cette position aujourd'hui, impact fiscal = ? » ; « vendre en perte avant le 31/12 pour compenser ? » (liste des positions en moins-value latente avec l'économie estimée, sans recommandation automatique de vente : information seulement).
- Rapprochement avec l'IFU Saxo (import en janvier) : écarts listés.

**PEA BoursoBank**
- Date d'ouverture, ancienneté, **date des 5 ans**, versements cumulés vs plafond 150 000 €, retraits, valeur liquidative, gain latent total, prélèvements sociaux estimés en cas de retrait (18,6 % × gain), taux historiques si PEA ancien (option `tax.pea_historic_rates`, à renseigner).
- Alerte si un titre du PEA perd son éligibilité (changement de siège) ou si une proposition tente de router un titre non éligible.
- **TTF dans le PEA** : la taxe sur les transactions financières (0,4 %, actions françaises > 1 Md€) s'applique aussi aux achats en PEA ; elle est comptée dans les coûts et dans la comparaison des comptes (docs/08 §8.2).

## 11.3 Page `/fiscal` et exports

Cartes : plus-value nette YTD CTO, impôt estimé (PFU / barème), moins-values reportables par année, TTF payée, CRD payée, PEA (ancienneté, 5 ans, versements restants). Tableaux : cessions de l'année (format proche du 2074), positions ouvertes avec PMP. Export CSV « cessions 2026 » et « positions au 31/12 ». Rappels : import IFU (janvier), échéance déclarative (avril–juin), fin d'année (fenêtre de compensation des moins-values).
