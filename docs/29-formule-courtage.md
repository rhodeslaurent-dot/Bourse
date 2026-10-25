# 29 — Formule de courtage BoursoBank : coût réel et préconisation

> Demande de l'utilisateur (25/10/2026) : vérifier les frais du PEA en formule **Découverte** et prévoir l'analyse — selon le nombre d'achats et de reventes, une formule Classic, Trader ou Ultimate Trader peut devenir plus intéressante.

## 29.1 Grille encodée (à vérifier sur la brochure officielle)

Relevée le 25/10/2026 sur des comparateurs ; le site BoursoBank n'est pas joignable depuis le serveur de développement. Ordres sur Euronext :

| Formule | Frais par ordre | Conditions | PEA |
|---|---|---|---|
| **Découverte** (actuelle) | 1,99 € jusqu'à 500 €, puis 0,60 % | aucune | oui |
| Classic | 5,50 € jusqu'à 1 000 €, puis 0,48 % (minimum 8,95 €) | — | à vérifier |
| Trader | 16,65 € jusqu'à 7 750 €, puis 0,22 % | 5,95 € le mois sans ordre | oui |
| Ultimate Trader | 9,90 € jusqu'à 10 000 €, puis 0,12 % | 119 € le mois si moins de 30 ordres | oui |

**Plafond réglementaire du PEA** : les frais d'un ordre passé en ligne sont plafonnés à **0,5 %** du montant (`brokers.boursobank.pea_cap_pct`). En Découverte, un ordre PEA de 5 000 € coûte donc 25 € (0,60 % plafonné à 0,50 %), soit **≈ 1 % aller-retour**.

## 29.2 Effet sur les propositions

Le coût complet des propositions PEA utilise désormais la grille de la formule actuelle (`brokers.boursobank.current_formula`), au lieu de frais « inconnus » comptés à zéro. Conséquence : à rendement égal et sans avantage fiscal connu (date d'ouverture du PEA non renseignée), le CTO Saxo est souvent préféré.

## 29.3 Analyse et préconisation

- Base : vos **ordres réels sur le PEA** des 3 derniers mois ; avec moins de 6 ordres, une **projection** sur les achats PEA proposés par le moteur sur 30 jours (la moitié supposée prise, chacun = un achat et une vente).
- Pour chaque formule : frais par ordre type, coût moyen par mois avec ses conditions mensuelles ; préconisation si une formule fait gagner au moins `plan_review.min_saving_eur` (5 €) par mois.
- Ordres de grandeur avec la grille ci-dessus (ordres de 5 000 €) : 6 ordres par mois → Découverte 150 €, Trader 100 €, Ultimate Trader 178 € (moins de 30 ordres) ; Trader devient intéressant dès un ordre par mois **au-dessus d'environ 3 330 €** (en dessous, les deux formules sont au plafond de 0,5 %). Ultimate Trader seulement au-delà d'environ 30 ordres par mois.
- Affichage : `/performance` et `/apprentissage` (« Formule de courtage BoursoBank ») ; commande `broker-plan` ; **une alerte P3 par mois** (première semaine) quand une autre formule coûterait moins cher.
