# ADR-003 — Sous-lot 1A : comptes, états d'exécution/protection, rapprochement

Statut : **accepté (provisoire)** · Date : 2026-09-15

## Décisions

1. **Positions reconstruites depuis les exécutions** (`trades`) : la ligne `positions` est une projection (quantité détenue, PMP frais inclus, quantité couverte, états) recalculée après chaque déclaration, import ou stop. Une déclaration rapprochée ne compte plus dans la quantité : la ligne confirmée (prix et frais courtier) fait foi.
2. **Exécutions Saxo dérivées des positions ouvertes** (`PositionId` → `broker_fill_id`, `OpenPrice`, `ExecutionTimeOpen`) et des positions fermées à venir ; c'est suffisant pour A1.1 (positions/cash/ordres identiques à l'interface). Un endpoint d'historique d'exécutions plus fin sera évalué en qualification.
3. **Stops courtier** (`OpenOrderType ∈ {Stop, StopLimit, TrailingStop}`) confirment ou créent une ligne `stops` avec `broker_order_id`, statut `active`, quantité couverte = quantité − exécutée.
4. **Correspondance ISIN ↔ symboles** provisoire dans `config/instruments_map.yaml` (sous-lot C livre le référentiel `instruments`).
5. **Cours du moniteur en phase 1** : EODHD REST (différé) faute de flux temps réel qualifié ; le statut (`delayed`) est affiché dans l'alerte et sur `/positions`. Le S1 réagit sur ce cours différé (prudent : une alerte différée vaut mieux qu'aucune) ; le flux temps réel arrive en phase 3.
6. **Alertes** : `AlertService` persiste chaque événement (envoyé ou différé selon le mode), dédup P2/P3 par (kind, isin, compte, jour), P1 par incident (`unprotected:<position>`, `stop_crossed:<position>`) avec rappel toutes les `notifications.p1_repeat_minutes` ; un incident est clos (`seen_at`) quand sa condition disparaît.
7. **Telegram** : déclarations par commandes (`/exec`, `/stop`, `/ordre`) ; les boutons des alertes rappellent la commande. Un formulaire guidé par boutons (conversation) est reporté en phase 2.
8. **Garde-fou « aucun endpoint de trading »** : la regex ne vise que les appels HTTP sortants ; nos routes entrantes `POST /api/orders` (déclaration « ordre saisi ») sont exclues explicitement.
