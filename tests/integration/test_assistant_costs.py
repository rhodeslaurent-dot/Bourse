"""User question of 28/10/2026 (« pourquoi Cirsa perd une fois les coûts payés ? ») : the assistant must find the
value named in a free question, and its fact pack must carry the full cost per account, the accounts left out and
why, the taxes applied and the origin of the expected R (the detector's history, not the value)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.db.models import Instrument, Proposal, Signal, WatchlistRow
from app.db.session import db_session
from app.domain.costs import foreign_tax, ftt_rate_any
from app.services.assistant import build_context, mentioned_isin, question_pool

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
CIRSA = "ES0109260291"


def test_foreign_tax_applies_above_the_cap_only():
    es = {"rate": 0.002, "min_market_cap_eur": 1e9}
    assert foreign_tax(es, 2.5e9)[0] == 0.002
    rate, why = foreign_tax(es, 4e8)
    assert rate == 0.0 and "non due" in why
    assert foreign_tax(es, None)[0] == 0.002  # unknown cap: kept by prudence
    assert foreign_tax(0.005, 1e6) == (0.005, "taux du pays d'émission")  # bare rate: no threshold (GB stamp duty)
    assert foreign_tax(None, 1e9)[0] == 0.0
    assert ftt_rate_any(es) == 0.002 and ftt_rate_any(0.001) == 0.001 and ftt_rate_any(None) == 0.0


def _seed(s):
    s.add(Instrument(isin=CIRSA, name="Cirsa Enterprises SAU", ticker_local="CIRSA", mic="XMAD", currency="EUR",
                     in_universe=True, srd_status="none"))  # fmt: skip
    s.add(Instrument(isin="FR0000133308", name="Orange SA", ticker_local="ORA", mic="XPAR", in_universe=True))
    s.add(WatchlistRow(isin=CIRSA, reason="momentum", source="rules", active=True, rs_rank=100.0, added_at=NOW))
    s.add(WatchlistRow(isin="FR0000133308", reason="momentum", source="rules", active=True, rs_rank=85.0,
                       added_at=NOW))  # fmt: skip
    sig = Signal(
        isin=CIRSA,
        ts=NOW,
        detector="d7_trend_quality",
        source="moteur",
        evidence={},
        timeframe="1d",
        data_status="eod",
        params_version="test",
    )
    s.add(sig)
    s.flush()
    s.add(
        Proposal(
            signal_id=sig.id, isin=CIRSA, created_at=NOW, action="WATCH", status="open", stop=17.2,
            targets=[19.4], account_recommended="cto_cash",
            gates={"blocked": ["WATCH : espérance historique négative au coût complet (ADR-013)"]},
            details={
                "comptes": {
                    "pea": {"statut": "admis", "frais_aller_retour_pct": 0.012, "gain_net_pct": 0.031,
                            "perte_nette_pct": 0.052, "ratio_net": 0.6},
                    "cto_cash": {"statut": "admis", "frais_aller_retour_pct": 0.0036, "gain_net_pct": 0.042,
                                 "perte_nette_pct": 0.046, "ratio_net": 0.91, "impot_eur": 21.3},
                },
                "comptes_exclus": {"cto_srd": "SRD réservé aux valeurs d'Euronext Paris"},
                "taxe_etrangere": {"pays": "ES", "taux": 0.002, "base": "capitalisation ≥ 1 Md€ (présomption)"},
                "esperance_nette_r": -0.137,
                "esperance_backtest": {"r_net_retreci": -0.137, "n": 2716},
            },
        )
    )  # fmt: skip
    s.flush()


def test_a_free_question_is_attached_to_the_value_it_names(db_engine):
    with db_session() as s:
        _seed(s)
        pool = question_pool(s, NOW)
        assert mentioned_isin(s, "Pourquoi Cirsa Enterprises SAU perd une fois les coûts payés ?", pool) == CIRSA
        assert mentioned_isin(s, "pourquoi CIRSA est en watch ?", pool) == CIRSA  # ticker
        assert mentioned_isin(s, "que veut dire le régime orange ?", pool) is None  # a word, not Orange SA
        assert mentioned_isin(s, "et Orange SA, on achète ?", pool) == "FR0000133308"
        assert mentioned_isin(s, "comment va le marché ?", pool) is None


def test_the_fact_pack_carries_costs_accounts_and_the_origin_of_the_expectancy(db_engine):
    with db_session() as s:
        _seed(s)
        ctx = build_context(s, CIRSA, 40000.0, now=NOW)
    prop = ctx["propositions_30j"][0]
    assert prop["detecteur"] == "d7_trend_quality"
    assert prop["couts_par_compte"]["PEA"]["frais_aller_retour_fraction"] == 0.012
    assert prop["couts_par_compte"]["CTO comptant"]["impot_sur_le_gain_eur"] == 21.3
    assert prop["comptes_exclus"] == {"CTO SRD": "SRD réservé aux valeurs d'Euronext Paris"}
    assert prop["taxe_etrangere"]["taux"] == 0.002
    esp = prop["esperance_nette"]
    assert esp["valeur_R"] == -0.137 and "backtest du détecteur" in esp["origine"] and "2716" in esp["origine"]
    assert "pas un calcul propre à cette valeur" in esp["portee"]
    assert any("ADR-013" in b for b in prop["regles_bloquantes"])


def test_the_engine_stores_the_foreign_tax_and_the_accounts_left_out(db_engine, raw):
    """A value outside Euronext Paris has no SRD account, said on the card; the foreign tax and its basis are kept."""
    from app.config import validate_params
    from app.services import engine
    from tests.integration.test_engine import DAY, ISIN, _seed, breakout_bars

    raw["tax"][0]["ftt_by_country"] = {"FR": {"rate": 0.002, "min_market_cap_eur": 1e9}}  # the fixture value is French
    config = validate_params(raw)
    with db_session() as s:
        _seed(s, breakout_bars())
        s.get(Instrument, ISIN).mic = "XAMS"
        s.flush()
        engine.scan(s, config, DAY, now=datetime(2026, 9, 9, 6, 0, tzinfo=UTC))
        details = [p.details or {} for p in s.query(Proposal).filter(Proposal.isin == ISIN)]
    assert any("comptes" in d for d in details), "the fixture must produce a costed proposal"
    for d in details:
        if "comptes" not in d:
            continue
        assert d["comptes_exclus"]["cto_srd"] == "SRD réservé aux valeurs d'Euronext Paris"
        assert "cto_srd" not in d["comptes"]
        assert d["taxe_etrangere"]["taux"] == 0.002 and "Md€" in d["taxe_etrangere"]["base"]
