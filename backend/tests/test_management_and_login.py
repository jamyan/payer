from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.browser_auth import issue_code, revoke_sessions
from app.config import get_settings
from app.models import BrowserSession, LoginCode, LoginGuard, Occurrence, Payment, Recurring, utcnow
from app.services import today
from tests.test_api import create, pay
from tests.test_bot_dialog import run


def test_edit_title_tariff_variable_preserves_paid(client):
    ident, occ = create(client)
    pay(client, occ, "1000")
    result = client.patch(
        f"/recurring/{ident}", json={"title": "Новый интернет", "amount": "7500", "variable": False}
    )
    assert result.status_code == 200, result.text
    assert result.json()["title"] == "Новый интернет"
    rows = client.get("/occurrences").json()
    assert Decimal(next(o for o in rows if o["id"] == occ)["expected_amount"]) == 6500
    assert all(Decimal(o["expected_amount"]) == 7500 for o in rows if o["id"] != occ)
    assert client.get("/audit", params={"entity_id": ident}).json()[-1]["action"] == "recurring_edited"


def test_schedule_edit_keeps_history_and_no_double_charge_same_day(client, engine):
    ident, occ = create(client)
    pay(client, occ, "6500")
    result = client.patch(
        f"/recurring/{ident}", json={"frequency": "weeks", "anchor_date": today().isoformat()}
    )
    assert result.status_code == 200, result.text
    assert client.post("/occurrences/generate").json()["created"] == 0
    with Session(engine) as db:
        assert db.get(Occurrence, UUID(occ)).status == "paid"
        assert db.scalar(select(func.sum(Payment.amount))) == 6500
        active = list(
            db.scalars(
                select(Occurrence).where(
                    Occurrence.recurring_id == UUID(ident),
                    Occurrence.status.in_(["pending", "partial", "paid"]),
                )
            )
        )
        assert len({o.scheduled_date for o in active}) == len(active)
        assert any(o.schedule_revision == 1 for o in active)
        assert (
            db.scalar(select(func.count()).select_from(Occurrence).where(Occurrence.status == "cancelled"))
            > 0
        )


def test_schedule_edit_rejects_backdated_anchor(client):
    ident, _ = create(client)
    assert (
        client.patch(
            f"/recurring/{ident}", json={"anchor_date": (today() - timedelta(days=1)).isoformat()}
        ).status_code
        == 422
    )


def test_move_future_start_earlier_does_not_revert_new_tariff(client, engine):
    from app.services import generate

    ident, _ = create(client, anchor_date=(today() + timedelta(days=60)).isoformat())
    assert (
        client.patch(
            f"/recurring/{ident}", json={"anchor_date": today().isoformat(), "amount": "7000"}
        ).status_code
        == 200
    )
    with Session(engine) as db, db.begin():
        generate(db, 1, today() + timedelta(days=120))
        rows = list(
            db.scalars(
                select(Occurrence).where(
                    Occurrence.recurring_id == UUID(ident), Occurrence.status == "pending"
                )
            )
        )
        assert len(rows) > 4 and all(o.expected_amount == 7000 for o in rows)


def test_delete_archives_and_keeps_partial_history(client, engine):
    ident, occ = create(client)
    pay(client, occ, "1000")
    assert client.delete(f"/recurring/{ident}").status_code == 200
    assert client.get("/recurring").json() == []
    assert len(client.get("/recurring", params={"include_deleted": True}).json()) == 1
    rows = client.get("/occurrences").json()
    assert next(o for o in rows if o["id"] == occ)["status"] == "partial"
    assert all(o["status"] == "cancelled" for o in rows if o["id"] != occ)
    assert pay(client, occ, "5500").status_code == 200
    assert client.delete(f"/recurring/{ident}").status_code == 200
    assert client.patch(f"/recurring/{ident}", json={"title": "Изменить"}).status_code == 409
    assert client.post("/occurrences/generate").json()["created"] == 0
    with Session(engine) as db:
        assert db.scalar(select(func.sum(Payment.amount))) == 6500


def test_other_owner_cannot_edit_delete(client, engine):
    ident, _ = create(client)
    with Session(engine) as db, db.begin():
        db.get(Recurring, UUID(ident)).owner_id = 2
    assert client.patch(f"/recurring/{ident}", json={"title": "Чужой"}).status_code == 404
    assert client.delete(f"/recurring/{ident}").status_code == 404


@pytest.fixture
def browser(client, monkeypatch):
    client.headers.pop("authorization", None)
    client.headers.update({"Origin": "http://testserver", "X-Platezhnik-Client": "web"})
    monkeypatch.setenv("APP_URL", "http://testserver")
    get_settings.cache_clear()
    return client


def code(engine, owner=1):
    with Session(engine) as db, db.begin():
        return issue_code(db, owner)


