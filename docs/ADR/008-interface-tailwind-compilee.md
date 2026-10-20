# ADR-008 — Interface : Tailwind compilé en local, sans CDN ni HTMX

Statut : **accepté** · Date : 2026-10-21

## Contexte

docs/03 et `CLAUDE.md` recommandaient « Jinja2 + HTMX + Tailwind (CDN) ». Après la première journée d'exploitation, l'interface a été jugée austère et peu lisible. Sa refonte a mis en évidence deux fragilités de l'approche CDN : chaque affichage dépend d'un script tiers chargé à la volée (pas de rendu si le CDN est inaccessible, ce qui est arrivé dans l'environnement de test), et le script « Play CDN » de Tailwind est explicitement réservé au développement par son éditeur.

## Décision

1. **Tailwind v3 compilé** : `tailwind.config.js` + `app/web/static/src.css` → `app/web/static/app.css` (minifié, ~22 Ko, **versionné**), servi par l'application sur `/static/app.css`. Aucun script tiers au chargement ; seule la police Inter est chargée depuis Google Fonts avec repli système.
2. Recompilation après toute modification de gabarit : `scripts/build_css.sh` (binaire autonome `scripts/tailwindcss` ou `npx tailwindcss@3.4.17`) ou `scripts/build_css.ps1` sous Windows. Un test vérifie que la feuille est servie et contient les composants attendus.
3. **HTMX retiré** : aucun gabarit ne l'utilisait ; il sera réintroduit si une interaction partielle le justifie.
4. Système d'interface partagé dans `app/web/templates/_ui.html` (macros `badge`, `dot`, `tile`, `bar`, `empty`, `field`, `select`, `button`) : couleurs d'état uniformes (vert / ambre / rouge / bleu / gris), mode sombre automatique (`prefers-color-scheme`), navigation haute sur ordinateur et barre basse sur mobile, chiffres tabulaires, filtres Jinja `eur` et `pct`.

## Conséquences

- Les pages restent lisibles hors ligne et derrière Cloudflare Access sans dépendre d'un CDN.
- Oublier de recompiler après un changement de classes laisse une page partiellement stylée : le RUNBOOK §6 le rappelle.
- Les textes porteurs de sens (« BLOQUÉ », « NON PROTÉGÉS », « RISQUE INCOMPLET », « à vérifier ») restent présents tels quels : les tests de pages les exigent.
