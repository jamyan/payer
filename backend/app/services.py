import hashlib
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import TZ
from app.models import Audit, Occurrence, Payment, PaymentCorrection, Recurring, Tariff, utcnow
from app.recurrence import generate_dates, next_date

CLOSED = {"paid", "skipped", "cancelled"}


def today():
    return datetime.now(TZ).date()


def fail(message, status=409):
    raise HTTPException(status_code=status, detail=message)


def lock_owner(db: Session, owner: int):
    # Single-user MVP: serialize writes, including jobs and cross-occurrence idempotency keys.
    # PostgreSQL releases this transaction-scoped lock on COMMIT or ROLLBACK.
    db.execute(text("SELECT pg_advisory_xact_lock(:owner)"), {"owner": owner})


def audit(db, owner, entity, action, **details):
    db.add(Audit(owner_id=owner, entity_id=entity, action=action, details=details))


def recurring(db, owner, ident, lock=False):
    query = select(Recurring).where(Recurring.id == ident, Recurring.owner_id == owner)
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if row is None:
        fail("Обязательство не найдено", 404)
    return row


def occurrence(db, owner, ident, lock=False):
    query = select(Occurrence).join(Recurring).where(Occurrence.id == ident, Recurring.owner_id == owner)
    if lock:
        query = query.with_for_update(of=Occurrence)
    row = db.scalar(query)
    if row is None:
        fail("Начисление не найдено", 404)
    return row


def effective_payment_amount():
    latest = (
        select(PaymentCorrection.amount)
        .where(PaymentCorrection.payment_id == Payment.id)
        .order_by(PaymentCorrection.revision.desc())
        .limit(1)
        .correlate(Payment)
        .scalar_subquery()
    )
    return func.coalesce(latest, Payment.amount)


def paid_amount(db, ident):
    return db.scalar(
        select(func.coalesce(func.sum(effective_payment_amount()), 0)).where(Payment.occurrence_id == ident)
    )


def view_payment(db, payment):
    correction = db.scalar(
        select(PaymentCorrection)
        .where(PaymentCorrection.payment_id == payment.id)
        .order_by(PaymentCorrection.revision.desc())
        .limit(1)
    )
    amount = correction.amount if correction else payment.amount
    return dict(
        id=payment.id,
        occurrence_id=payment.occurrence_id,
        amount=amount,
        original_amount=payment.amount,
        original_paid_on=payment.paid_on,
        paid_on=correction.paid_on if correction else payment.paid_on,
        revision=correction.revision if correction else 0,
        cancelled=amount == 0,
        idempotency_key=payment.idempotency_key,
        created_at=payment.created_at,
    )


def view_occurrence(db, row):
    paid = paid_amount(db, row.id)
    remaining = row.expected_amount - paid
    return dict(
        id=row.id,
        recurring_id=row.recurring_id,
        scheduled_date=row.scheduled_date,
        due_date=row.due_date,
        expected_amount=str(row.expected_amount),
        paid_amount=str(paid),
        remaining_amount=str(remaining),
        currency="RUB",
        status=row.status,
        manually_adjusted=row.manually_adjusted,
        overdue=row.due_date < today() and remaining > 0 and row.status not in CLOSED,
    )


def create_recurring(db, owner, data):
    lock_owner(db, owner)
    row = Recurring(owner_id=owner, **data.model_dump(exclude={"amount"}))
    db.add(row)
    db.flush()
    db.add(Tariff(recurring_id=row.id, effective_from=row.anchor_date, amount=data.amount))
    audit(db, owner, row.id, "recurring_created", amount=str(data.amount))
    db.flush()
    generate(db, owner, as_of=today())
    return row


