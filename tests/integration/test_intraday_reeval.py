"""``intraday_reeval`` (user request of 28/10/2026): today's open proposals re-evaluated with a fresh quote —
pullback reached, breakout trigger crossed, degraded configuration, price above P_max; a delayed quote gives
« à vérifier » and no alert; only changes stored; the proposal and its snapshot untouched (rule 10);
notifications deduplicated per instrument, account, kind and day, and subject to the availability mode."""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.data.dto import DataStatus, MarketPerimeter, PriceType, Quote
from app.db.models import DecisionSnapshot, JobRun, Proposal, ProposalReevaluation
from app.db.session import db_session
from app.notify.base import Notifier
from app.scheduler.jobs.intraday_reeval import candidates, reevaluate_once
from app.scheduler.runner import JobContext
from app.services.alerts import AlertService
from app.services.decisions import create_proposal, record_signal
from app.services.portfolio import ensure_accounts
from app.services.reevaluations import latest_by_proposal, summary

T = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
ISIN = "FR0000120271"
WAIT = "WATCH : refusé — P_max 99 dépassé de 4.04% — limite posée en attente d'un repli"


def _q(last: float, at: datetime, delayed: bool = False) -> Quote:
    return Quote(
        source="eodhd_rest" if delayed else "saxo",
        market_perimeter=MarketPerimeter.PRIMARY,
        received_at=at,
        processed_at=at,
        data_status=DataStatus.DELAYED if delayed else DataStatus.REALTIME,
        symbol=ISIN,
        market_timestamp=at - timedelta(minutes=15 if delayed else 0),
        price_type=PriceType.LAST,
        last=last,
        declared_delay_minutes=15 if delayed else 0,
    )


@pytest.fixture
def ctx(config, calendars, db_engine):
    sent: list = []
    c = JobContext(config, calendars, T.date(), ("XPAR",), "normal")
    c.alerts = AlertService(Notifier(telegram_send=lambda ev: sent.append(ev)), 15)
    c.mode = "disponible"
    c.sent = sent  # type: ignore[attr-defined]
    with db_session() as s:
        ensure_accounts(s)
    return c


def _pullback(s, snapshot_price: float = 103.0) -> Proposal:
    sig = record_signal(s, ISIN, "d7_trend_quality", "moteur", T, "eod", "eod", "t", stop_initial=90.0)
    return create_proposal(
        s, sig, "WATCH", {ISIN: {"last": snapshot_price, "data_status": "eod"}}, {"passed": [], "blocked": [WAIT]},
        "t", created_at=T - timedelta(hours=2), stop=90.0, targets=[120.0, 130.0], account_recommended="pea",
        details={
            "admission": "refuse", "p_max": 99.0, "comptes": {"pea": {"frais_aller_retour_pct": 0.01, "titres": 10}},
            "entree": {"declencheur": 100.0, "limite": 99.0},
            "configuration": {"type": "repli_support", "niveau_bas": 95.0, "p_max_technique": 101.0},
        },
    )  # fmt: skip


def _breakout(s) -> Proposal:
    sig = record_signal(s, ISIN, "d2_breakout", "moteur", T, "eod", "eod", "t", stop_initial=95.0)
    return create_proposal(
        s, sig, "BUY", {ISIN: {"last": 101.0, "data_status": "eod"}}, {"passed": [], "blocked": []}, "t",
        created_at=T - timedelta(hours=2), stop=95.0, targets=[130.0], account_recommended="cto_cash",
        details={
            "admission": "admis", "p_max": 104.0, "comptes": {"cto_cash": {"frais_aller_retour_pct": 0.0}},
            "entree": {"declencheur": 102.0, "limite": 104.0},
            "configuration": {"type": "franchissement", "niveau_bas": 100.0, "p_max_technique": 104.0},
        },
    )  # fmt: skip


def _tick(ctx, price: float, at: datetime, delayed: bool = False) -> int:
    with db_session() as s:
        props = candidates(s, at)
        return reevaluate_once(s, ctx, props, {ISIN: _q(price, at, delayed)}, at)


def _rows(pid: int) -> list[ProposalReevaluation]:
    with db_session() as s:
        return list(s.scalars(select(ProposalReevaluation).where(ProposalReevaluation.proposal_id == pid)))


