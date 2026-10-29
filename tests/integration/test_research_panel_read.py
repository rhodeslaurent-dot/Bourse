"""29/10/2026: the New York panel (7 849 values with the delisted ones) killed the process on the PC while it was
read in one block. The chunked read must give exactly the arrays of the one-block read."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.db.models import PriceEod
from app.db.session import db_session
from app.services.research import _read_price_panel


def _one_block(s, isins, start, end):  # the former reading, kept here as the reference
    stmt = select(PriceEod.isin, PriceEod.day, PriceEod.open, PriceEod.high, PriceEod.low, PriceEod.close,
                  PriceEod.adj_close, PriceEod.volume, PriceEod.source, PriceEod.received_at).where(
        PriceEod.isin.in_(isins), PriceEod.day >= start, PriceEod.day <= end)  # fmt: skip
    df = pd.read_sql(stmt, s.connection())
    df["day"] = pd.to_datetime(df["day"]).values.astype("datetime64[D]")
    days = np.unique(df["day"].to_numpy().astype("datetime64[D]"))
    cols = sorted(df["isin"].unique().tolist())
    col = {k: n for n, k in enumerate(cols)}
    r = np.searchsorted(days, df["day"].to_numpy().astype("datetime64[D]"))
    c = df["isin"].map(col).to_numpy()
    arrays = {}
    for k in ("open", "high", "low", "close", "adj_close", "volume"):
        a = np.full((days.size, len(cols)), np.nan)
        a[r, c] = pd.to_numeric(df[k], errors="coerce").to_numpy(dtype=np.float64)
        arrays[k] = a
    rec = pd.to_datetime(df["received_at"], utc=True, errors="coerce").dt.strftime("%Y-%m-%d").fillna("?")
    codes, _ = pd.factorize(df["source"].astype(str) + "|" + rec)
    vin = np.full((days.size, len(cols)), -1, dtype=np.int64)
    vin[r, c] = codes.astype(np.int64)
    arrays["vintage"] = vin
    return days, cols, arrays


def test_the_chunked_read_gives_the_one_block_arrays(db_engine):
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    with db_session() as s:
        d0 = date(2024, 1, 1)
        for k, isin in enumerate(["US0000000003", "US0000000001", "US0000000002"]):
            for j in range(40):
                if (j + k) % 7 == 0:
                    continue  # holes, different per value
                px = 10 + k + j * 0.1
                s.add(PriceEod(isin=isin, day=d0 + timedelta(days=j), open=px, high=px + 1, low=px - 1, close=px,
                               adj_close=None if j % 5 == 0 else px * 0.9, volume=1000 + j,
                               source="eodhd" if j < 20 else "yfinance", received_at=t0 + timedelta(days=j // 10),
                               data_status="eod"))  # fmt: skip
        s.flush()
        wanted = ["US0000000001", "US0000000002", "US0000000003", "US0000000009"]  # the last one has no bar
        ref = _one_block(s, wanted, d0, d0 + timedelta(days=30))
        got = _read_price_panel(s, wanted, d0, d0 + timedelta(days=30), chunk_rows=7)
    assert np.array_equal(ref[0], got[0]) and ref[1] == got[1] == ["US0000000001", "US0000000002", "US0000000003"]
    for k in ("open", "high", "low", "close", "adj_close", "volume"):
        assert np.array_equal(ref[2][k], got[2][k], equal_nan=True), k
    # vintage labels are arbitrary codes: the same partition of the bars is what matters
    a, b = ref[2]["vintage"].ravel(), got[2]["vintage"].ravel()
    assert ((a[:, None] == a[None, :]) == (b[:, None] == b[None, :])).all()