def generate(db, owner, as_of: date):
    lock_owner(db, owner)
    end = as_of + timedelta(days=90)
    count = 0
    for row in db.scalars(
        select(Recurring).where(Recurring.owner_id == owner, Recurring.active, Recurring.deleted_at.is_(None))
    ):
        dates = generate_dates(row.anchor_date, row.frequency, row.interval, row.anchor_date, end)
        if row.frequency == "years":
            extra = next_date(row.anchor_date, row.frequency, row.interval, end)
            # Only add next beyond horizon if no upcoming yearly occurrence is in the window.
            if not any(d >= as_of for d in dates) and extra:
                dates.append(extra)
        tariffs = list(
            db.scalars(select(Tariff).where(Tariff.recurring_id == row.id).order_by(Tariff.effective_from))
        )
        existing = set(
            db.scalars(
                select(Occurrence.scheduled_date).where(
                    Occurrence.recurring_id == row.id, Occurrence.schedule_revision == row.schedule_revision
                )
            )
        )
        protected = set(
            db.scalars(
                select(Occurrence.scheduled_date).where(
                    Occurrence.recurring_id == row.id, Occurrence.status.in_(["paid", "partial"])
                )
            )
        )
        for day in dates:
            if day in existing or day in protected:
                continue
            amount = next(t.amount for t in reversed(tariffs) if t.effective_from <= day)
            result = db.execute(
                insert(Occurrence)
                .values(
                    recurring_id=row.id,
                    schedule_revision=row.schedule_revision,
                    scheduled_date=day,
                    due_date=day,
                    expected_amount=amount,
                    status="pending",
                    manually_adjusted=False,
                )
                .on_conflict_do_nothing(constraint="uq_occurrence_date")
            )
            count += result.rowcount
    db.flush()
    return count


def change_tariff(db, owner, ident, data):
    lock_owner(db, owner)
    row = recurring(db, owner, ident, lock=True)
    if not row.active:
        fail("Обязательство остановлено")
    if data.effective_from < max(today(), row.anchor_date):
        fail("Дата нового тарифа должна быть не раньше сегодня и начала обязательства", 422)
    if db.scalar(
        select(Tariff).where(Tariff.recurring_id == ident, Tariff.effective_from == data.effective_from)
    ):
        fail("Тариф на эту дату уже существует")
    db.add(Tariff(recurring_id=ident, **data.model_dump()))
    db.flush()
    tariffs = list(
        db.scalars(select(Tariff).where(Tariff.recurring_id == ident).order_by(Tariff.effective_from))
    )
    # Paid, partial, closed and manually corrected occurrences keep their agreed amount.
    for occ in db.scalars(
        select(Occurrence)
        .where(
            Occurrence.recurring_id == ident,
            Occurrence.scheduled_date >= data.effective_from,
            Occurrence.status == "pending",
            Occurrence.manually_adjusted.is_(False),
        )
        .with_for_update()
    ):
        amount = next(t.amount for t in reversed(tariffs) if t.effective_from <= occ.scheduled_date)
        if occ.expected_amount != amount:
            audit(db, owner, occ.id, "tariff_applied", old=str(occ.expected_amount), new=str(amount))
            occ.expected_amount = amount
    audit(
        db,
        owner,
        row.id,
        "tariff_created",
        effective_from=data.effective_from.isoformat(),
        amount=str(data.amount),
    )


def change_expected(db, owner, occ, amount, paid):
    if not recurring(db, owner, occ.recurring_id).variable:
        fail("Сумму отдельного начисления можно менять только у переменного платежа")
    if amount < paid:
        fail("Сумма начисления не может быть меньше уже оплаченной")
    audit(db, owner, occ.id, "expected_changed", old=str(occ.expected_amount), new=str(amount))
    occ.expected_amount = amount
    occ.manually_adjusted = True
    occ.status = "paid" if paid == amount else ("partial" if paid else "pending")


