"""``us_filings`` (06:20 Paris, every day) — lot S5 (step 1 of the user's plan, 15/11/2026).

The SEC filings (managers' dealings Form 4, material events 8-K) and the FINRA daily short-sale volume of the US
values followed, over the last days (the SEC daily index of day D is complete in the night, the FINRA file is
posted after the US close): idempotent, paced, with the user's own User-Agent. Without ``data.sec.user_agent``
the SEC is not read, and it is said so on the job's line."""

from __future__ import annotations

from datetime import timedelta

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.us_sources import UsReport, finra_load, http_fetch, sec_load, settings


def run(ctx: JobContext) -> int:
    st = settings(ctx.config)
    end = ctx.run_date - timedelta(days=1)
    rep = UsReport()
    with db_session() as s:
        if st.sec_enabled and st.user_agent:
            fetch = http_fetch(st.user_agent, st.min_interval_s)
            try:
                with s.begin_nested():
                    rep = sec_load(s, ctx.config, end - timedelta(days=st.lookback_days - 1), end, fetch)
            except Exception as exc:  # noqa: BLE001 — FINRA is still read
                rep.notes.append(f"SEC en échec ({exc.__class__.__name__})")
        elif st.sec_enabled:
            rep.notes.append("SEC non lue : data.sec.user_agent à renseigner (nom et adresse de contact)")
        if st.finra_enabled:
            fetch_f = http_fetch(None, 1.0)
            try:
                with s.begin_nested():
                    finra_load(s, end - timedelta(days=st.finra_lookback_days - 1), end, fetch_f, rep)
            except Exception as exc:  # noqa: BLE001
                rep.notes.append(f"FINRA en échec ({exc.__class__.__name__})")
    ctx.note = rep.line()
    return rep.form4_added + rep.form8k_added + rep.short_rows
