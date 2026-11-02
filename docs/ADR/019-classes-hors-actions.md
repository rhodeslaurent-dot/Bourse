# ADR-019 — Obligations, or, matières premières et suivi de tendance : rester hors de l'outil

- **Statut** : **proposé** le 02/11/2026 (lot M0 de docs/39) ; décision D6 de l'utilisateur attendue. Aucun code.
- **Contexte** : l'utilisateur a demandé les meilleures méthodes sur toutes les classes d'actifs (docs/39). docs/00 limite l'univers aux actions des places PEA (dérivés hors périmètre, États-Unis en phase 6). La recherche a évalué les obligations, l'or, les matières premières, le suivi de tendance et les règles de rotation (Faber, double momentum, parité de risque).
- **Constats** (docs/39 §2 et §4, sources citées là-bas) :
  1. Ces classes et ces règles **réduisent le risque** ; aucune n'a montré un **gain net** supérieur à un cœur d'actions mondiales pour un investisseur sans levier, une fois les frais, l'impôt au CTO et la décroissance après publication comptés.
  2. Les règles de timing (moyenne mobile 10 mois, double momentum) **vendent le cœur après une baisse**, ce que docs/39 §5 interdit.
  3. Aucune n'est logeable au PEA (obligations, ETC) ; au CTO, chaque arbitrage est imposé au PFU.
- **Décision proposée** : ne pas les intégrer. La réserve de sécurité reste hors de l'outil (livrets, fonds euros).
- **Ce qu'une extension exigerait** (si l'utilisateur répond non à D6) :
  - le chargement des ETC et ETN (`app/scheduler/jobs/universe_refresh.py`, aujourd'hui actions et ETF seulement) ;
  - l'extension du banc de recherche au-delà de `kind == "stock"` (`app/services/research.py`) et une mesure de portefeuille en % par an (le banc mesure en R par trade) ;
  - des indices de rendement total ;
  - un plan pré-enregistré à 1 ou 2 paramètres, comparé à l'achat-conservation de l'ETF Monde, avant tout affichage ;
  - un ADR accepté par l'utilisateur.
