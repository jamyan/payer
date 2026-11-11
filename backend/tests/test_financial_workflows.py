from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import schemas, services
from app.bot import handle_update
from app.models import Audit, BotState, Occurrence, Payment, PaymentCorrection, Recurring
from tests.test_api import create, pay
from tests.test_bot_dialog import run, update
from tests.test_web_and_bot import settings

pytestmark = pytest.mark.integration


def correction(client, payment, amount, revision=0, **extra):
    body = dict(
        amount=amount,
        paid_on=services.today().isoformat(),
        reason="Ошибка в сумме",
        expected_revision=revision,
        idempotency_key=str(uuid4()),
    )
    body.update(extra)
    return client.post(f"/payments/{payment}/corrections", json=body)


def test_correction_preserves_original_and_reopens_bill(client, engine):
    _, occ = create(client, amount="1000")
    p = pay(client, occ, "1000").json()
    result = correction(client, p["id"], "400")
    assert result.status_code == 200, result.text
    assert result.json()["amount"] == "400.00"
    assert result.json()["original_amount"] == "1000.00"
    row = client.get(f"/occurrences/{occ}").json()
    assert row["status"] == "partial" and Decimal(row["remaining_amount"]) == 600
    result = correction(client, p["id"], "0", revision=1)
    assert result.status_code == 200 and result.json()["cancelled"]
    assert client.get(f"/occurrences/{occ}").json()["status"] == "pending"
    with Session(engine) as db:
        assert db.get(Payment, UUID(p["id"])).amount == 1000
        assert db.scalar(select(func.count()).select_from(PaymentCorrection)) == 2
        assert (
            db.scalar(select(func.count()).select_from(Audit).where(Audit.action == "payment_corrected")) == 2
        )


def test_correction_retry_and_stale_revision(client, engine):
    _, occ = create(client, amount="1000")
    p = pay(client, occ, "300").json()
    key = str(uuid4())
    assert correction(client, p["id"], "400", idempotency_key=key).status_code == 200
    assert correction(client, p["id"], "400", idempotency_key=key).status_code == 200
    assert correction(client, p["id"], "500", idempotency_key=key).status_code == 409
    assert correction(client, p["id"], "500").status_code == 409
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(PaymentCorrection)) == 1


def test_correction_cannot_overpay_or_access_another_owner(client, engine):
    _, occ = create(client, amount="1000")
    p = pay(client, occ, "300").json()
    pay(client, occ, "500")
    assert correction(client, p["id"], "501").status_code == 409
    with Session(engine) as db:
        assert services.paid_amount(db, UUID(occ)) == 800
        assert db.scalar(select(func.count()).select_from(PaymentCorrection)) == 0
    with Session(engine) as db, db.begin():
        db.get(Payment, UUID(p["id"])).owner_id = 42
    assert correction(client, p["id"], "200").status_code == 404


@pytest.mark.parametrize(
    "field,value", [("amount", "-1"), ("amount", "1.001"), ("reason", " "), ("expected_revision", -1)]
)
def test_correction_validation(client, field, value):
    _, occ = create(client)
    p = pay(client, occ, "100").json()
    args = {"amount": "50", field: value}
    assert correction(client, p["id"], **args).status_code == 422


def test_no_future_payment_dates_and_noop(client):
    _, occ = create(client)
    future = (services.today() + timedelta(days=1)).isoformat()
    assert pay(client, occ, "100", paid_on=future).status_code == 422
    p = pay(client, occ, "100").json()
    assert correction(client, p["id"], "50", paid_on=future).status_code == 422
    assert correction(client, p["id"], "100").status_code == 422


def test_cash_month_differs_from_due_month_and_corrections_move_date(client, engine):
    _, occ = create(client, amount="1000")
    first = services.today().replace(day=1)
    old = first - timedelta(days=1)
    with Session(engine) as db, db.begin():
        db.get(Occurrence, UUID(occ)).due_date = old
    p = pay(client, occ, "300", paid_on=services.today().isoformat()).json()
    summary = client.get("/summary").json()
    assert summary["expected_due"] == "0.00" and summary["paid_for_due"] == "0.00"
    assert summary["cash_paid"] == "300.00" and summary["old_debt"] == "700.00"
    assert correction(client, p["id"], "200", paid_on=old.isoformat()).status_code == 200
    summary = client.get("/summary").json()
    assert summary["cash_paid"] == "0.00" and summary["old_debt"] == "800.00"
    assert client.get("/summary", params={"month": old.isoformat()}).json()["cash_paid"] == "200.00"


def test_archiving_and_restoring_preserves_history_and_partial(client, engine):
    ident, occ = create(client, amount="1000")
    pay(client, occ, "300")
    assert client.delete(f"/recurring/{ident}").status_code == 200
    assert all(r["id"] != ident for r in client.get("/recurring").json())
    response = client.post(f"/recurring/{ident}/restore", json={"first_date": services.today().isoformat()})
    assert response.status_code == 200, response.text
    assert response.json()["deleted_at"] is None and response.json()["active"]
    rows = [o for o in client.get("/occurrences").json() if o["recurring_id"] == ident]
    assert (
        len(
            [
                o
                for o in rows
                if o["scheduled_date"] == services.today().isoformat() and o["status"] != "cancelled"
            ]
        )
        == 1
    )
    assert client.get(f"/occurrences/{occ}").json()["paid_amount"] == "300.00"
    assert (
        client.post(
            f"/recurring/{ident}/restore", json={"first_date": services.today().isoformat()}
        ).status_code
        == 409
    )
    with Session(engine) as db:
        assert (
            db.scalar(select(func.count()).select_from(Audit).where(Audit.action == "recurring_restored"))
            == 1
        )


