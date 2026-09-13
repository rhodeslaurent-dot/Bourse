"""Pydantic schemas for config/params.yaml.

Required groups (``config_validation.required_groups``) are validated strictly: a missing or
invalid required group refuses startup. Optional groups only disable the feature they drive.
No business number is hard-coded here: defaults are absent on purpose for business values.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Mode = Literal["disponible", "reunion", "absent"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow")


# --- required groups -------------------------------------------------------------------------


class Timezones(_Model):
    storage: str = "UTC"
    market: str
    display: str


class Capital(_Model):
    effective_from: date
    capital_pilote_eur: float = Field(gt=0)
    cash_additionnel_max_eur: float = Field(ge=0)


class CircuitBreaker(_Model):
    weekly_loss_r: float
    monthly_loss_r: float
    resume_size_multiplier: float


class StopParams(_Model):
    policy: str
    buffer_atr_mult: float
    max_distance_adr: float
    protection_order_type_default: str
    unconfirmed_exit_minutes: int
    trailing_1r: dict[str, Any]
    trailing_2r: dict[str, Any]
    move_to_breakeven_at_r: float
    reduce_at_target1_pct: float


class Risk(_Model):
    capital_base: str
    max_risk_per_trade_pct: float = Field(gt=0, lt=0.1)
    slippage_pct: float = Field(ge=0)
    risk_current_floor_zero: bool
    post_fill_risk_tolerance: float
    max_position_pct_of_capital: float
    max_position_pct_of_adv: float
    max_open_risk_pct: float
    max_sector_risk_pct: float
    max_factor_risk_pct: float
    factor_groups: dict[str, list[str]] = Field(default_factory=dict)
    gap_scenario_pct: float
    positions_max: int
    diversification_hints: dict[str, float]
    exposure: dict[str, float]
    size_multipliers: dict[str, float]
    fx_haircut: float
    max_fx_exposure_pct: float
    max_positions_per_sector: int
    max_positions_per_sector_green_high_score: int
    sector_third_position_min_ratio: float
    max_exposure_per_non_paris_market_pct: float
    srd_max_leverage: float
    srd_abs_max_leverage: float
    srd_cash_reserve_pct: float
    srd_coverage_buffer_alert: float
    srd_coverage_buffer_block: float
    srd_max_cost_pct_of_target1: float
    earnings_blackout_hours: int
    earnings_policy_on_position: str
    circuit_breaker: CircuitBreaker
    revenge_trade_cooldown_sessions: int
    no_new_buy_after_local: str
    no_new_buy_before_local: str
    stop: StopParams
    pyramiding: dict[str, Any]


class AccountPea(_Model):
    broker: str
    order_types_stop: list[str]
    entry_order_types: list[str]
    linked_orders_if_done: bool
    import_: str = Field(alias="import")
    import_reminder_days: int
    state_max_age_hours: int


class AccountCto(_Model):
    broker: str
    order_types_stop: list[str]
    entry_order_types: list[str]
    linked_orders_if_done: bool
    import_: str = Field(alias="import")
    sync_intraday_minutes: int
    sync_time_local: str
    match_tolerance: dict[str, float]


class Accounts(_Model):
    portfolio_state_max_age_minutes: int
    pea: AccountPea
    cto: AccountCto


class Notifications(_Model):
    telegram: dict[str, Any]
    email: dict[str, Any]
    dedup_key: list[str]
    p1_grouping: str
    p1_repeat_minutes: int
    unread_escalation_minutes: int


class Data(_Model):
    primary_eod: str
    primary_realtime: str
    secondary_realtime: str | None = None
    screener: str | None = None
    fallback_eod: str | None = None
    buy_intraday_max_market_age_minutes: int
    perimeter_consistency_required: bool = True
    silent_source_switch_allowed: bool = False
    stale_after_minutes: dict[str, int]
    eodhd: dict[str, Any] = Field(default_factory=dict)
    tradingview_screener: dict[str, Any] = Field(default_factory=dict)
    rss_feeds: list[dict[str, Any]] = Field(default_factory=list)
    imap: dict[str, Any] = Field(default_factory=dict)


class JobSpec(_Model):
    """One scheduled job. Cron is in Europe/Paris unless ``tz`` says otherwise."""

    cron: str | None = None
    every_minutes: int | None = None
    every_seconds: int | None = None
    every_minutes_from: str | None = None
    times_from: str | None = None
    window_local: list[str] | None = None
    days: str | None = None
    tz: str | None = None
    half_day: str | None = None
    half_day_end: str | None = None
    misfire_grace_min: int | None = None
    market_days_only: bool = True
    enabled: bool = True
    also_after: list[str] | None = None
    premarket: dict[str, Any] | None = None
    day: dict[str, Any] | None = None


class Jobs(_Model):
    model_config = ConfigDict(extra="allow")

    @field_validator("*", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        return v

    def specs(self) -> dict[str, JobSpec]:
        out: dict[str, JobSpec] = {}
        extra = self.model_extra or {}
        for name, raw in extra.items():
            if isinstance(raw, dict):
                out[name] = JobSpec.model_validate(raw)
        return out


# --- optional groups -------------------------------------------------------------------------


class ScheduleSlot(_Model):
    days: str
    mode: Mode
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    ref: Literal["clock", "market_open"] | None = None
    offset_min: list[int] | None = None
    confirmed: bool = True


class Availability(_Model):
    default_mode: Mode
    schedule: list[ScheduleSlot] = Field(default_factory=list)
    unprotected_fill_alert_level: str = "P1"
    quotas: dict[str, dict[str, int]] = Field(default_factory=dict)
    d1_allowed_modes: list[Mode] = Field(default_factory=lambda: list[Mode](["disponible"]))


class TaxVersion(_Model):
    effective_from: date
    social_contributions_rate: float | None = None
    pfu_income_tax_rate: float | None = None
    pfu_total_rate: float | None = None


class Llm(_Model):
    provider: str
    classify_model: str
    write_model: str
    temperature: float = 0
    monthly_budget_usd: float
    cache: bool = True
    prompt_versions_dir: str = "app/llm/prompts"


class SrdParams(_Model):
    effective_from: date
    calendar_2026: list[list[date]]
    calendar_2027_provisional: list[date] = Field(default_factory=list)
    crd_daily_rate: float
    prorogation_rate: float
    prorogation_min_eur: float
    prorogation_deadline_local: str | None = None
    coverage_rate_broker: float | None = None


class ConfigValidation(_Model):
    required_groups: list[str]
    optional_groups: list[str] = Field(default_factory=list)


class Params(BaseModel):
    """Whole params.yaml. Required groups are typed; optional groups may be ``None``."""

    model_config = ConfigDict(extra="allow")

    version: str
    timezones: Timezones
    capital: Capital
    risk: Risk
    accounts: Accounts
    notifications: Notifications
    data: Data
    jobs: Jobs
    config_validation: ConfigValidation
    user: dict[str, Any] = Field(default_factory=dict)
    availability: Availability | None = None
    tax: list[TaxVersion] | None = None
    llm: Llm | None = None
    srd: SrdParams | None = None
    brokers: dict[str, Any] | None = None
    detectors: dict[str, Any] | None = None
    scoring: dict[str, Any] | None = None
    universe: dict[str, Any] | None = None
    regime: dict[str, Any] | None = None
    pea: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    subscriptions: list[dict[str, Any]] | None = None
