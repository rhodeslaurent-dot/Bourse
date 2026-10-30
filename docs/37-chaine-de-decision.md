# 37 — Chaîne de décision en quatre temps : de la détection à la préconisation explicite

> **Statut : proposition du 30/10/2026, à valider par l'utilisateur.** Le lot L0 est **construit, désactivé par défaut** (`verdict.enabled: false`, ADR-016 proposé) : il ne change rien tant que les décisions D9–D15 du §37.6 ne sont pas prises (§37.8). Les lots L1 à L5 ne sont pas codés.
>
> Demande de l'utilisateur (30/10/2026) :
> - « la démarche ne peut pas être binaire » ;
> - identifier, rechercher, analyser globalement, puis rédiger une préconisation explicite qui pèse le risque de perte contre le potentiel de gain : « je prendrai la décision finale » ;
> - « l'investissement à risque zéro n'existe pas » ;
> - « ce n'est pas possible qu'aucune opportunité ne ressorte en vert : soit la détection, soit le filtre, soit le conseil ne fonctionne pas ».

## 37.1 Pourquoi aucune opportunité n'est verte : c'est le filtre, par construction

Le scanner technique (≈ 1 300 valeurs) **ne peut produire aucun achat** aujourd'hui :
- d2 (cassure) et d7 (tendance + qualité) sont en surveillance seule (ADR-013, décision du 26/10/2026) ;
- d3 et d4 sont désactivés (ADR-014).

Seules quatre sources peuvent donner un « BUY » :
- d1 (saut + catalyseur, phase 3, mode « disponible » seulement) ;
- d5 (lettres) ;
- d6 (catalyseurs) ;
- d8 (buzz).

Sur celles-là s'empilent encore des portes binaires :
- R4 : ratio net ≥ 2 vers le **premier** objectif, ce qui place la limite ≈ 1 % sous la clôture ;
- score : ≥ 75 % de l'atteignable, 80 % en régime orange ;
- confiance ≥ 3 ;
- portail de la place ;
- gel macro ;
- plafonds.

Chaque porte se défend seule. Empilées, elles donnent zéro. **Votre diagnostic est juste : le filtre ne fonctionne pas comme outil d'aide à la décision.**

Ce que la mesure a établi reste vrai, et doit être **montré, pas caché** :
- acheter mécaniquement toutes les configurations d7 a perdu −0,137 R par trade au coût complet (2 716 trades, 2018-2026) ;
- d3 et d4 perdent même avant frais.

C'est une information de risque pour votre décision, pas une raison de ne rien présenter.

## 37.2 Principe : une évaluation graduée, une décision humaine

**Ce qui reste absolu** (CLAUDE.md, jamais négocié) :
- aucun ordre passé par l'outil ;
- donnée périmée en séance → « à vérifier », sans fiche d'ordre (règle 2) ;
- taille calculée sur le prix maximal et le stop glissé ;
- plafonds de risque ouvert et réservé ;
- instantané de décision ;
- aucune donnée inventée.

**Tout le reste devient un élément d'appréciation affiché sur la carte**, et non une porte :
- historique du détecteur ou de la source ;
- ratio vers chaque objectif ;
- score ;
- régime de marché ;
- événements à venir ;
- risque de saut d'ouverture ;
- corrélation avec le portefeuille.

**Échelle de verdict** (liste fermée, calculée en Python ; le LLM ne la fixe jamais) :

| Verdict | Couleur | Sens | Taille proposée |
|---|---|---|---|
| Conviction forte | vert foncé | source dont l'avantage est **mesuré** positif + contexte favorable + ratio ≥ 2 | pleine |
| À considérer | vert | configuration valide, risque borné, ratio ≥ 2 sur un objectif plausible ; historique neutre ou non mesuré (dit) | pleine ou ¾ |
| Spéculative | ambre | avantage faible, non mesuré ou mesuré négatif (chiffre affiché), ou risque élevé ; **reste exécutable**, étiquetée | ½ (ou ¼) |
| À surveiller | bleu | déclencheur non atteint, attente d'une clôture, données à vérifier | — |
| Écartée | gris | violation d'une règle absolue seulement : plafond, stop impossible, objectif sous le stop, donnée périmée | — |

La décision vous revient. La carte dit pourquoi elle est à ce niveau et ce qui la ferait monter ou descendre.

## 37.3 Les quatre temps, et ce qui existe déjà