def test_browser_code_login_cookie_and_reload(browser, engine):
    value = code(engine)
    response = browser.post("/auth/login", json={"code": value})
    assert response.status_code == 200, response.text
    assert (
        "HttpOnly" in response.headers["set-cookie"] and "SameSite=strict" in response.headers["set-cookie"]
    )
    assert browser.get("/settings").status_code == 200
    assert browser.post("/auth/login", json={"code": value}).status_code == 401
    with Session(engine) as db:
        stored = db.scalar(select(BrowserSession))
        assert len(stored.token_hash) == 64
        assert stored.token_hash != browser.cookies.get("pt_session")
        assert db.get(LoginCode, 1) is None
    assert browser.post("/auth/logout").status_code == 200
    assert browser.get("/settings").status_code == 401


def test_browser_csrf_rejected_and_cookie_expiry(browser, engine):
    assert browser.post("/auth/login", json={"code": code(engine)}).status_code == 200
    assert (
        browser.post("/occurrences/generate", headers={"Origin": "https://evil.example"}).status_code == 403
    )
    assert browser.post("/occurrences/generate", headers={"X-Platezhnik-Client": ""}).status_code == 403
    assert browser.post("/occurrences/generate").status_code == 200
    with Session(engine) as db, db.begin():
        db.scalar(select(BrowserSession)).expires_at = utcnow() - timedelta(seconds=1)
    assert browser.get("/settings").status_code == 401


def test_wrong_code_rate_limit_persists_and_new_code_resets(browser, engine):
    code(engine)
    for _ in range(10):
        assert browser.post("/auth/login", json={"code": "WRONG-CODE"}).status_code == 401
    assert browser.post("/auth/login", json={"code": "WRONG-CODE"}).status_code == 429
    with Session(engine) as db:
        assert db.get(LoginGuard, 1).failures == 10
    assert browser.post("/auth/login", json={"code": code(engine)}).status_code == 200


def test_expired_replaced_code_and_revoke(browser, engine):
    old = code(engine)
    new = code(engine)
    assert browser.post("/auth/login", json={"code": old}).status_code == 401
    with Session(engine) as db, db.begin():
        db.get(LoginCode, 1).expires_at = utcnow() - timedelta(seconds=1)
    assert browser.post("/auth/login", json={"code": new}).status_code == 401
    assert browser.post("/auth/login", json={"code": code(engine)}).status_code == 200
    with Session(engine) as db, db.begin():
        revoke_sessions(db, 1)
    assert browser.get("/settings").status_code == 401


def test_login_requires_same_origin_before_consuming_code(browser, engine):
    value = code(engine)
    assert (
        browser.post(
            "/auth/login", json={"code": value}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert browser.post("/auth/login", json={"code": value}).status_code == 200


def test_bot_login_redacts_outbox_and_report_preserves_draft(engine):
    telegram = run(engine, ["/add", "Интернет", "/login"])
    message = telegram.calls[-1][1]["text"]
    assert "Код входа" in message
    with Session(engine) as db:
        from app.models import BotReply, BotState

        assert db.get(BotState, 42).step == "amount"
        assert (
            db.scalar(select(BotReply).where(BotReply.update_id == 3)).text
            == "Одноразовый код отправлен (скрыт после доставки)"
        )
    run(engine, ["/remaining", "/summary", "/next", "/overdue", "/logout"], start=4)
    with Session(engine) as db:
        from app.models import BotState

        assert db.get(BotState, 42).step == "amount"
        assert db.get(LoginCode, 42) is None


def test_bot_reports_actual_debts_and_partial_amounts(client, engine):
    ident, occ = create(client, amount="1000")
    pay(client, occ, "300")
    with Session(engine) as db, db.begin():
        db.get(Recurring, UUID(ident)).owner_id = 42
        db.get(Occurrence, UUID(occ)).due_date = today() - timedelta(days=1)
    telegram = run(engine, ["/remaining", "/overdue", "/next", "/summary"])
    assert "700.00 RUB" in telegram.calls[0][1]["text"]
    assert "просрочен" in telegram.calls[1][1]["text"]
    assert "700.00 RUB" in telegram.calls[2][1]["text"]
    assert "Просрочено за все даты: 700.00 RUB" in telegram.calls[3][1]["text"]


def test_production_cookie_flags(browser, engine, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "telegram")
    monkeypatch.setenv("APP_URL", "https://pay.example.com")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake-token-not-used-for-network")
    get_settings.cache_clear()
    with TestClient(
        app,
        base_url="https://pay.example.com",
        headers={"Origin": "https://pay.example.com", "X-Platezhnik-Client": "web"},
    ) as c:
        result = c.post("/auth/login", json={"code": code(engine)})
        assert result.status_code == 200
        header = result.headers["set-cookie"]
        assert "__Host-pt_session=" in header and "Secure" in header and "HttpOnly" in header
        assert "SameSite=strict" in header and "Domain=" not in header
        assert c.get("/settings").status_code == 200


def test_one_time_code_concurrent_consumption(browser, engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from fastapi.testclient import TestClient

    from app.main import app

    value = code(engine)
    barrier = Barrier(2)

    def attempt(_):
        with TestClient(app, headers={"Origin": "http://testserver", "X-Platezhnik-Client": "web"}) as c:
            barrier.wait(timeout=10)
            return c.post("/auth/login", json={"code": value}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [200, 401]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(BrowserSession)) == 1
