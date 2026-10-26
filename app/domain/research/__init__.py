"""Trigger research harness (docs/31, pre-registered plan ``research/prereg/2026-10-27_reg1.yaml``).

Pure, typed, numpy only: panels of daily bars per venue group, vectorised features and triggers, a
vectorised exit engine equivalent to ``app.domain.backtest.simulate_trade``, costs per account, inference
(Newey–West, block bootstrap, Romano–Wolf, Holm, BH, PBO, DSR) and the mechanical A1–A13 classification.
No I/O: the service ``app.services.research`` reads the database and writes the aggregated report.
"""