| Temps | Existe | Manque |
|---|---|---|
| **1. Identification** | lettres (lecture + analyse, docs/20) ; actualité RSS classée ; momentum et configurations techniques sur ≈ 1 300 valeurs ; forums et réseaux (docs/26, docs/30) ; buzz d8 ; achats de dirigeants AMF (archive quotidienne) ; calendrier de résultats | historique daté des nouvelles ; podcasts et vidéos ; régulateurs hors France (domaines à ouvrir) |
| **2. Recherche proactive** | force relative des valeurs ; rotation par ETF (bloc M) ; zones détectées | **module à construire** (§37.4) : secteurs, configurations « en formation », catalyseurs à venir, agent chercheur, liste d'anticipation |
| **3. Analyse globale** | fiche d'opportunité unique (docs/22 §22.2) ; comité pré-mortem + président (docs/24, en observation, top 5 seulement) ; risque de saut, résultats dans l'horizon, corrélation | dossier de contexte complet pour **chaque** opportunité « à considérer » ou mieux ; avis contradictoires construits sur des faits différents |
| **4. Préconisation explicite** | thèse rédigée par le LLM sur faits numérotés (garde-fou des nombres) ; gain et perte nets par compte | format de préconisation complet (§37.5), proche de celui de votre projet Claude |

Les causes des hausses (docs/36, étape 1 en cours) alimentent les temps 1 et 2 : elles disent quelles sources méritent d'être surveillées, et avec quel poids.

## 37.4 Le module de recherche proactive (temps 2) : anticiper plutôt que réagir

**Calculs Python** (testés, horodatés, point dans le temps) :
- **Rotation sectorielle et régionale** : force relative des secteurs à 1, 3 et 6 mois, largeur (part des valeurs au-dessus de leur MM50), accélération.
- **Configurations en formation**, avant le déclencheur :
  - base qui se resserre (volatilité en contraction) ;
  - cours à moins de x % d'une résistance détectée ;
  - volume qui s'assèche puis revient ;
  - force relative à un nouveau plus haut avant le cours.
- **Catalyseurs à venir** : résultats dans 5 à 15 séances, assemblées générales, échéances connues ; achats de dirigeants récents.

**Agent IA « chercheur »** (quotidien léger, hebdomadaire approfondi) :
- **Entrée** : faits numérotés par secteur et par valeur ; flux de nouvelles classées ; lettres.
- **Sortie** : listes fermées (direction, horizon, type de déclencheur, conviction) et un texte court, sans aucun nombre absent des faits (règle 3). Pour chaque candidat :
  - la thèse ;
  - le déclencheur à attendre ;
  - le niveau qui l'invaliderait ;
  - ce qui manque pour conclure.
- **Liste d'anticipation** : une page et une alerte quand le déclencheur arrive. L'opportunité est alors déjà analysée : on gagne du temps sur le marché, sans sauter les contrôles.

**Mesure** : chaque thèse est enregistrée et suivie « en fantôme ». On saura en quelques mois si l'anticipation fait mieux que la réaction.

## 37.5 La préconisation explicite (temps 4)

Une carte par opportunité. Les chiffres sont calculés en Python ; le texte est rédigé par le LLM sur ces chiffres seulement.

1. **Verdict** (échelle du §37.2), en une phrase : « À considérer, taille ¾ : … ».
2. **Thèse** : pourquoi maintenant (sources convergentes, contexte de marché et de secteur).
3. **Scénario favorable** :
   - gain net en € et en % à l'objectif 1 puis à l'objectif final ;
   - horizon ;
   - ce qui doit se passer.
4. **Scénario défavorable** :
   - perte nette en € au stop ;
   - perte en cas de saut d'ouverture (quantile historique) ;
   - ce qui le déclencherait.
5. **Balance** : ratio vers chaque objectif. Fréquence historique de succès quand elle est **mesurée** (n, intervalle) ; « non mesurée » sinon, jamais une estimation.
6. **Risques identifiés** : événements dans l'horizon, liquidité, corrélation avec vos lignes, régime, contre-arguments du comité.
7. **Ordre proposé** :
   - type (limite ou plage), niveaux et validité ;
   - compte et taille ;
   - stop à poser dès l'exécution.
8. **Ce qui reste incertain** et **ce qui ferait changer l'avis**.

Pour aligner ce format sur celui que vous utilisez dans votre projet Claude, un exemple de préconisation que vous jugez réussie (sans montant personnel) suffirait.

## 37.6 Décisions demandées