def test_pullback_reached_degraded_only_changes_and_proposal_untouched(ctx):
    with db_session() as s:
        p = _pullback(s)
        pid = p.id
        before = copy.deepcopy((p.action, p.details, p.gates, p.stop, p.targets, p.status))
        snap_before = copy.deepcopy(
            s.scalar(select(DecisionSnapshot).where(DecisionSnapshot.proposal_id == pid)).quotes
        )
    assert _tick(ctx, 101.0, T) == 1  # above P_max, as at creation: stored (first look), no alert
    assert _tick(ctx, 101.5, T + timedelta(seconds=15)) == 0  # same status: nothing stored
    assert ctx.sent == []
    assert _tick(ctx, 98.5, T + timedelta(seconds=30)) == 1  # (a) the limit becomes reachable
    assert [e.kind for e in ctx.sent] == ["reeval_entry"] and ctx.sent[0].level == "P2"
    assert "prix d'entrée atteint" in ctx.sent[0].title and len(ctx.sent[0].telegram_text().split("\n")) <= 12
    assert _tick(ctx, 98.4, T + timedelta(seconds=45)) == 0
    assert _tick(ctx, 94.0, T + timedelta(seconds=60)) == 1  # (c) structure lost
    assert [e.kind for e in ctx.sent] == ["reeval_entry", "reeval_degraded"]
    assert "annuler" in "\n".join(ctx.sent[1].lines)
    rows = _rows(pid)
    assert [(r.status, r.actionable) for r in rows] == [
        ("limite_depassee", False), ("entree_atteinte", True), ("configuration_degradee", False)
    ]  # fmt: skip
    assert rows[1].source == "saxo" and rows[1].data_status == "realtime" and rows[1].price == 98.5
    with db_session() as s:
        p = s.get(Proposal, pid)
        assert (p.action, p.details, p.gates, p.stop, p.targets, p.status) == before  # rule 10
        assert s.scalar(select(DecisionSnapshot).where(DecisionSnapshot.proposal_id == pid)).quotes == snap_before


def test_delayed_quote_is_to_verify_without_alert(ctx):
    with db_session() as s:
        pid = _pullback(s).id
    assert _tick(ctx, 98.5, T, delayed=True) == 1
    assert ctx.sent == []
    (row,) = _rows(pid)
    assert row.status == "entree_atteinte" and not row.actionable and row.data_status == "delayed"
    with db_session() as s:
        text = summary(latest_by_proposal(s, [pid])[pid])["texte"]
    assert text == "Réévaluée à 14:00 (heure Réunion) : prix d'entrée atteint — cours différé 15 min, à vérifier"
    # the same quote turning realtime (subscription) confirms the entry: announced then
    assert _tick(ctx, 98.5, T + timedelta(seconds=15)) == 1
    assert [e.kind for e in ctx.sent] == ["reeval_entry"]


def test_breakout_trigger_crossed_then_above_pmax(ctx):
    with db_session() as s:
        pid = _breakout(s).id
    assert _tick(ctx, 101.5, T) == 1  # under the trigger (as at creation): waiting, no alert
    assert ctx.sent == []
    assert _tick(ctx, 103.0, T + timedelta(seconds=15)) == 1  # (b)
    assert [e.kind for e in ctx.sent] == ["reeval_entry"] and "déclencheur franchi" in ctx.sent[0].title
    assert _tick(ctx, 105.0, T + timedelta(seconds=30)) == 1  # (d)
    assert [r.status for r in _rows(pid)] == ["en_attente", "declencheur_franchi", "limite_depassee"]


def test_entry_alert_deduplicated_per_day_and_mode_read(ctx):
    with db_session() as s:
        _pullback(s)
    _tick(ctx, 101.0, T)
    _tick(ctx, 98.5, T + timedelta(seconds=15))
    _tick(ctx, 101.0, T + timedelta(seconds=30))
    _tick(ctx, 98.5, T + timedelta(seconds=45))  # back to the entry: same instrument, account, kind and day
    assert [e.kind for e in ctx.sent] == ["reeval_entry"]


def test_reunion_mode_defers_the_intraday_p2(ctx):
    ctx.mode = "reunion"
    with db_session() as s:
        _pullback(s)
    _tick(ctx, 101.0, T)
    _tick(ctx, 98.5, T + timedelta(seconds=15))
    assert ctx.sent == []  # stored in alerts, not pushed (docs/02 §2.3)


def test_other_watch_reasons_and_old_days_are_not_followed(ctx):
    with db_session() as s:
        p = _pullback(s)
        p.gates = {"blocked": [WAIT, "WATCH : confiance 2/5 < 3"]}
        q = _pullback(s)
        q.created_at = T - timedelta(days=1)
        s.flush()
        assert candidates(s, T) == []


def test_job_writes_its_jobs_runs_line(config, calendars, db_engine):
    from app.scheduler.runner import execute_job

    run = execute_job("intraday_reeval", config, calendars, run_date=date(2026, 10, 28))
    assert run.status == "ok" and run.rows == 0
    with db_session() as s:
        row = s.scalar(select(JobRun).where(JobRun.job == "intraday_reeval"))
        assert row is not None and "aucune proposition" in (row.note or "")


def test_scheduled_every_15_s_inside_the_window(config):
    from app.scheduler.registry import build_schedule

    (job,) = [j for j in build_schedule(config.params) if j.name == "intraday_reeval"]
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["second"] == "*/15" and fields["hour"] == "9-21" and fields["day_of_week"] == "mon-fri"