def patch_occurrence(db, owner, ident, data):
    lock_owner(db, owner)
    occ = occurrence(db, owner, ident, lock=True)
    if occ.status in CLOSED:
        fail("Начисление закрыто")
    paid = paid_amount(db, occ.id)
    if data.status and paid:
        fail("Нельзя отменить или пропустить частично оплаченное начисление")
    if data.expected_amount is not None:
        change_expected(db, owner, occ, data.expected_amount, paid)
    if data.due_date is not None:
        audit(
            db, owner, occ.id, "due_date_changed", old=occ.due_date.isoformat(), new=data.due_date.isoformat()
        )
        occ.due_date = data.due_date
    if data.status:
        audit(db, owner, occ.id, "status_changed", old=occ.status, new=data.status)
        occ.status = data.status
    db.flush()
    return occ


def register_payment(db, owner, ident: UUID, data):
    lock_owner(db, owner)
    # Normalize scale so 7200 and 7200.00 are the same request.
    revised = None if data.revised_expected_amount is None else f"{data.revised_expected_amount:.2f}"
    payload = f"{ident}|{data.amount:.2f}|{revised}"
    if data.paid_on is not None:
        payload += f"|{data.paid_on.isoformat()}"
    fingerprint = hashlib.sha256(payload.encode()).hexdigest()
    prior = db.scalar(
        select(Payment).where(Payment.owner_id == owner, Payment.idempotency_key == data.idempotency_key)
    )
    if prior:
        if prior.request_hash != fingerprint:
            fail("Этот ключ идемпотентности уже использован с другими параметрами")
        return prior
    payment_date = data.paid_on or today()
    if payment_date > today():
        fail("Дата фактической оплаты не может быть в будущем", 422)
    occ = occurrence(db, owner, ident, lock=True)
    if occ.status in CLOSED:
        fail("Начисление закрыто")
    paid = paid_amount(db, ident)
    expected = (
        data.revised_expected_amount if data.revised_expected_amount is not None else occ.expected_amount
    )
    if paid + data.amount > expected:
        fail("Переплата запрещена")
    if data.revised_expected_amount is not None:
        change_expected(db, owner, occ, expected, paid)
    payment = Payment(
        owner_id=owner,
        occurrence_id=ident,
        amount=data.amount,
        idempotency_key=data.idempotency_key,
        request_hash=fingerprint,
        paid_on=payment_date,
    )
    db.add(payment)
    occ.status = "paid" if paid + data.amount == expected else "partial"
    db.flush()
    audit(
        db,
        owner,
        ident,
        "payment_registered",
        payment_id=str(payment.id),
        amount=str(data.amount),
        paid_on=payment_date.isoformat(),
    )
    return payment


def stop_recurring(db, owner, ident, cancel_future):
    lock_owner(db, owner)
    row = recurring(db, owner, ident, lock=True)
    row.active = False
    if cancel_future:
        for occ in db.scalars(
            select(Occurrence)
            .where(
                Occurrence.recurring_id == ident,
                Occurrence.scheduled_date >= today(),
                Occurrence.status == "pending",
            )
            .with_for_update()
        ):
            occ.status = "cancelled"
            audit(db, owner, occ.id, "status_changed", old="pending", new="cancelled")
    audit(db, owner, ident, "recurring_stopped", cancel_future_unpaid=cancel_future)


