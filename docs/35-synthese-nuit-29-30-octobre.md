# 35 — Synthèse de la nuit du 29 au 30/10/2026 : pourquoi l'outil ne proposait aucun achat, ce qui est corrigé, ce qui reste à mesurer

> Demande de l'utilisateur (29/10, soir) : « Toujours des propositions en rouge ou en orange car proposition trop tardive avec cours trop haut… Les actions remontées du Momentum en orange sans aucune analyse supplémentaire : aucun intérêt… trouve la solution ! » — travail délégué en autonomie jusqu'au 30/10 6 h (heure de La Réunion), comités multi-agents autorisés.

## 35.1 Le diagnostic en une page

Sept analyses indépendantes (quatre comités « fable », trois développeurs) convergent :

1. **La limite tombait sous le cours par construction.** R4 (ratio gain/risque net ≥ 2 vers le *premier* objectif, la résistance la plus proche) avec un stop structurel serré place le prix maximal ≈ 1 % sous la clôture du signal. Sur la page du 29/10 : écart cours → limite médian +2,4 % ; ratio au cours ≥ 2 vers l'objectif 1 dans 5 cas sur 24, vers l'objectif le plus lointain dans 14 sur 24. Part de l'écart : ≈ 60 % la règle R4 sur l'objectif 1, ≈ 40 % la détection tardive (buzz après une séance de +3 à +8 %).
2. **La limite en attente d'un repli est l'ordre « mal sélectionné » type** (simulation synthétique, étiquetée comme telle) : exécutée ≈ 1 fois sur 2, surtout sur les signaux qui échouent (−0,48 R contre +0,35 R pour ceux qu'elle rate). L'ordre à plage de déclenchement J+1 inverse ce biais — et **docs/06 §6.3 le prévoyait déjà pour les cassures** : le code ne l'appliquait pas (défaut, corrigé cette nuit).
3. **Quatre verrous empilés** rendaient l'achat structurellement impossible (audit) : détecteurs en surveillance seule (ADR-013, décision du 26/10, non touchée) ; gel macro R14 **sans historique** (74 propositions sur 74 bloquées le 29/10, ≈ 30 % des séances, et cela jusqu'en avril 2027 : seules la semaine en cours et la suivante étaient connues) ; portail « restreint » de Paris **inatteignable** (ratio après impôt comparé à 2,5, score sur 100 incluant un bloc catalyseur que la plupart des signaux ne peuvent pas avoir) ; double pénalité du régime orange et « donnée inconnue = 0 point ».
4. **La lecture des lettres était fausse.** Des codes boursiers lus dans des mots français (« sur », « aux », « pas », « mon », « lien »…) : 345 fausses lectures depuis le 20/10, 41 propositions et 17 lignes de watchlist fausses ; les vraies valeurs non reconnues (« Atos Group », « Sartorius Stedim Biotech »…) ; l'IA ne renvoyait aucun niveau (consigne v1) ; les noms de niveaux n'étaient jamais ceux que le moteur lit.
5. **Le fond, dit honnêtement** (comité « sources de gain », littérature à l'appui) : les déclencheurs **techniques seuls** ne gagnent rien après frais à 2–20 séances (cohérent avec l'enregistrement n° 1, 195 cellules sans résultat). Ce qui résiste, ce sont les **événements d'information** — dérive après résultats, achats de dirigeants, chocs de cours avec nouvelle classée — avec un gain modeste (+0,1 à +0,3 R brut), **seulement en CTO** (le PEA Découverte, ≈ 0,2 R l'aller-retour, ne convient pas aux allers-retours de moins de 20 séances). Mieux entrer est nécessaire, mais ne crée pas d'avantage à lui seul.

## 35.2 Ce qui est corrigé (poussé, tests au vert)

