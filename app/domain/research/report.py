# ruff: noqa: E501
"""Aggregated report of a registration run (docs/31 §4.3): statistics only — no price, no instrument (EODHD
data must not be redistributed). Every row with fewer than ``report_min_n`` trades is suppressed, at any depth
(cells, splits, folds, regimes, decomposition, stage-1 rows…); no key may be price-like and no string may
look like an ISIN. Pure."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.domain.research.prereg import Prereg

FORBIDDEN_KEYS = frozenset({"entry", "exit", "stop", "target", "open", "high", "low", "close", "isin", "name"})
HEADLINE_NONE = "AUCUN DÉCLENCHEUR RETENU"
CELL_KEYS_C = ("id", "n", "mean_r", "median_r", "hit_rate", "profit_factor", "mean_eur_300", "mean_excess",
               "t_week", "p_romano_wolf", "p_holm", "q_bh", "gate", "semaines", "mois", "duree_mediane")  # fmt: skip


# an ISIN anywhere in a key or a value (2 letters, 9 alphanumerics, 1 check digit, as a whole token)
ISIN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}[A-Z0-9]{9}[0-9](?![A-Za-z0-9])")
# labels kept on a suppressed row (no statistic)
SUPPRESSED_KEEP = ("id", "famille", "declencheur", "h", "niveau", "signaux", "annee", "variante", "periode")


class ReportError(ValueError):
    pass


def _no_isin(text: str, path: str) -> None:
    if ISIN_RE.search(text):
        raise ReportError(f"identifiant d'instrument (ISIN) dans le rapport : {path}")


def assert_no_price_fields(report: Any, path: str = "") -> None:
    """Refuse any price-like or instrument-identifying key, and any ISIN-shaped key or string value, at any
    depth."""
    if isinstance(report, Mapping):
        for k, v in report.items():
            if str(k).lower() in FORBIDDEN_KEYS:
                raise ReportError(f"champ interdit dans le rapport : {path}/{k}")
            _no_isin(str(k), f"{path}/{k}")
            assert_no_price_fields(v, f"{path}/{k}")
    elif isinstance(report, list | tuple | set | frozenset):
        for i, v in enumerate(report):
            assert_no_price_fields(v, f"{path}[{i}]")
    elif isinstance(report, str):
        _no_isin(report, path)


def _public(d: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if not str(k).startswith("_")}


def _small(row: Mapping[str, Any], min_n: int) -> bool:
    n = row.get("n")
    return isinstance(n, int) and not isinstance(n, bool) and n < min_n


def _suppressed(row: Mapping[str, Any], min_n: int) -> dict[str, Any]:
    keep = {k: row[k] for k in SUPPRESSED_KEEP if k in row}
    keep.update(n=row["n"], supprime=f"moins de {min_n} trades")
    return keep


def suppress_deep(x: Any, min_n: int) -> Any:
    """Every mapping carrying an integer ``n`` below ``min_n`` is replaced by its labels and ``n``, at any
    depth (a large cell keeps its rows; its small splits, folds or regimes are masked one by one)."""
    if isinstance(x, Mapping):
        if _small(x, min_n):
            return _suppressed(x, min_n)
        return {k: suppress_deep(v, min_n) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [suppress_deep(v, min_n) for v in x]
    return x


def suppress(row: Mapping[str, Any], min_n: int) -> dict[str, Any]:
    if _small(row, min_n):
        return _suppressed(row, min_n)
    out: dict[str, Any] = suppress_deep(_public(row), min_n)
    return out


def build_report(prereg: Prereg, results: Mapping[str, Any], header: Mapping[str, Any]) -> dict[str, Any]:
    th = prereg.thresholds
    min_n = int(th["report_min_n"])
    cells: Mapping[str, dict[str, Any]] = results.get("cellules", {})
    rows = {cid: suppress(c, min_n) for cid, c in cells.items()}
    cands = list(results.get("candidats", []))
    headline = (
        HEADLINE_NONE if not cands else f"{len(cands)} DÉCLENCHEUR(S) CANDIDAT(S) — À CONFIRMER SUR LA PÉRIODE SCELLÉE"
    )
    by_family: dict[str, list[dict[str, Any]]] = {}
    for cid, c in cells.items():
        by_family.setdefault(str(c.get("famille")), []).append({k: rows[cid].get(k) for k in CELL_KEYS_C if k in rows[cid]}
                                                                if "supprime" not in rows[cid] else rows[cid])  # fmt: skip

    def pick(keys: tuple[str, ...]) -> dict[str, Any]:
        return {cid: {k: r.get(k) for k in keys if k in r} for cid, r in rows.items() if "supprime" not in r}

    f0 = {k: (suppress(v, min_n) if isinstance(v, Mapping) else v) for k, v in (results.get("f0") or {}).items()}
    cal = header.get("calibration_verdict")
    if cal is not None and cal != "VALIDE":  # a report without a valid calibration is not exploitable
        headline = f"INVALIDE (calibration non valide) — {headline}"
    report: dict[str, Any] = {
        "entete": {
            **dict(header),
            "plan": prereg.id,
            "plan_sha256": prereg.sha256,
            "ensemble_cellules_sha256": prereg.cell_set_hash,
            "cellules_enregistrees": prereg.counts(),
            "cellules_executees": results.get("comptes_cellules", {}),
            "effet_minimal_detectable": {
                k: v.get("effet_minimal_detectable") for k, v in (results.get("familles") or {}).items()
            },
            "puissance_effet_plante": {
                k: {
                    "valeurs": v.get("puissance"),
                    "seuil_t": v.get("seuil_t_puissance"),
                    "nature": v.get("puissance_nature"),
                }
                for k, v in (results.get("familles") or {}).items()
            },  # fmt: skip
            "romano_wolf": results.get("romano_wolf", {}),
            "lignes_portefeuille": results.get("lignes_portefeuille"),
            "avertissements": list(results.get("avertissements", [])),
            "segment": results.get("segment"),
            "annees_dividendes_ecartees": results.get("annees_dividendes_ecartees"),
            "qualite_donnees": results.get("qualite_donnees", {}),
        },
        "A_resume": {
            "titre": headline,
            "candidats": cands,
            "surveillance": list(results.get("surveillance", [])),
            "verdicts_familles": results.get("verdicts_familles", {}),
            "confirmatoires": {
                cid: {k: rows[cid].get(k) for k in ("n", "mean_r", "mean_excess", "t_week", "p_holm", "niveau")}
                for cid, c in cells.items()
                if c.get("tier") == "confirmatory"
            },  # fmt: skip
        },
        "B_diagnostics_run2": {
            "decomposition": results.get("run2_decomposition", {}),
            "detecteurs_existants_walk_forward": results.get("detecteurs_existants", {}),
            "cellules_f0": f0,
        },
        "C_etape0_par_famille": by_family,
        "D_controles_et_references": {
            "cellules": pick(
                (
                    "mean_excess_ctrl",
                    "excess_ctrl_ic",
                    "mean_excess_ew",
                    "selection_adverse",
                    "p_permutation",
                    "executes",
                    "non_executes",
                    "annules",
                    "issue_inconnue",
                    "operations_sur_titres",
                    "signaux_masques_ost",
                    "exclus_dividendes_douteux",
                    "sorties_cotation_interrompue",
                )
            ),  # fmt: skip
            "references_imbriquees": results.get("references", {}),
            "familles": results.get("familles", {}),
        },
        "E_etape1_sorties": results.get("etape1", {}),
        "F_robustesse": pick(
            (
                "splits",
                "regles_parcours",
                "ambigu",
                "regime",
                "plis",
                "oos",
                "oos_sous_ensemble_cellule",
                "plis_sous_ensemble_cellule",
                "plis_positifs_sous_ensemble_cellule",
                "walk_forward_famille",
                "inference",
                "lag_romano_wolf_semaines",
                "t_week_controle",
                "design_effect",
                "se_grappes_mois",
                "se_grappes_mois_valeur",
                "dsr",
                "pbo_famille",
            )
        ),  # fmt: skip
        "G_couts_et_comptes": pick(
            (
                "mean_r_brut",
                "cout_moyen_r",
                "mean_r_stress",
                "multiplicateur_couts_equilibre",
                "pea",
                "srd",
                "mean_pct_notionnel",
                "non_financables",
            )
        ),  # fmt: skip
        "H_survie_dividendes_capacite": pick(("survie", "dividendes", "capacite", "concentration")),
        "I_classement": {
            cid: {"niveau": c.get("niveau"), "criteres": c.get("criteres", {})} for cid, c in cells.items()
        },
        "J_portefeuille": results.get("etape2", {}),
        "K_limites": list(header.get("limites", [])),
    }
    report = suppress_deep(report, min_n)
    assert_no_price_fields(report)
    return report


def _fmt(x: Any, nd: int = 3) -> str:
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "oui" if x else "non"
    if isinstance(x, float):
        return f"{x:+.{nd}f}"
    return str(x)


def render_fr(report: Mapping[str, Any]) -> str:
    h = report["entete"]
    a = report["A_resume"]
    lines = [
        f"RECHERCHE DE DÉCLENCHEURS — {h.get('plan')} ({h.get('segment')})",
        "",
        f">>> {a['titre']}",
        "",
        "En-tête",
        f"  données : {h.get('debut_donnees')} → {h.get('fin_donnees')} ; univers : {h.get('univers')}",
        f"  instruments couverts : {h.get('instruments')} ; inactifs ou sans cours depuis 30 jours : {h.get('radies_ou_inactifs')}",
        f"  calibration (rejeu du run 2, même plan et même run, couvrant la fin des données) : {h.get('calibration')}",
        f"  jours au taux de change statique du plan (avant le premier taux BCE) : {h.get('jours_devise_taux_statique')}",
        f"  plan sha256 : {h.get('plan_sha256')}",
        f"  cellules enregistrées : {h.get('cellules_enregistrees')}",
        f"  cellules exécutées : {h.get('cellules_executees')}",
        f"  durée : {h.get('duree_s')} s",
        f"  années-valeurs aux dividendes écartés (trades exclus) : {h.get('annees_dividendes_ecartees')}",
        f"  qualité des données (millésimes neutralisés, dividendes rejetés, jours de sauts de masse, OST, "
        f"observations masquées après OST) : {h.get('qualite_donnees')}",
        f"  Romano–Wolf (bloc en semaines, t max critique à 5 %) : {(h.get('romano_wolf') or {}).get('bloc_semaines')}, "
        f"{(h.get('romano_wolf') or {}).get('t_max_critique_5pct')} ; lignes du portefeuille : {h.get('lignes_portefeuille')}",
        "  puissance de l'effet planté : borne supérieure (A2 et A5 ne sont pas simulés)",
        *[f"  AVERTISSEMENT : {w}" for w in h.get("avertissements") or []],
        "",
        "A — Résumé",
        f"  candidats : {', '.join(a['candidats']) or 'aucun'}",
        f"  liste de surveillance : {', '.join(a['surveillance']) or 'aucune'}",
    ]
    for fam, v in (a.get("verdicts_familles") or {}).items():
        lines.append(f"  famille {fam} : {v}")
    lines += ["", "  Hypothèses confirmatoires (Holm sur 8)"]
    for cid, c in (a.get("confirmatoires") or {}).items():
        lines.append(f"    {cid:<4} n {_fmt(c.get('n'))} R moyen {_fmt(c.get('mean_r'))} excès {_fmt(c.get('mean_excess'))} "
                     f"t {_fmt(c.get('t_week'), 2)} p Holm {_fmt(c.get('p_holm'), 4)} → {c.get('niveau')}")  # fmt: skip
    b = report["B_diagnostics_run2"]
    lines += ["", "B — Diagnostics de la chaîne de production (run 2)"]
    for det, v in (b.get("detecteurs_existants_walk_forward") or {}).items():
        lines.append(f"  {det} : R net hors échantillon {_fmt(v.get('r_net_moyen_oos'))} IC {v.get('ic')} "
                     f"{'→ ADR désactivation ou refonte' if v.get('adr_requis') else ''}")  # fmt: skip
    for det, v in (b.get("decomposition") or {}).items():
        lines.append(f"  {det} : n {v.get('n')} brut {_fmt(v.get('r_brut_moyen'))} net {_fmt(v.get('r_net_moyen'))} "
                     f"coûts {_fmt(v.get('cout_moyen_r'))} ; perdants passés par +1 R {v.get('perdants_passes_par_1r')}")  # fmt: skip
    core_keys = ("n", "difference_moyenne", "exces_moyen", "r_moyen", "ic", "t_semaine", "supprime")
    f0 = b.get("cellules_f0") or {}
    for cid, v in f0.items():
        if cid.startswith("_") or cid == "periode_scellee_run2":
            continue
        if isinstance(v, Mapping):
            lines.append(f"  {cid} : { {k: v.get(k) for k in core_keys if k in v} }")
    sealed = f0.get("periode_scellee_run2")
    if isinstance(sealed, Mapping):
        lines.append(f"  [{sealed.get('libelle')}] trades du run 2 : {sealed.get('trades_run2')}")
        for cid, v in (sealed.get("cellules") or {}).items():
            if isinstance(v, Mapping):
                lines.append(f"    {cid} (scellée) : { {k: v.get(k) for k in core_keys if k in v} }")
    lines += ["", "C — Étape 0 par famille (n, R moyen, excès, t semaine, p Romano–Wolf, q BH, porte)"]
    for fam, rows in report["C_etape0_par_famille"].items():
        lines.append(f"  [{fam}]")
        for r in sorted(rows, key=lambda r: -(r.get("t_week") or -99)):
            if "supprime" in r:
                lines.append(f"    {r.get('id')} : {r['supprime']} (n {r.get('n')})")
                continue
            lines.append(f"    {r.get('id')} : n {r.get('n')} R {_fmt(r.get('mean_r'))} excès {_fmt(r.get('mean_excess'))} "
                         f"t {_fmt(r.get('t_week'), 2)} pRW {_fmt(r.get('p_romano_wolf'), 3)} q {_fmt(r.get('q_bh'), 3)} "
                         f"porte {_fmt(r.get('gate'))}")  # fmt: skip
    lines += ["", "E — Étape 1 (sorties, ΔR appariés)"]
    for cid, ex in (report.get("E_etape1_sorties") or {}).items():
        for code, v in ex.items():
            lines.append(f"  {cid} {code} : ΔR hors échantillon {_fmt(v.get('delta_r_oos'))} IC {v.get('delta_r_oos_ic')} "
                         f"Δ€ {_fmt(v.get('delta_eur_300'), 1)} → {'retenue' if v.get('retenue') else 'rejetée'}")  # fmt: skip
    if not report.get("E_etape1_sorties"):
        lines.append("  aucune (aucun déclencheur n'a passé la porte de l'étape 0)")
    lines += ["", "I — Classement A1–A13"]
    for cid, v in report["I_classement"].items():
        if v.get("niveau") in ("CANDIDAT", "SURVEILLANCE"):
            ok = [k for k, x in (v.get("criteres") or {}).items() if x]
            lines.append(f"  {cid} : {v['niveau']} (critères remplis : {', '.join(ok)})")
    rej = sum(1 for v in report["I_classement"].values() if v.get("niveau") == "REJET")
    lines.append(f"  {rej} cellule(s) rejetée(s)")
    lines += ["", "J — Portefeuille (6 lignes) contre les contrôles"]
    for cid, v in (report.get("J_portefeuille") or {}).items():
        lines.append(f"  {cid} : recul max {v['cellule'].get('max_drawdown_r')} R contre {v['reference_controles'].get('max_drawdown_r')} R → "
                     f"{'ok' if v.get('ok') else 'non'}")  # fmt: skip
    lines += ["", "K — Limites"] + [f"  - {x}" for x in report.get("K_limites", [])]
    return "\n".join(lines) + "\n"


EXPLAIN_SECTIONS = (
    ("D_controles_et_references", ("mean_excess_ctrl", "excess_ctrl_ic", "p_permutation", "selection_adverse")),
    ("F_robustesse", ("oos", "plis", "walk_forward_famille", "regime", "dsr", "pbo_famille", "splits")),
    ("G_couts_et_comptes", ("mean_r_brut", "cout_moyen_r", "mean_r_stress", "multiplicateur_couts_equilibre",
                            "mean_pct_notionnel", "pea", "srd")),
    ("H_survie_dividendes_capacite", ("survie", "dividendes", "capacite", "concentration")),
)  # fmt: skip


def _compact(x: Any, width: int) -> str:
    import json

    text = json.dumps(x, ensure_ascii=False, separators=(",", ":"), default=str)
    return text if len(text) <= width else text[: width - 1] + "…"


def explain(report: Mapping[str, Any], top: int = 8, width: int = 360) -> str:
    """``research explain``: for the ``top`` cells by weekly t, the criteria A1–A13 not met and the statistics
    behind them (controls, robustness, costs, survival, concentration), compact — what the .txt leaves out.
    Aggregates only, like the report itself."""
    rows = [r for fam in (report.get("C_etape0_par_famille") or {}).values() for r in fam if "supprime" not in r]
    rows.sort(key=lambda r: -(r.get("t_week") if isinstance(r.get("t_week"), int | float) else -99.0))
    lines = [f"{report.get('A_resume', {}).get('titre', '')} — {min(top, len(rows))} cellule(s) les plus fortes", ""]
    head = report.get("entete") or {}
    for k in ("effet_minimal_detectable", "puissance_effet_plante", "romano_wolf", "avertissements"):
        if head.get(k):
            lines.append(f"{k} : {_compact(head[k], width * 2)}")
    for r in rows[:top]:
        cid = str(r.get("id"))
        crit = ((report.get("I_classement") or {}).get(cid) or {}).get("criteres") or {}
        miss = [k for k, v in crit.items() if not v]
        lines += ["", f"■ {cid}", f"  étape 0 : {_compact({k: v for k, v in r.items() if k != 'id'}, width)}",
                  f"  critères NON remplis : {', '.join(miss) if miss else 'aucun'}"]  # fmt: skip
        for sec, keys in EXPLAIN_SECTIONS:
            row = ((report.get(sec) or {}).get("cellules") if sec.startswith("D_") else report.get(sec)) or {}
            vals = row.get(cid) or {}
            for k in keys:
                if k in vals and vals[k] is not None:
                    lines.append(f"  {k} : {_compact(vals[k], width)}")
    b = report.get("B_diagnostics_run2") or {}
    if b.get("detecteurs_existants_walk_forward"):
        lines += [
            "",
            f"détecteurs existants (walk-forward) : {_compact(b['detecteurs_existants_walk_forward'], width * 3)}",
        ]
    return "\n".join(lines) + "\n"
