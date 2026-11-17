"""Step 2, option B (17/11/2026, docs/42) — the evening AI review of each held position, in observation: masked
dossier from Python's review, analyst then contradictor, Python's guard on the final view (a stop never lowered nor
above the last close; quantity by Python), budget, idempotence, never applied."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.db.models import Instrument, LlmCall, PositionRow, PriceEod
from app.db.session import db_session
from app.llm.gateway import LlmUsage
from app.llm.schemas import PositionAnalystOut, PositionContradictorOut
from app.services import position_ai
from app.services.portfolio import declare_execution, declare_stop, ensure_accounts

ISIN = "FR0000120271"
NOW = datetime(2026, 11, 17, 18, 40, tzinfo=UTC)
DAY = date(2026, 11, 17)


class Gw:
    enabled = True

    class settings:  # noqa: N801
        write_model = "claude-sonnet-5-5"
        prompts_dir = Path("app/llm/prompts")
        prices = {"claude-sonnet-5-5": {"input": 2.0, "output": 10.0}}

    def __init__(self, analyst: dict, contra: dict) -> None:
        self.out = {"position_ai_analyst": PositionAnalystOut.model_validate(analyst),
                    "position_ai_contradictor": PositionContradictorOut.model_validate(contra)}  # fmt: skip
        self.users: list[str] = []

    def parse(self, s, purpose, prompt, user, schema, model=None, max_tokens=0):
        self.users.append(user)
        s.add(LlmCall(model=model, prompt_version="1", prompt_hash=purpose, purpose=purpose, cost_usd=0.01, ts=NOW))
        return self.out[purpose], LlmUsage(10, 10, 0.01, False)


def _held(s) -> int:
    s.add(Instrument(isin=ISIN, name="TotalEnergies SE", mic="XPAR", currency="EUR", in_universe=True))
    d = DAY - timedelta(days=300)
    k = 0
    while d <= DAY:
        if d.weekday() < 5:
            c = 50 + k * 0.05  # a slow rise: last close ≈ 60
            s.add(PriceEod(isin=ISIN, day=d, open=c, high=c * 1.01, low=c * 0.99, close=c, adj_close=c, volume=1000,
                           source="eodhd", received_at=NOW))  # fmt: skip
            k += 1
        d += timedelta(days=1)
    s.flush()
    accs = ensure_accounts(s)
    r = declare_execution(s, accs["pea"].id, ISIN, "buy", 40, 55.0, NOW - timedelta(days=30), "web")
    declare_stop(s, r.position_id, 52.0, "seuil", 40, "initial", "web")
    s.flush()
    return r.position_id


ANALYST = {"action": "remonter_stop", "stop": 57.5, "motifs": [{"texte": "Le gain est installé.", "faits": ["F2"]}],
           "confiance": "moyenne"}  # fmt: skip


def test_the_review_is_masked_guarded_and_never_applied(db_engine, config):
    with db_session() as s:
        pid = _held(s)
        stop_before = s.get(PositionRow, pid).stop_current
        gw = Gw(ANALYST, {"objections": [{"texte": "Le stop proposé laisse de la marge.", "faits": ["F1"]}],
                          "action_finale": "alleger", "part_finale": 0.25, "confiance": "faible"})  # fmt: skip
        (row,) = position_ai.run_day(s, config, gw, DAY, NOW)
        assert row.status == "ok" and row.action == "alleger" and row.shares_ai == 10  # 25 % of 40, by Python
        assert "TotalEnergies" not in gw.users[0] and ISIN not in gw.users[0]  # the company is masked
        assert "<avis>" in gw.users[1] and row.python["verdict"] is not None  # Python's review kept beside it
        assert s.get(PositionRow, pid).stop_current == stop_before  # observation: nothing applied
        assert "jamais appliqué" in position_ai.summary(row)
        again = position_ai.run_day(s, config, gw, DAY, NOW)[0]
        assert again.id == row.id and len(gw.users) == 2  # idempotent: same day, same dossier → no new call


def test_a_stop_lowered_or_above_the_close_is_set_aside_never_corrected(db_engine, config):
    with db_session() as s:
        _held(s)
        for k, (stop, why) in enumerate(((50.0, "jamais abaissé"), (80.0, "au-dessus du dernier cours"))):
            final = {"objections": [{"texte": "Avis repris.", "faits": ["F2"]}], "action_finale": "remonter_stop",
                     "stop_final": stop, "confiance": "moyenne"}  # fmt: skip
            gw = Gw(ANALYST, final)
            (row,) = position_ai.run_day(s, config, gw, DAY + timedelta(days=k), NOW)
            assert row.status == "ecartee" and row.action is None and row.stop_ai is None
            assert why in row.rejected[-1]["motif"]
    st = position_ai.settings(config)
    line = {"cours": 60.0, "stop": None}
    assert "sous le dernier cours" in position_ai.check_final("remonter_stop", 30.0, None, line, st)  # band 25 %
    assert position_ai.check_final("remonter_stop", 58.0, None, line, st) is None
    assert "hors liste" in position_ai.check_final("alleger", None, 0.3, line, st)


def test_the_review_respects_its_daily_budget(db_engine, config):
    config.raw["position_ai"]["daily_budget_usd"] = 0.001
    with db_session() as s:
        _held(s)
        (row,) = position_ai.run_day(s, config, Gw(ANALYST, {"action_finale": "conserver", "confiance": "faible"}),
                                     DAY, NOW)  # fmt: skip
        assert row.status == "budget" and not s.scalars(select(LlmCall)).first()


def test_the_defence_is_measured_against_doing_nothing_on_finished_windows(db_engine, config):
    with db_session() as s:
        pid = _held(s)
        gw = Gw(ANALYST, {"objections": [{"texte": "Avis repris.", "faits": ["F2"]}], "action_finale": "remonter_stop",
                          "stop_final": 57.5, "confiance": "moyenne"})  # fmt: skip
        (row,) = position_ai.run_day(s, config, gw, DAY, NOW)
        assert row.status == "ok" and row.stop_ai == 57.5
        assert position_ai.measure_defense(s, DAY + timedelta(days=1)) == []  # no session after the review yet
        d, k = DAY + timedelta(days=1), 0
        while k < 20:  # the stock falls after the review: 59, 58, … through 57.5 then 52
            if d.weekday() < 5:
                c = 59.0 - k
                s.add(PriceEod(isin=ISIN, day=d, open=c + 0.5, high=c + 1, low=c - 0.5, close=c, adj_close=c,
                               volume=1000, source="eodhd", received_at=NOW))  # fmt: skip
                k += 1
            d += timedelta(days=1)
        s.flush()
        by = {(m.author, m.action, m.horizon): m for m in position_ai.measure_defense(s, d)}
        ia5, ia20 = by[("ia", "remonter_stop", 5)], by[("ia", "remonter_stop", 20)]
        assert ia5.n == 1 and ia5.positions == 1 and ia5.mean_gap > 0  # out at the raised stop, not at 52
        assert ia20.mean_gap > 0 and ia5.t_stat is None  # one case: no t
        assert s.get(PositionRow, pid).stop_current == 52.0  # observation: nothing applied


def test_a_run_without_the_ai_never_replaces_a_review_the_ai_gave(db_engine, config):
    """17/11/2026: `run-job position_ai_review` from the command line had no AI and overwrote the evening reviews."""
    with db_session() as s:
        _held(s)
        gw = Gw(ANALYST, {"objections": [{"texte": "Avis repris.", "faits": ["F2"]}], "action_finale": "conserver",
                          "confiance": "moyenne"})  # fmt: skip
        (row,) = position_ai.run_day(s, config, gw, DAY, NOW)
        assert row.status == "ok"
        (again,) = position_ai.run_day(s, config, None, DAY, NOW)
        assert again.id == row.id and again.status == "ok" and again.action == "conserver"
        assert position_ai.prompts_dir(config, None) is not None  # the hash never depends on the AI being there
