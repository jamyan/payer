from calendar import monthrange
from decimal import Decimal

from sqlalchemy import select

from app.models import Occurrence, Recurring
from app.services import monthly_summary, paid_amount, today


def report(db, owner, command):
    day = today()
    end = day.replace(day=monthrange(day.year, day.month)[1])
    rows = list(
        db.execute(
            select(Occurrence, Recurring.title)
            .join(Recurring)
            .where(Recurring.owner_id == owner)
            .order_by(Occurrence.due_date, Occurrence.id)
        )
    )
    values = [(o, title, paid_amount(db, o.id)) for o, title in rows]
    unpaid = [
        (o, title, o.expected_amount - paid)
        for o, title, paid in values
        if o.status in ("pending", "partial")
    ]
    if command == "/summary":
        totals = monthly_summary(db, owner, day)
        return (
            f"Сводка {day:%m.%Y} (Иркутск)\n\n"
            f"План по срокам месяца: {totals['expected_due']} RUB\n"
            f"Оплачено по этим начислениям: {totals['paid_for_due']} RUB\n"
            f"Осталось по срокам месяца: {totals['remaining_due']} RUB\n\n"
            f"Фактически внесено в этом месяце: {totals['cash_paid']} RUB\n"
            f"Старые долги (срок до начала месяца): {totals['old_debt']} RUB\n"
            f"Просрочено за все даты: {totals['overdue']} RUB\n\n"
            "Фактические расходы считаются по дате оплаты, с учётом исправлений.\nЗаписать оплату: /pay"
        )
    if command == "/overdue":
        selected = [(o, title, amount) for o, title, amount in unpaid if o.due_date < day]
        heading = "Просроченные платежи за все даты"
    elif command == "/next":
        selected = unpaid[:1]
        heading = "Ближайший неоплаченный платёж (сначала старые долги)"
    else:
        # Remaining current-month bills + every older debt, without including remote future bills.
        selected = [(o, title, amount) for o, title, amount in unpaid if o.due_date <= end]
        heading = f"Осталось до конца {day:%m.%Y}, включая старые долги"
    if not selected:
        return heading + "\n\nНеоплаченных начислений нет. Посмотреть другие даты: /app"
    total = sum((amount for _, _, amount in selected), Decimal(0))
    text = f"{heading}\nВсего: {len(selected)}, остаток {total:.2f} RUB\n"
    shown = 0
    for o, title, amount in selected:
        line = f"\n{o.due_date:%d.%m.%Y} · {title} — {amount:.2f} RUB" + (
            " · просрочен" if o.due_date < day else ""
        )
        if len(text) + len(line) > 3400:
            break
        text += line
        shown += 1
    if shown < len(selected):
        text += f"\n\nПоказано {shown} из {len(selected)}. Полный список: /app"
    return text
