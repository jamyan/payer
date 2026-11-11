import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.main import app

TOKEN = "test-only-api-token-at-least-32-characters"


@pytest.fixture
def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated PostgreSQL database ending in _test")
    parsed = make_url(url)
    # This suite truncates tables: never accept the normal application database.
    if parsed.get_backend_name() != "postgresql" or not (parsed.database or "").endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must be a PostgreSQL database ending in _test")
    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE login_codes, login_guards, browser_sessions, bot_replies, bot_states, notifications, audit_log, payments, occurrences, tariffs, recurring_payments CASCADE"
            )
        )
    yield engine
    engine.dispose()


@pytest.fixture
def client(engine, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("LOCAL_API_TOKEN", TOKEN)
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "1")
    monkeypatch.setenv("TELEGRAM_NOTIFICATIONS_ENABLED", "false")
    get_settings.cache_clear()

    def db_override():
        with Session(engine, expire_on_commit=False) as db, db.begin():
            yield db

    app.dependency_overrides[get_db] = db_override
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client
    app.dependency_overrides.clear()
    get_settings.cache_clear()