def test_restore_does_not_resurrect_old_cancelled_debts(client, engine):
    ident, occ = create(client, amount="1000")
    assert client.delete(f"/recurring/{ident}").status_code == 200
    assert (
        client.post(
            f"/recurring/{ident}/restore",
            json={"first_date": (services.today() - timedelta(days=1)).isoformat()},
        ).status_code
        == 422
    )
    future = services.today() + timedelta(days=5)
    assert (
        client.post(f"/recurring/{ident}/restore", json={"first_date": future.isoformat()}).status_code == 200
    )
    assert client.get(f"/occurrences/{occ}").json()["status"] == "cancelled"
    rows = [
        o
        for o in client.get("/occurrences").json()
        if o["recurring_id"] == ident and o["status"] == "pending"
    ]
    assert rows and min(o["due_date"] for o in rows) == future.isoformat()
    with Session(engine) as db:
        assert services.paid_amount(db, UUID(occ)) == 0


def test_correction_archived_payment_does_not_create_unpaid_debt(client):
    ident, occ = create(client, amount="1000")
    p = pay(client, occ, "1000").json()
    client.delete(f"/recurring/{ident}")
    assert correction(client, p["id"], "0").status_code == 200
    assert client.get(f"/occurrences/{occ}").json()["status"] == "cancelled"
    assert (
        correction(
            client, p["id"], "1000", revision=1, reason="Отмена исправления была ошибочной"
        ).status_code
        == 200
    )
    assert client.get(f"/occurrences/{occ}").json()["status"] == "paid"


def prepare_bot(client, engine):
    ident, occ = create(client, amount="1000")
    with Session(engine) as db, db.begin():
        db.get(Recurring, UUID(ident)).owner_id = 42
    return ident, occ


def test_bot_partial_payment_confirm_and_duplicate_update(client, engine):
    _, occ = prepare_bot(client, engine)
    telegram = run(engine, ["Записать оплату", "1", "300", "Сегодня"])
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Payment)) == 0
        assert db.get(BotState, 42).step == "pay_confirm"
    incoming = update(5, "Подтвердить оплату")
    handle_update(incoming, settings(), telegram, engine)
    handle_update(incoming, settings(), telegram, engine)
    with Session(engine) as db:
        assert services.paid_amount(db, UUID(occ)) == 300
        assert db.scalar(select(func.count()).select_from(Payment)) == 1
        assert db.get(BotState, 42).step == "idle"


def test_bot_full_variable_revision_and_cancel(client, engine):
    _, occ = prepare_bot(client, engine)
    run(engine, ["/pay", "1", "Уточнить сумму", "1200", "Вся сумма", "Сегодня", "Отмена"])
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Payment)) == 0
        assert db.get(Occurrence, UUID(occ)).expected_amount == 1000
    run(
        engine, ["/pay", "1", "Уточнить сумму", "1200", "Вся сумма", "Сегодня", "Подтвердить оплату"], start=8
    )
    with Session(engine) as db:
        assert db.get(Occurrence, UUID(occ)).status == "paid"
        assert services.paid_amount(db, UUID(occ)) == 1200


def test_bot_stale_bill_and_overpayment(client, engine):
    _, occ = prepare_bot(client, engine)
    run(engine, ["/pay", "1", "1001"])
    with Session(engine) as db:
        assert db.get(BotState, 42).step == "pay_amount"
    run(engine, ["Вся сумма", "Сегодня"], start=4)
    with Session(engine) as db, db.begin():
        services.register_payment(
            db, 42, UUID(occ), schemas.PaymentCreate(amount="100", idempotency_key="other-client")
        )
    telegram = run(engine, ["Подтвердить оплату"], start=6)
    assert "изменилось" in telegram.calls[-1][1]["text"]
    with Session(engine) as db:
        assert services.paid_amount(db, UUID(occ)) == 100


def test_concurrent_corrections_only_one_revision(client, engine):
    _, occ = create(client, amount="1000")
    p = pay(client, occ, "500").json()
    barrier = Barrier(2)

    def attempt(amount):
        barrier.wait(timeout=10)
        try:
            with Session(engine) as db, db.begin():
                services.correct_payment(
                    db,
                    1,
                    UUID(p["id"]),
                    schemas.PaymentCorrectionCreate(
                        amount=amount,
                        paid_on=services.today(),
                        reason="Исправление",
                        expected_revision=0,
                        idempotency_key=str(uuid4()),
                    ),
                )
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, ["300", "400"])) == [200, 409]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(PaymentCorrection)) == 1


def test_reopened_payment_reactivates_unsent_reminders(client, engine):
    from app.notifications import reconcile

    _, occ = create(client, anchor_date=(services.today() + timedelta(days=10)).isoformat(), amount="1000")
    with Session(engine) as db, db.begin():
        reconcile(db, 1)
    p = pay(client, occ, "1000").json()
    with Session(engine) as db, db.begin():
        reconcile(db, 1)
    assert correction(client, p["id"], "0").status_code == 200
    with Session(engine) as db, db.begin():
        reconcile(db, 1)
        from app.models import Notification

        statuses = list(
            db.scalars(select(Notification.status).where(Notification.occurrence_id == UUID(occ)))
        )
        assert statuses == ["pending"] * 3


def test_bot_payment_pages_keep_selection_mapping(engine):
    with Session(engine) as db, db.begin():
        for i in range(11):
            services.create_recurring(
                db,
                42,
                schemas.RecurringCreate(
                    title=f"Платёж {i}", amount="100", anchor_date=services.today(), frequency="years"
                ),
            )
    run(engine, ["/pay", "Дальше", "1", "Вся сумма", "Сегодня", "Подтвердить оплату"])
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Payment)) == 1
        assert db.get(BotState, 42).step == "idle"
