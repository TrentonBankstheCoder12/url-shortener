import os
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Base, make_engine
from app.main import create_app


class FakeClock:
    """Lets tests jump forward in time instead of sleeping."""

    def __init__(self):
        self.now = datetime(2026, 1, 1, 12, 0, 0)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture()
def clock():
    return FakeClock()


# Default: a fresh in-memory SQLite per test. Set TEST_DATABASE_URL to run the same suite
# against Postgres (tables are dropped after each test).
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "sqlite://")


@pytest.fixture()
def make_client(clock):
    """Build a client with a fresh in-memory DB; override settings per test as needed."""
    clients = []

    def factory(**overrides) -> TestClient:
        options = {
            "database_url": TEST_DATABASE_URL,
            "base_url": "http://sho.rt",
            "rate_limit_capacity": 1000,
        }
        settings = Settings(**{**options, **overrides})
        client = TestClient(create_app(settings, clock=clock), follow_redirects=False)
        client.__enter__()  # runs the lifespan (creates tables, cache, limiter)
        clients.append(client)
        return client

    yield factory
    for c in clients:
        c.__exit__(None, None, None)
    if TEST_DATABASE_URL != "sqlite://":
        engine = make_engine(TEST_DATABASE_URL)
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def client(make_client):
    return make_client()
