"""A0.6: required group missing → startup refused; optional missing → feature disabled only."""

import pytest

from app.config import ConfigError, validate_params


def test_example_params_load(config):
    assert config.params.capital.capital_pilote_eur == 40000
    assert config.params.risk.max_risk_per_trade_pct == 0.0075
    assert all(f.enabled for f in config.features), [f for f in config.features if not f.enabled]


@pytest.mark.parametrize("group", ["timezones", "capital", "risk", "accounts", "notifications", "data", "jobs"])
def test_missing_required_group_refuses_startup(raw, group):
    del raw[group]
    with pytest.raises(ConfigError) as exc:
        validate_params(raw)
    assert group in str(exc.value)
    assert "refusé" in str(exc.value)


def test_invalid_required_value_refuses_startup(raw):
    raw["risk"]["max_risk_per_trade_pct"] = "beaucoup"
    with pytest.raises(ConfigError) as exc:
        validate_params(raw)
    assert "risk" in str(exc.value)


def test_missing_optional_tax_disables_only_tax(raw):
    del raw["tax"]
    cfg = validate_params(raw)
    disabled = {f.key: f for f in cfg.disabled_features}
    assert set(disabled) == {"tax"}
    assert "absent" in disabled["tax"].reason
    assert cfg.params.risk.stop.unconfirmed_exit_minutes == 5  # stops keep working


def test_invalid_optional_llm_disables_llm_only(raw):
    raw["llm"]["monthly_budget_usd"] = "gratuit"
    cfg = validate_params(raw)
    assert [f.key for f in cfg.disabled_features] == ["llm"]
    assert cfg.params.llm is None
    assert cfg.feature_enabled("tax")


def test_missing_availability_schedule_disables_schedule_feature(raw):
    del raw["availability"]["schedule"]
    cfg = validate_params(raw)
    assert not cfg.feature_enabled("availability.schedule")
    assert cfg.params.availability is not None and cfg.params.availability.default_mode == "reunion"


def test_required_groups_come_from_config_validation(raw):
    raw["config_validation"]["required_groups"] = [
        "timezones",
        "capital",
        "risk",
        "accounts",
        "notifications",
        "data",
        "jobs",
        "tax",
    ]
    del raw["tax"]
    with pytest.raises(ConfigError):
        validate_params(raw)


def test_jobs_specs_parsed(config):
    specs = config.params.jobs.specs()
    assert specs["backup"].tz == "Indian/Reunion" and specs["backup"].market_days_only is False
    assert specs["eod_pipeline"].half_day == "14:20"
    assert specs["open_scan"].enabled is False