| # | Question | Recommandation |
|---|---|---|
| D9 | Remplacer les portes non absolues par l'échelle de verdict du §37.2 | oui |
| D10 | ADR-013 : d2 et d7 passent de « surveillance seule » à « spéculative », taille ½, historique affiché | oui : risque mesuré et borné, décision humaine |
| D11 | d3 et d4 (perte démontrée **même avant frais**) | rester désactivés : ce n'est pas de l'incertitude, c'est une perte connue |
| D12 | R4 : la limite reste calculée sur l'objectif 1 pour « à considérer » ; « spéculative » peut reposer sur l'objectif final avec ratio ≥ 2, dit en clair | oui : la limite remonte près du cours, le pari est nommé |
| D13 | Ordre des lots (§37.7) | L0 → L1 → L2 → L3 → L4 |
| D14 | Exemple de préconisation de votre projet Claude | à fournir |
| D15 | Si D10 : une carte d7 « spéculative » entre par **ordre à plage** (plan n° 4 : la plage exécute les bons signaux, la limite d'attente les mauvais), stop à 2,5 ATR, **sans objectif 1 fixe** (sortie au temps ou chandelier), espérance mesurée affichée « ≈ 0 R (−0,10 à +0,08 R selon la sortie, non confirmée) » | oui, comme information de risque ; rien de cela n'est une règle validée (plans n° 4 et 6 : aucune confirmation) |

D2 (risque « élevé » → demi-taille) et D6 (double pénalité orange) de docs/35 sont absorbés par l'échelle : le régime et le risque modulent le verdict et la taille, ils ne bloquent plus.

## 37.7 Ordre de construction proposé

| Lot | Contenu | Dépend de |
|---|---|---|
| **L0** | échelle de verdict, ADR-013 bis, cartes et Telegram ; règles absolues inchangées ; tests — **construit, désactivé par défaut** (30/10/2026, §37.8) | D9 à D12, D15 |
| **L1** | causes des hausses : étape 1 (en cours), étape 2 avec les nouvelles | source de nouvelles |
| **L2** | préconisation explicite (§37.5) sur chaque carte ≥ « spéculative » | L0, D14 |
| **L3** | dossier d'analyse globale + comité étendu à ces cartes (coût estimé avant de coder, plafond journalier inchangé sauf décision) | L2 |
| **L4** | module de recherche proactive (§37.4) et liste d'anticipation | L1, L3 |
| **L5** | podcasts et vidéos (transcription), régulateurs hors France | domaines réseau, décision |

## 37.8 Lot L0 : construit, désactivé par défaut (30/10/2026)

**Rien ne change tant que vous n'activez pas l'échelle.** Groupe absent de `params.yaml`, ou `verdict.enabled: false` (valeur livrée) : le moteur enregistre exactement les mêmes propositions qu'avant (test de non-régression qui compare les deux, octet par octet).

**Voir l'effet avant de décider** (lecture seule, fonctionne échelle désactivée) :

```
uv run python -m app.cli verdict-preview --date 2026-10-30
```

La commande reprend chaque proposition enregistrée du jour (ses portes et ses détails) et affiche :
- le nombre de propositions par niveau ;
- une ligne par proposition : valeur, détecteur, action d'aujourd'hui → niveau, raison principale ;
- ce que le moteur changerait (ADR-013 levé, plage P2 estimée de d7, objectif final D12).

**Activer** (dans `config/params.yaml`, groupe `verdict`, ajouté par `params-init --update`) :

| Clé | Livré | Décision |
|---|---|---|
| `enabled` | `false` | D9 : `true` active l'échelle |
| `speculative_detectors` | `[d2_breakout, d7_trend_quality]` | D10 (`[]` = ADR-013 inchangé) |
| `speculative_size_multiplier` | 0,5 | D10 : taille ½ |
| `speculative_last_target` | `true` | D12 |
| `speculative_d7_entry` | `plage` | D15 (`limite` = R4 classique, D12 possible) |
| `speculative_stop_atr` / `speculative_plage_cap_atr` / `speculative_plage_validity_sessions` / `speculative_time_exit_sessions` | 2,5 / 0,5 / 3 / 20 | D15 : plan n° 4, cellule C1 |
| `notify_speculative` | `false` | Telegram : cartes spéculatives envoyées seulement si `true` |

**Ce que fait l'échelle activée** (ADR-016) :
- **Écartée** : règles absolues seulement — plafonds, coupe-circuit, R14 (gel maintenu, décision C du 29/10), stop impossible, taille nulle, change ou cours indisponible, emballement, ADR-013 pour un détecteur que vous y laissez.
- **À surveiller** : limite en attente, R4 non atteint sur l'objectif 1, déclencheur non franchi, cours au-dessus de la limite, pas d'objectif, donnée à vérifier (règle 2).
- **Spéculative** : éléments d'appréciation seulement, ou détecteur spéculatif. Achat exécutable à taille ½, avec la ligne « Spéculative — espérance historique … » (chiffre mesuré ou « non mesurée ») ; les éléments d'appréciation sont écrits sur la carte, pas en blocage.
- **À considérer** : l'achat d'aujourd'hui sans aucun blocage.
- **Conviction forte** : avantage mesuré positif dans un portail ouvert. **Aucune source ne l'a aujourd'hui** : ce niveau restera vide tant qu'un plan de recherche ou l'historique comparable ne l'aura pas établi.

La page Opportunités regroupe alors les cartes par niveau (vert foncé, vert, ambre, bleu, gris ; écartées repliées). Le niveau et ses raisons sont enregistrés dans `details.verdict_niveau` et `details.verdict_gradue`.