def edit_recurring(db, owner, ident, data):
    lock_owner(db, owner)
    row = recurring(db, owner, ident, lock=True)
    if row.deleted_at:
        fail("Обязательство удалено")
    changes = data.model_dump(exclude_unset=True)
    schedule_changed = any(
        k in changes and changes[k] != getattr(row, k) for k in ("anchor_date", "frequency", "interval")
    )
    old = {k: str(getattr(row, k)) for k in ("title", "variable", "anchor_date", "frequency", "interval")}
    tariffs = list(
        db.scalars(select(Tariff).where(Tariff.recurring_id == ident).order_by(Tariff.effective_from))
    )
    effective = max(today(), row.anchor_date)
    current_amount = next(t.amount for t in reversed(tariffs) if t.effective_from <= effective)
    if schedule_changed:
        anchor = changes.get("anchor_date", row.anchor_date)
        if anchor < today():
            fail("Для нового расписания укажи первую дату не раньше сегодня", 422)
        for occ in db.scalars(
            select(Occurrence)
            .where(
                Occurrence.recurring_id == ident,
                Occurrence.scheduled_date >= today(),
                Occurrence.status == "pending",
            )
            .with_for_update()
        ):
            occ.status = "cancelled"
            audit(
                db, owner, occ.id, "status_changed", old="pending", new="cancelled", reason="schedule_changed"
            )
        row.schedule_revision += 1
    for key in ("title", "variable", "anchor_date", "frequency", "interval"):
        if key in changes:
            setattr(row, key, changes[key])
    if data.amount is not None or schedule_changed:
        effective = max(today(), row.anchor_date)
        amount = data.amount if data.amount is not None else current_amount
        if schedule_changed and tariffs[0].effective_from > effective:
            audit(
                db,
                owner,
                ident,
                "tariff_start_changed",
                old=tariffs[0].effective_from.isoformat(),
                new=effective.isoformat(),
            )
            tariffs[0].effective_from = effective
            db.flush()
        tariff = db.scalar(
            select(Tariff).where(Tariff.recurring_id == ident, Tariff.effective_from == effective)
        )
        if tariff:
            before = str(tariff.amount)
            tariff.amount = amount
        else:
            before = None
            db.add(Tariff(recurring_id=ident, effective_from=effective, amount=amount))
        audit(
            db,
            owner,
            ident,
            "tariff_edited",
            old=before,
            amount=str(amount),
            effective_from=effective.isoformat(),
        )
        db.flush()
        versions = list(
            db.scalars(select(Tariff).where(Tariff.recurring_id == ident).order_by(Tariff.effective_from))
        )
        for occ in db.scalars(
            select(Occurrence)
            .where(
                Occurrence.recurring_id == ident,
                Occurrence.scheduled_date >= effective,
                Occurrence.status == "pending",
                Occurrence.manually_adjusted.is_(False),
            )
            .with_for_update()
        ):
            amount = next(t.amount for t in reversed(versions) if t.effective_from <= occ.scheduled_date)
            if occ.expected_amount != amount:
                audit(db, owner, occ.id, "tariff_applied", old=str(occ.expected_amount), new=str(amount))
                occ.expected_amount = amount
    audit(
        db,
        owner,
        ident,
        "recurring_edited",
        old=old,
        new={k: str(getattr(row, k)) for k in old},
        schedule_changed=schedule_changed,
    )
    db.flush()
    generate(db, owner, today())
    return row


def delete_recurring(db, owner, ident):
    lock_owner(db, owner)
    row = recurring(db, owner, ident, lock=True)
    if row.deleted_at:
        return
    row.deleted_at, row.active = utcnow(), False
    for occ in db.scalars(
        select(Occurrence)
        .where(Occurrence.recurring_id == ident, Occurrence.status == "pending")
        .with_for_update()
    ):
        occ.status = "cancelled"
        audit(db, owner, occ.id, "status_changed", old="pending", new="cancelled", reason="recurring_deleted")
    audit(db, owner, ident, "recurring_deleted", title=row.title, history_preserved=True)


