from datetime import datetime, time, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.config import TZ
from app.logging_config import silence_http_logging
from app.models import Notification, Occurrence, Recurring
from app.services import CLOSED, lock_owner, paid_amount


def scheduled_at(due, offset):
    local = datetime.combine(due - timedelta(days=offset), time(11), tzinfo=TZ)
    return local.astimezone(timezone.utc)


def reconcile(db, owner):
    lock_owner(db, owner)
    occurrences = list(db.scalars(select(Occurrence).join(Recurring).where(Recurring.owner_id == owner)))
    for occ in occurrences:
        jobs = list(db.scalars(select(Notification).where(Notification.occurrence_id == occ.id)))
        for job in jobs:
            if job.status == "pending" and (job.due_snapshot != occ.due_date or occ.status in CLOSED):
                job.status = "cancelled"
            elif job.status == "cancelled" and job.due_snapshot == occ.due_date and occ.status not in CLOSED:
                job.status = "pending"
        if occ.status in CLOSED:
            continue
        for offset in (3, 1, 0):
            db.execute(
                insert(Notification)
                .values(
                    occurrence_id=occ.id,
                    due_snapshot=occ.due_date,
                    days_before=offset,
                    scheduled_at=scheduled_at(occ.due_date, offset),
                    status="pending",
                    attempts=0,
                )
                .on_conflict_do_nothing(constraint="uq_notification")
            )
    db.flush()


def telegram_send(settings, message):
    silence_http_logging()
    # Token is never put into logs; avoid logging HTTP exceptions (their URLs contain it).
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token.get_secret_value()}/sendMessage"
    with httpx.Client(timeout=10, trust_env=False) as client:
        response = client.post(url, json={"chat_id": settings.owner_telegram_id, "text": message})
        response.raise_for_status()
        if not response.json().get("ok"):
            raise RuntimeError("Telegram returned ok=false")


def dispatch(db, settings, now=None, send=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Timezone-aware datetime required")
    owner = settings.owner_telegram_id
    lock_owner(db, owner)
    reconcile(db, owner)
    jobs = list(
        db.scalars(
            select(Notification)
            .join(Occurrence)
            .join(Recurring)
            .where(
                Recurring.owner_id == owner,
                Notification.status == "pending",
                Notification.scheduled_at <= now,
            )
            .order_by(Notification.scheduled_at)
            .with_for_update(of=Notification, skip_locked=True)
        )
    )
    sent = 0
    for job in jobs:
        if job.scheduled_at.astimezone(TZ).date() < now.astimezone(TZ).date():
            job.status = "expired"  # Do not flood the user with old reminders after an outage.
            continue
        if not settings.telegram_notifications_enabled:
            continue
        if job.next_attempt_at and job.next_attempt_at > now:
            continue
        occ = db.scalar(select(Occurrence).where(Occurrence.id == job.occurrence_id).with_for_update())
        if occ.status in CLOSED or occ.due_date != job.due_snapshot:
            job.status = "cancelled"
            continue
        row = db.get(Recurring, occ.recurring_id)
        remaining = occ.expected_amount - paid_amount(db, occ.id)
        message = f"Платёжник: {row.title}\nСрок: {occ.due_date:%d.%m.%Y}\nОсталось: {remaining:.2f} RUB"
        job.attempts += 1
        try:
            (send or (lambda msg: telegram_send(settings, msg)))(message)
        except Exception as exc:
            # Store only the class, not exception text, URLs, headers or tokens.
            job.last_error = type(exc).__name__
            job.next_attempt_at = now + timedelta(seconds=min(3600, 60 * 2 ** min(job.attempts - 1, 6)))
        else:
            job.status = "sent"
            job.sent_at = now
            job.last_error = None
            job.next_attempt_at = None
            sent += 1
    db.flush()
    return sent
