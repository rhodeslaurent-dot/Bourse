"""One-sentence summary of an opportunity (docs/22 §22.2), written by rules from the stored signal
evidence — never by the LLM, never with a figure that is not in the evidence."""

from __future__ import annotations

from typing import Any


def _pct(x: Any) -> str:
    return f"{float(x) * 100:.1f} %".replace(".", ",")


def _num(x: Any) -> str:
    return f"{float(x):.4g}".replace(".", ",")


def _rs(ev: dict[str, Any]) -> str:
    rs = ev.get("rs_rank")
    return f" ; force relative {float(rs):.0f}/100" if isinstance(rs, int | float) else ""


def pitch(detector: str | None, evidence: dict[str, Any] | None, source: str | None = None) -> str | None:
    ev = evidence or {}
    num = lambda k: isinstance(ev.get(k), int | float)  # noqa: E731
    if detector == "d2_breakout" and num("pivot_60"):
        vol = f" sur un volume {float(ev['rvol']):.1f}× la moyenne".replace(".", ",") if num("rvol") else ""
        pivot = _num(ev["pivot_60"])
        return f"Cassure du plus haut de 60 séances ({pivot}){vol}, après une consolidation serrée{_rs(ev)}."
    if detector == "d3_range_expansion" and num("hausse_du_jour"):
        vol = f" sur un volume {float(ev['rvol']):.1f}× la moyenne".replace(".", ",") if num("rvol") else ""
        return (
            f"Séance d'accélération : +{_pct(ev['hausse_du_jour'])}{vol}, clôture dans le haut de la séance, "
            f"au sortir d'une base calme{_rs(ev)}."
        )
    if detector == "d4_pullback" and num("repli_pct"):
        touched = [str(m).upper() for m in ev.get("moyennes_touchees") or []]
        mas = f"sa {touched[0]}, qui tient" if len(touched) == 1 else f"ses {' et '.join(touched)}, qui tiennent"
        if not touched:
            mas = "ses moyennes courtes, qui tiennent"
        rsi = f", RSI {float(ev['rsi14']):.0f}" if num("rsi14") else ""
        pull = _pct(ev["repli_pct"])
        return f"Repli de {pull} d'une valeur leader jusqu'à {mas}, avec reprise du jour{rsi}{_rs(ev)}."
    if detector == "d7_trend_quality":
        slope = f" en hausse de {_pct(ev['pente_mm200'])} sur 20 séances" if num("pente_mm200") else ""
        return (
            f"Tendance de fond haussière (MM50 au-dessus de la MM200{slope}) et retour sur la MM50, qui tient{_rs(ev)}."
        )
    if detector == "external":
        who = f" de {source}" if source and source not in ("moteur", "moteur_lettre") else " d'une lettre"
        return (
            f"Recommandation d'achat{who} avec objectif et niveau d'invalidation publiés, recalculée au coût complet."
        )
    return None
