"""docs/34 — rises out of the noise, triple barrier, lift tables (pure)."""

from __future__ import annotations

from app.domain.indicators import Bar
from app.domain.moves import (
    MoveParams,
    barrier_label,
    decile_lift,
    find_episodes,
    size_bucket,
    vol_bucket,
    wilson,
    zigzag,
)


def _bars(closes: list[float], spread: float = 0.005) -> list[Bar]:
    return [Bar(c, c * (1 + spread), c * (1 - spread), c, 1000) for c in closes]


def test_zigzag_pivots_need_the_reversal_threshold():
    closes = [100, 99, 98, 100, 103, 106, 104, 101, 100, 103, 104]
    piv = zigzag(closes, [0.03] * len(closes))
    assert piv[:3] == [(2, "L"), (5, "H"), (8, "L")]
    assert piv[-1] == (10, "H?")  # last extreme, unconfirmed


def test_an_episode_is_a_rise_out_of_the_noise():
    flat = [101.2 - 0.03 * i + (0.3 if i % 2 else -0.3) for i in range(40)]  # quiet base, low at its end
    rise = [100 * 1.012**k for k in range(1, 11)]  # +12.7 % in 10 sessions, steady
    after = [rise[-1] * 0.99**k for k in range(1, 8)]  # reversal confirms the peak
    eps = find_episodes(_bars(flat + rise + after), MoveParams())
    big = [e for e in eps if e.rise >= 0.1]
    assert len(big) == 1
    ep = big[0]
    assert ep.peak == 49 and ep.confirmed and ep.shape == "progressive" and 0.12 < ep.rise < 0.14
    # a volatile value (±3 % a day): +7 % in two sessions is noise (2 σ √2 ≈ 8.5 %), not an episode
    noisy = (
        [100 * (1.03 if i % 2 else 1.0) - 0.02 * i for i in range(40)]
        + [103.5, 106.5]
        + [106.5 * 0.94**k for k in (1, 2)]
    )
    assert [e for e in find_episodes(_bars(noisy), MoveParams(sigma_k=0)) if e.peak == 41]  # a zigzag upswing…
    assert not [e for e in find_episodes(_bars(noisy), MoveParams()) if e.peak == 41]  # …but inside the noise


def test_a_one_session_jump_is_a_saut():
    base = [101.2 - 0.03 * i + (0.2 if i % 2 else -0.2) for i in range(40)]
    jump = [109, 110, 111]
    after = [111 * 0.97**k for k in range(1, 6)]
    eps = [e for e in find_episodes(_bars(base + jump + after), MoveParams()) if e.rise >= 0.08]
    assert eps and eps[0].shape == "saut"


def test_triple_barrier_from_the_next_open():
    p = MoveParams(min_rise=0.05, horizon=5, stop_atr=2.0)
    up = _bars([100, 100, 101, 103, 106, 107, 108])
    assert barrier_label(up, 0, atr=1.0, sigma=0.01, p=p) == 1
    down = _bars([100, 100, 99, 97, 96, 95, 94])
    assert barrier_label(down, 0, atr=1.0, sigma=0.01, p=p) == 0
    assert barrier_label(up, 3, atr=1.0, sigma=0.01, p=p) is None  # not enough sessions after
    assert barrier_label(up, 0, atr=None, sigma=0.01, p=p) is None


def test_decile_lift_and_intervals():
    vals = list(range(1000))
    labels = [1 if v >= 900 else 0 for v in vals]  # only the top decile rises
    rows = decile_lift(vals, labels)
    assert len(rows) == 10 and rows[-1].rate == 1.0 and round(rows[-1].lift, 1) == 10.0 and rows[0].lift == 0.0
    assert decile_lift([1.0] * 50, [1] * 50) == []  # too few rows
    lo, hi = wilson(50, 1000)
    assert lo < 0.05 < hi and hi - lo < 0.03
    assert size_bucket(0.07) == "5–10 %" and size_bucket(0.25) == "> 20 %"
    assert vol_bucket(0.01).startswith("calme") and vol_bucket(None) == "inconnue"
