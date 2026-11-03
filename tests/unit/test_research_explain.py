"""03/11/2026 — ``research explain`` on a descriptive plan read at the sign (registration n° 8: a negative drift):
the cells shown are the largest |t|, and one line lists every cell, so that no cell is silently left out (C3 and X2
were not among the 8 highest t)."""

from __future__ import annotations

from app.domain.research.report import explain


def _report(title: str) -> dict:
    rows = [{"id": f"X{i}", "n": 100, "mean_excess_ctrl": 0.001 * i, "t_week": float(i)} for i in range(1, 9)]
    rows.append({"id": "C3", "n": 7000, "mean_excess_ctrl": -0.02, "t_week": -9.4})
    return {"A_resume": {"titre": title}, "C_etape0_par_famille": {"F": rows}}


def test_a_descriptive_plan_shows_its_most_negative_cell():
    out = explain(_report("DESCRIPTIF (…) — AUCUN DÉCLENCHEUR RETENU"), top=3)
    assert "■ C3" in out and "|t| le plus grand" in out
    assert "C3 n 7000 excès -0.0200 t -9.4000" in out  # the all-cells line


def test_a_buy_plan_keeps_the_highest_t_and_still_lists_every_cell():
    out = explain(_report("AUCUN DÉCLENCHEUR RETENU"), top=3)
    assert "■ X8" in out and "■ C3" not in out and "t le plus haut" in out
    assert "C3 n 7000" in out
