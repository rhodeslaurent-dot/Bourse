# Fiches de qualification des fournisseurs (docs/04 §4.3 bis)

Deux temps : **pré-qualification documentaire** (avant tout code, ici), puis **qualification par prototype**
(`scripts/qualify_providers.py`, 3 séances, A0.7) qui complète chaque fiche avec les mesures. Décision : ADR-002.

Règles rappelées : un indicateur ne mélange jamais deux périmètres (RVOL numérateur et dénominateur sur le même
périmètre) ; aucun changement de source silencieux (`source_switched` + P4) ; un trou du flux temps réel est marqué,
jamais rebouché avec l'API historique intraday (différée, finalisée après clôture).
