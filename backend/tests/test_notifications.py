from datetime import timedelta, timezone
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import services
from app.config import Settings
from app.models import Notification
from app.notifications import dispatch, reconcile, scheduled_at, telegram_send
from tests.test_api import create, pay

pytestmark = pytest.mark.integration


def settings():
    return Settings(
        auth_mode="telegram",
        owner_telegram_id=1,
        telegram_bot_token="fake-token",
        telegram_notifications_enabled=True,
    )


def test_schedule_irkutsk():
    due = services.today()
    for offset in (3, 1, 0):
        value = scheduled_at(due, offset)
        assert value.hour == 3 and value.tzinfo == timezone.utc
        assert value.date() == due - timedelta(days=offset)


def test_reminder_at_11_partial_remaining_and_no_duplicate(client, engine):
    _, occ = create(client)
    pay(client, occ, "1000")
    now = scheduled_at(services.today(), 0)
    messages = []
    with Session(engine) as db, db.begin():
        assert dispatch(db, settings(), now - timedelta(seconds=1), messages.append) == 0
    with Session(engine) as db, db.begin():
        assert dispatch(db, settings(), now, messages.append) == 1
    assert "5500.00 RUB" in messages[0]
    with Session(engine) as db, db.begin():
        assert dispatch(db, settings(), now + timedelta(minutes=1), messages.append) == 0
        statuses = list(
            db.scalars(select(Notification.status).where(Notification.occurrence_id == UUID(occ)))
        )
        assert sorted(statuses) == ["expired", "expired", "sent"]
    assert len(messages) == 1


@pytest.mark.parametrize("offset", [3, 1, 0])
def test_each_offset_sends_on_the_correct_day(client, engine, offset):
    due = services.today() + timedelta(days=offset)
    create(client, anchor_date=due.isoformat())
    messages = []
    with Session(engine) as db, db.begin():
        assert dispatch(db, settings(), scheduled_at(due, offset), messages.append) == 1
    assert len(messages) == 1
    assert due.strftime("%d.%m.%Y") in messages[0]


def test_fully_paid_never_sends(client, engine):
    _, occ = create(client)
    pay(client, occ, "6500")
    messages = []
    with Session(engine) as db, db.begin():
        dispatch(db, settings(), scheduled_at(services.today(), 0), messages.append)
    assert messages == []


def test_due_date_change_cancels_old_and_replans(client, engine):
    _, occ = create(client)
    with Session(engine) as db, db.begin():
        reconcile(db, 1)
    new_due = services.today() + timedelta(days=10)
    response = client.patch(f"/occurrences/{occ}", json={"due_date": new_due.isoformat()})
    assert response.status_code == 200
    with Session(engine) as db, db.begin():
        reconcile(db, 1)
        jobs = list(db.scalars(select(Notification).where(Notification.occurrence_id == UUID(occ))))
        assert len(jobs) == 6
        assert all(j.status == "cancelled" for j in jobs if j.due_snapshot != new_due)
        assert all(j.status == "pending" for j in jobs if j.due_snapshot == new_due)
        assert {j.days_before for j in jobs if j.due_snapshot == new_due} == {3, 1, 0}


def test_failure_retries_without_token_leak(client, engine):
    _, occ = create(client)
    now = scheduled_at(services.today(), 0)

    def failure(msg):
        raise RuntimeError("fake-token must never be stored")

    with Session(engine) as db, db.begin():
        assert dispatch(db, settings(), now, failure) == 0
        job = db.scalar(
            select(Notification).where(Notification.occurrence_id == UUID(occ), Notification.days_before == 0)
        )
        assert job.attempts == 1 and job.last_error == "RuntimeError"
    messages = []
    with Session(engine) as db, db.begin():
        assert dispatch(db, settings(), now + timedelta(seconds=30), messages.append) == 0
    with Session(engine) as db, db.begin():
        assert dispatch(db, settings(), now + timedelta(seconds=61), messages.append) == 1
    assert len(messages) == 1


def test_disabled_does_not_send(client, engine):
    create(client)
    disabled = settings().model_copy(update={"telegram_notifications_enabled": False})
    with Session(engine) as db, db.begin():
        assert (
            dispatch(
                db, disabled, scheduled_at(services.today(), 0), lambda msg: pytest.fail("Must not send")
            )
            == 0
        )


def test_send_transport_is_mocked(monkeypatch):
    seen = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == 10

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, json):
            seen.append(json)
            return httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "Client", Client)
    telegram_send(settings(), "Тест")
    assert seen == [{"chat_id": 1, "text": "Тест"}]
