# ruff: noqa: E501
"""Aggregated report of a registration run (docs/31 §4.3): statistics only — no price, no instrument (EODHD
data must not be redistributed). Cells with fewer than ``report_min_n`` trades are suppressed. Pure."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.research.prereg import Prereg

FORBIDDEN_KEYS = frozenset({"entry", "exit", "stop", "target", "open", "high", "low", "close", "isin", "name"})
HEADLINE_NONE = "AUCUN DÉCLENCHEUR RETENU"
CELL_KEYS_C = ("id", "n", "mean_r", "median_r", "hit_rate", "profit_factor", "mean_eur_300", "mean_excess",
               "t_week", "p_romano_wolf", "p_holm", "q_bh", "gate", "semaines", "mois", "duree_mediane")  # fmt: skip


class ReportError(ValueError):
    pass


def assert_no_price_fields(report: Any, path: str = "") -> None:
    """Refuse any price-like or instrument-identifying key, at any depth."""
    if isinstance(report, Mapping):
        for k, v in report.items():
            if str(k).lower() in FORBIDDEN_KEYS:
                raise ReportError(f"champ interdit dans le rapport : {path}/{k}")
            assert_no_price_fields(v, f"{path}/{k}")
    elif isinstance(report, list | tuple):
        for i, v in enumerate(report):
            assert_no_price_fields(v, f"{path}[{i}]")


def _public(d: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if not str(k).startswith("_")}


def suppress(row: Mapping[str, Any], min_n: int) -> dict[str, Any]:
    n = row.get("n")
    if isinstance(n, int) and n < min_n:
        keep = {k: row[k] for k in ("id", "famille", "declencheur", "h", "niveau", "signaux") if k in row}
        keep.update(n=n, supprime=f"moins de {min_n} trades")
        return keep
    return _public(row)


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
            "puissance_effet_plante": {k: v.get("puissance") for k, v in (results.get("familles") or {}).items()},
            "segment": results.get("segment"),
            "annees_dividendes_ecartees": results.get("annees_dividendes_ecartees"),
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
        f"  calibration (rejeu du run 2) : {h.get('calibration')}",
        f"  plan sha256 : {h.get('plan_sha256')}",
        f"  cellules enregistrées : {h.get('cellules_enregistrees')}",
        f"  cellules exécutées : {h.get('cellules_executees')}",
        f"  durée : {h.get('duree_s')} s",
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
    for cid, v in (b.get("cellules_f0") or {}).items():
        if cid.startswith("_"):
            continue
        if isinstance(v, Mapping):
            core = {
                k: v.get(k) for k in ("n", "difference_moyenne", "exces_moyen", "r_moyen", "ic", "t_semaine") if k in v
            }
            lines.append(f"  {cid} : {core}")
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
