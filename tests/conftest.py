from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.config import validate_params
from app.db.session import configure_engine, create_all_for_tests
from app.domain.calendar import MarketCalendars

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def example_raw() -> dict:
    return yaml.safe_load((ROOT / "config" / "params.example.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def raw(example_raw: dict) -> dict:
    return copy.deepcopy(example_raw)


@pytest.fixture
def config(raw: dict):
    return validate_params(raw, path=ROOT / "config" / "params.example.yaml", sha256="test")


@pytest.fixture(scope="session")
def calendars() -> MarketCalendars:
    return MarketCalendars.from_dict(yaml.safe_load((ROOT / "config" / "market_calendars.yaml").read_text()))


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_all_for_tests(engine)
    configure_engine(engine)
    yield engine
    engine.dispose()
