"""docs/32 §32.3 — user decision of 26/10/2026: « je préfère investir sur les marchés principaux, mais ok pour
Xetra s'ils ne sont pas activés ou si l'écart de change est déterminant ». The primary venue stays the default; a
euro listing is shown as an execution alternative and recommended only when the change fee it avoids weighs."""

from __future__ import annotations

from app.db.models import FxRate, Instrument, InstrumentAlias, Proposal
from app.db.session import db_session
from app.domain.costs import alt_listing_saving
from app.services import engine
from app.services.universe import SymbolRow, refresh_universe
from tests.integration.test_engine import DAY, ISIN, NOW, _seed, breakout_bars


def test_change_fee_avoided_and_threshold():
    al = alt_listing_saving("AMZ.XETRA", 5_000.0, 5_500.0, 200.0, 0.0025, 0.10)
    assert al is not None and round(al.fx_saving_eur, 2) == 26.25  # one conversion per leg
    assert round(al.share_of_gain or 0, 4) == 0.1313 and al.recommended
    assert not alt_listing_saving("AMZ.XETRA", 5_000.0, 5_500.0, 1_000.0, 0.0025, 0.10).recommended  # 2.6 %
    lost = alt_listing_saving("AMZ.XETRA", 5_000.0, 5_500.0, -10.0, 0.0025, 0.10)
    assert lost is not None and lost.share_of_gain is None and not lost.recommended
    assert alt_listing_saving("SAP.XETRA", 5_000.0, 5_500.0, 200.0, 0.0, 0.10) is None  # euro value: nothing avoided


def test_the_euro_listing_is_kept_as_an_alias_never_as_the_listing(db_engine, config, calendars):
    rows = [
        SymbolRow("US0231351067", "Amazon", "AMZN", "US", "USD", None, market_cap_eur=1.8e12),
        SymbolRow("US0231351067", "Amazon", "AMZ", "XETRA", "EUR", None, market_cap_eur=1.8e12),
    ]
    with db_session() as s:
        refresh_universe(s, config, calendars, rows, DAY)
        inst = s.get(Instrument, "US0231351067")
        assert inst.ticker_eodhd == "AMZN.US" and inst.currency == "USD"  # primary venue first
        alts = s.query(InstrumentAlias).filter(InstrumentAlias.provider == "eodhd_alt").all()
        assert [(a.isin, a.alias) for a in alts] == [("US0231351067", "AMZ.XETRA")]
        refresh_universe(s, config, calendars, rows, DAY)
        assert s.query(InstrumentAlias).filter(InstrumentAlias.provider == "eodhd_alt").count() == 1


def _scan(s, config, *, currency: str, alias: bool) -> list[dict]:
    _seed(s, breakout_bars(), pea=False)
    inst = s.get(Instrument, ISIN)
    inst.currency, inst.mic = currency, "XNYS" if currency == "USD" else "XPAR"
    s.add(FxRate(pair="EURUSD", day=DAY, rate=1.08, source="ecb"))
    if alias:
        s.add(InstrumentAlias(isin=ISIN, provider="eodhd_alt", alias="TOTB.XETRA"))
    s.flush()
    engine.scan(s, config, DAY, now=NOW)
    return [p.details or {} for p in s.query(Proposal).filter(Proposal.isin == ISIN)]


def test_a_dollar_value_shows_the_euro_listing_with_its_weight(db_engine, raw):
    from app.config import validate_params

    raw["brokers"]["saxo"]["fx_fee_pct"] = 0.0001  # the fixture's stop sits just under P_max: keep it sizable
    config = validate_params(raw)
    with db_session() as s:
        details = _scan(s, config, currency="USD", alias=True)
    alts = [d["execution_alternative"] for d in details if "execution_alternative" in d]
    assert alts, "a sized dollar proposal must show the euro listing"
    for x in alts:
        assert x["cotation"] == "TOTB.XETRA" and x["change_evite_eur"] > 0 and x["seuil"] == 0.10
        assert x["recommandee"] == (x["part_du_gain_net"] is not None and x["part_du_gain_net"] >= 0.10)
        assert x["niveaux_indicatifs_eur"]["limite"] > x["niveaux_indicatifs_eur"]["stop"] > 0
        assert "non vérifiées" in x["avertissement"]


def test_no_alternative_without_an_euro_listing(db_engine, config):
    with db_session() as s:
        assert not any("execution_alternative" in d for d in _scan(s, config, currency="USD", alias=False))


def test_no_alternative_for_a_euro_value(db_engine, config):
    with db_session() as s:
        assert not any("execution_alternative" in d for d in _scan(s, config, currency="EUR", alias=True))


def test_refused_only_because_of_the_change_points_to_the_euro_listing():
    """The change fee alone pushes P_max under the stop: the euro listing is pointed to, the proposal stays
    refused (re-assessed on that listing before any order)."""
    from types import SimpleNamespace

    from app.services.engine import _alt_execution

    ev = SimpleNamespace(details={})
    rows = {"cto_cash": {"statut": "refuse", "p_max": 98.6, "sans_change": {"statut": "admis", "p_max": 99.2}}}
    _alt_execution(ev, "TOTB.XETRA", None, {}, rows, 110.0, 0.92, SimpleNamespace(stop=98.9), 0.10)  # type: ignore[arg-type]
    x = ev.details["execution_alternative"]
    assert x["admise_seulement_sans_change"] and x["recommandee"] and x["compte"] == "cto_cash"
    assert x["niveaux_indicatifs_eur"] == {"limite": round(99.2 * 0.92, 2), "stop": round(98.9 * 0.92, 2)}
    assert "réévaluer" in x["avertissement"]
    ev2 = SimpleNamespace(details={})
    rows["cto_cash"]["sans_change"]["statut"] = "refuse"  # refused anyway: nothing to point to
    _alt_execution(ev2, "TOTB.XETRA", None, {}, rows, 110.0, 0.92, SimpleNamespace(stop=98.9), 0.10)  # type: ignore[arg-type]
    assert ev2.details == {}


def test_the_fiche_shows_the_execution_venue(raw, calendars, db_engine, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import validate_params
    from app.main import create_app

    raw["brokers"]["saxo"]["fx_fee_pct"] = 0.0001
    config = validate_params(raw)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with db_session() as s:
        _scan(s, config, currency="USD", alias=True)
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        page = c.get("/opportunites").text  # created today (wall clock)
    assert "Place d'exécution" in page and "TOTB.XETRA" in page and "non vérifiées" in page
