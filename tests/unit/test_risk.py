"""Rule 7 on another author's levels (17/11/2026): the sizing inputs stored with a proposal, replayed."""

from __future__ import annotations

from app.domain.risk import EntryOrderKind, FeeSchedule, SizingInput, size_position


def test_stored_sizing_inputs_size_other_levels_by_the_same_rule():
    """17/11/2026 — the AI's levels are sized by Python with the proposal's own inputs (rule 7)."""
    from app.domain.risk import resize, sizing_input_to_dict

    inp = SizingInput(capital_eur=10_000, max_risk_pct=0.01, entry_level=100.0, stop_initial=95.0,
                      order_kind=EntryOrderKind.LIMIT, slippage_pct=0.002,
                      fees=FeeSchedule("bourso", 0.005, 1.99, bands=((500.0, 0.0, 0.0, 1.99),)),
                      max_position_pct_of_capital=0.25, max_position_pct_of_adv=0.05, adv20_eur=1e7)  # fmt: skip
    stored = sizing_input_to_dict(inp)
    assert size_position(inp).shares == resize(stored, 100.0, 95.0).shares  # same levels, same size
    assert resize(stored, 100.0, 97.0).shares > resize(stored, 100.0, 95.0).shares  # tighter stop, more shares