def correct_payment(db, owner, ident, data):
    lock_owner(db, owner)
    payment = db.scalar(
        select(Payment).where(Payment.id == ident, Payment.owner_id == owner).with_for_update()
    )
    if payment is None:
        fail("Оплата не найдена", 404)
    payload = f"{ident}|{data.amount:.2f}|{data.paid_on}|{data.reason}|{data.expected_revision}"
    fingerprint = hashlib.sha256(payload.encode()).hexdigest()
    prior = db.scalar(
        select(PaymentCorrection).where(
            PaymentCorrection.owner_id == owner, PaymentCorrection.idempotency_key == data.idempotency_key
        )
    )
    if prior:
        if prior.request_hash != fingerprint:
            fail("Этот ключ исправления уже использован с другими параметрами")
        return payment
    current = view_payment(db, payment)
    if current["revision"] != data.expected_revision:
        fail("Оплата уже изменена. Обнови её и проверь сумму ещё раз")
    if data.paid_on > today():
        fail("Дата фактической оплаты не может быть в будущем", 422)
    occ = occurrence(db, owner, payment.occurrence_id, lock=True)
    total = paid_amount(db, occ.id) - current["amount"] + data.amount
    if total > occ.expected_amount:
        fail("Исправление создаст переплату")
    if data.amount == current["amount"] and data.paid_on == current["paid_on"]:
        fail("Сумма и дата не изменились", 422)
    db.add(
        PaymentCorrection(
            payment_id=ident,
            owner_id=owner,
            revision=current["revision"] + 1,
            amount=data.amount,
            paid_on=data.paid_on,
            reason=data.reason,
            idempotency_key=data.idempotency_key,
            request_hash=fingerprint,
        )
    )
    parent = recurring(db, owner, occ.recurring_id)
    if total:
        occ.status = "paid" if total == occ.expected_amount else "partial"
    elif parent.deleted_at or occ.status in {"skipped", "cancelled"}:
        occ.status = "cancelled"
    else:
        occ.status = "pending"
    audit(
        db,
        owner,
        occ.id,
        "payment_corrected",
        payment_id=str(ident),
        old_amount=str(current["amount"]),
        new_amount=str(data.amount),
        old_paid_on=current["paid_on"].isoformat(),
        new_paid_on=data.paid_on.isoformat(),
        reason=data.reason,
        revision=current["revision"] + 1,
    )
    db.flush()
    return payment


def restore_recurring(db, owner, ident, first_date):
    lock_owner(db, owner)
    row = recurring(db, owner, ident, lock=True)
    if not row.deleted_at:
        fail("Обязательство уже восстановлено или не находится в архиве")
    if first_date < max(today(), row.anchor_date):
        fail("Дата возобновления должна быть не раньше сегодня и начала обязательства", 422)
    archived_at = row.deleted_at.isoformat()
    row.deleted_at, row.active = None, True
    row.anchor_date = first_date
    row.schedule_revision += 1
    audit(db, owner, ident, "recurring_restored", first_date=first_date.isoformat(), archived_at=archived_at)
    db.flush()
    generate(db, owner, today())
    return row


def monthly_summary(db, owner, month=None):
    lock_owner(db, owner)
    first = (month or today()).replace(day=1)
    end = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    # Cash follows the effective payment date, independent of the bill's due date.
    latest_date = (
        select(PaymentCorrection.paid_on)
        .where(PaymentCorrection.payment_id == Payment.id)
        .order_by(PaymentCorrection.revision.desc())
        .limit(1)
        .correlate(Payment)
        .scalar_subquery()
    )
    cash_date = func.coalesce(latest_date, Payment.paid_on)
    cash = db.scalar(
        select(func.coalesce(func.sum(effective_payment_amount()), 0)).where(
            Payment.owner_id == owner, cash_date >= first, cash_date < end
        )
    )
    rows = list(db.scalars(select(Occurrence).join(Recurring).where(Recurring.owner_id == owner)))
    expected = paid_due = remaining = old_debt = overdue = Decimal(0)
    count = 0
    for o in rows:
        paid = paid_amount(db, o.id)
        if first <= o.due_date < end and o.status not in {"cancelled", "skipped"}:
            count += 1
            expected += o.expected_amount
            paid_due += paid
            remaining += o.expected_amount - paid
        if o.status not in CLOSED:
            if o.due_date < first:
                old_debt += o.expected_amount - paid
            if o.due_date < today():
                overdue += o.expected_amount - paid
    return dict(
        month=first.strftime("%Y-%m"),
        currency="RUB",
        timezone="Asia/Irkutsk",
        count=count,
        expected_due=f"{expected:.2f}",
        paid_for_due=f"{paid_due:.2f}",
        remaining_due=f"{remaining:.2f}",
        cash_paid=f"{cash:.2f}",
        old_debt=f"{old_debt:.2f}",
        overdue=f"{overdue:.2f}",
    )
