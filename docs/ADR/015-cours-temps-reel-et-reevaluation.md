# ADR-015 — Cours en pseudo temps réel et réévaluation intraday des propositions

- **Statut** : **accepté le 28/10/2026** (demande de l'utilisateur : « ok pour 15 s Saxo si cela n'entraîne pas de coûts supplémentaires », EODHD à 15 min pour l'affichage, bouton de rafraîchissement, analyses relancées à la même fréquence).
- **Contexte** : les cours des pages Opportunités et Watchlist venaient de l'instantané du matin ou de la dernière clôture. Une limite « en attente d'un repli » pouvait devenir atteignable dans la journée sans que l'outil le montre. Les appels Saxo OpenAPI sont gratuits. Sans abonnement Euronext L1, ils renvoient des cours différés de 15 min ou aucun cours. EODHD REST compte un appel par valeur.

## Décision

1. Un service de cours d'affichage (`app/services/live_quotes.py`) garde en mémoire le dernier cours de chaque valeur. Saxo est relevé au plus toutes les `live_quotes.saxo_seconds` ; le Uic est cherché par ISIN sur `ref/v1/instruments`, lecture seule, cotation de la place de la valeur, jamais deviné. EODHD est relevé au plus toutes les `live_quotes.eodhd_minutes`, ou à la demande (`live_quotes.manual_min_seconds`), dans un budget quotidien (`live_quotes.eodhd_daily_calls_max`). Rien n'est relevé hors séance de la place.
2. Ces cours servent à l'affichage et à la réévaluation. Ils n'entrent jamais dans un instantané de décision (règle 10).
3. Le job `intraday_reeval` (toutes les 15 s, fenêtre 09:00–21:59 Paris, séance vérifiée par place) recalcule l'admission par `admit()` sur les valeurs enregistrées de la proposition, avec le prix maximal enregistré comme plafond. Seuls les changements d'état sont stockés (`proposal_reevaluations`, migration 0028). Une entrée n'est actionnable qu'avec un cours temps réel frais (règle 2).
4. Une cadence inférieure à la minute reste dans la fenêtre et les jours du job (`second=*/n` du déclencheur cron). Avant, un `every_seconds` < 60 tournait jour et nuit.

## Conséquences

- Coût : aucun abonnement ni appel payant de plus ; EODHD est borné par le budget d'affichage.
- Volume : une ligne `jobs_runs` toutes les 15 s pendant la fenêtre, soit environ 3 100 par jour de bourse.
- Le cache vit dans le processus (application et planificateur partagent le même) : un redémarrage remet le budget EODHD du jour à zéro.
- Sans abonnement L1, aucune entrée n'est actionnable en séance : les transitions sont enregistrées « à vérifier : cours différé ». La configuration dégradée est signalée dans tous les cas.
- Annulation : `live_quotes.enabled: false` et `jobs.intraday_reeval.enabled: false`.