| Domaine | Correction |
|---|---|
| Frais hors euro | risque par titre converti en euros dans l'estimation des frais (la commission minimale pesait jusqu'à 40 % en pence, couronnes, zlotys) ; une limite au niveau du stop n'est plus un ordre ; niveaux affichés dans la devise de cotation |
| Lettres | repérage par noms de sociétés normalisés seulement (jamais un code ni un mot courant) ; noms de l'IA résolus sur le nom réduit ; niveaux en liste fermée (« entrée » → achat, « invalidation » → stop, déclencheur) ; consigne v2 ; **chaque valeur recommandée analysée par l'outil** : carte en trois blocs (ce que dit la lettre / ce que voit l'outil / verdict + liste de contrôle), deux stops toujours affichés, entonnoir par lettre, même phrase sur la carte, la page Lettres, Telegram |
| Cassures | jamais de limite en attente sous un déclencheur (lettres et détecteurs) ; **ordre à plage de déclenchement J+1** de docs/06 §6.3 appliqué (seuil = déclencheur, limite = prix maximal, valable `entry.confirm_sessions` séances de la place, taille sur la limite et le stop glissé) ; cours déjà au-dessus de la limite : « ne pas courir après le prix », aucun ordre |
| Verrous | portail restreint sur le ratio **net de frais** (R4) ; risque réservé des ordres en attente compté (règle 7) ; plafonds sur le capital de l'interface ; jour d'annonce macro transmis au score ; exposition aux résultats **mesurée** sur l'historique ; chargeur d'historique des annonces macro (`macro-history-load`) ; données manquantes affichées sur la carte ; `gates-coverage` |
| Page Opportunités | cartes qui ne peuvent pas devenir un ordre repliées « Pour information » ; mention « sorties non validées par la recherche » |
| Nettoyage | `mentions-recheck --apply` (fausses lectures retirées, réversible), `letters-trace --ia`, `letters-reread` |
| Revue critique indépendante (fin de nuit) | R5 de nouveau testé sur le cours réel d'une cassure (un cours sous l'obstacle ou sous le stop ne donne plus d'ordre à plage) ; « Ordre saisi » réserve le risque de la proposition (plafonds, page Risque) ; analyse ciblée en séance sans cours frais → « à vérifier » (réception API, revue manuelle), jamais de fiche d'achat Telegram pour une proposition « à vérifier » ; fiche au fixing : jamais une cassure de lettre non franchie ; franchissement en séance du déclencheur d'une lettre = alerte, ordre après une clôture au-dessus ; libellés du verdict (docs/15, ligne du 30/10) |

## 35.3 Ce qui est mesuré sur le PC (`scripts\matin.ps1`)

Plans **gelés avant toute donnée** (empreintes dans docs/34) : n° 4 politiques d'entrée (§34.11, `86ac714f…`), n° 5 achats de dirigeants (§34.12, `5d1e4a1d…`), n° 6 règles de sortie (§34.13, `cdfe9660…`). Puis, dans l'ordre : historique macro ; couverture des verrous ; étude historique des lettres (docs/20 §20.11 : les valeurs recommandées montent-elles après frais, contre 20 valeurs tirées au hasard le même jour) ; rejeu des propositions réellement faites ; rejeu des politiques d'entrée 2017–2026 (docs/34 §34.10, **plusieurs heures** : à laisser tourner) ; calibration et exécution des plans n° 4 puis n° 6. Option `-Dirigeants` : historique AMF depuis 11/2021 (long), préalable au plan n° 5.

Sur le PC, dans l'ordre : `git pull` ; `uv run python -m app.cli params-init --update --email <votre adresse>` (ajoute les groupes `letters`, `entry`, `market_gate`, `scoring`, `events`) ; `docker compose up -d --build` ; puis `powershell -ExecutionPolicy Bypass -File scripts\matin.ps1` (ajouter `-Dirigeants` pour l'historique AMF). Une étape en échec n'arrête pas les suivantes ; le résumé final liste celles à relancer.

## 35.4 Décisions qui reviennent à l'utilisateur

| # | Question | Défaut actuel | Recommandation |
|---|---|---|---|
| D1 | Portail restreint : base du score (`market_gate.restricted_score_basis`) | `total` (/100, catalyseur compris) | `admission_ratio` ≥ 0,85 : exigeant sans être inatteignable |
| D2 | Risque « élevé » (`scoring.high_risk_policy`) | `watch` | `half_size` (taille × 0,5, stop ≤ 1,5 ADR), comme docs/07 §7.2 |
| D3 | Stop des lettres (`letters.stop_policy`) | `letter_within_max_else_structure` (déjà actif) | garder : les deux stops sont affichés ; à mesurer par l'étude des lettres |
| D4 | Lettre sans niveau d'entrée : entrée au cours (mode `marche`) | actif | garder, carte explicite |
| D5 | Allers-retours courts (≤ 10–20 séances) routés vers le CTO | non | oui : les frais du PEA Découverte mangent l'avantage |
| D6 | Double pénalité du régime orange | inchangée | décider après `gates-coverage` |
| D7 | Régulateurs lot 1 (Suède, Allemagne, Pays-Bas, Belgique) | en attente | ouvrir les domaines réseau de la session |
| D8 | Mécanique d'entrée et sorties (R4-bis) | inchangées | seulement après les verdicts des plans n° 4 et n° 6 (ADR, demi-risque, suivi fantôme) |

## 35.5 Le chemin le plus court vers ~4 ordres validés par mois

1. **Dérive après résultats** (plan n° 3, en cours sur le PC) : ≈ 40–60 signaux par mois sur ~1 300 valeurs européennes ; s'il passe, détecteur t14 entrée à l'ouverture, CTO hors France, demi-risque et suivi fantôme d'abord.
2. **Achats de dirigeants** (plan n° 5) : historique AMF à charger, verdict au mieux un suivi fantôme, puis confirmation sur 12 mois.
3. **Lettres** : l'étude dira si elles battent le hasard ; dès maintenant, chaque recommandation est analysée et expliquée.
4. **Entrée et sorties** (plans n° 4 et n° 6) : rendent exécutables les signaux qui ont un avantage, sans en créer.

Tant qu'aucun plan n'a passé ses seuils, l'outil n'invente pas d'achat : il dit pourquoi, valeur par valeur.
