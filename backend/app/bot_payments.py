"""Record existing payments in Telegram with an explicit, durable confirmation."""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.models import Occurrence, Recurring
from app.schemas import PaymentCreate
from app.services import occurrence, paid_amount, recurring, register_payment, today


def keyboard(rows):
    from app.bot_dialog import keyboard as make_keyboard

    return make_keyboard(rows)


def menu():
    from app.bot_dialog import MENU

    return keyboard(MENU)


def money(raw):
    value = raw.strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    if not re.fullmatch(r"\d{1,12}(\.\d{1,2})?", value) or Decimal(value) <= 0:
        raise ValueError("Введи положительную сумму в рублях, максимум два знака после запятой.")
    return Decimal(value)


def start_payment(db, owner, state, page=0):
    rows = list(
        db.execute(
            select(Occurrence, Recurring.title)
            .join(Recurring)
            .where(Recurring.owner_id == owner, Occurrence.status.in_(["pending", "partial"]))
            .order_by(Occurrence.due_date, Occurrence.id)
        )
    )
    if not rows:
        state.step, state.draft = "idle", {}
        return "Неоплаченных начислений нет.", menu()
    page = max(0, min(page, (len(rows) - 1) // 10))
    selected = rows[page * 10 : page * 10 + 10]
    options = {str(i + 1): str(o.id) for i, (o, _) in enumerate(selected)}
    labels = [[f"{i + 1} · {name[:45]} · {o.due_date:%d.%m}"] for i, (o, name) in enumerate(selected)]
    text = "Какой платёж отметить? Это запись уже совершённой оплаты, деньги не списываются.\n"
    for i, (o, name) in enumerate(selected):
        text += f"\n{i + 1}. {name} · {o.due_date:%d.%m.%Y} · осталось {o.expected_amount - paid_amount(db, o.id):.2f} RUB"
    navigation = ([] if page == 0 else ["Назад"]) + ([] if (page + 1) * 10 >= len(rows) else ["Дальше"])
    state.step, state.draft = "pay_select", {"options": options, "page": page}
    return text + f"\n\nСтраница {page + 1}. Нажми кнопку или введи номер.", keyboard(
        labels + ([navigation] if navigation else []) + [["Отмена"]]
    )


def amount_prompt(db, owner, state):
    draft = state.draft
    occ = occurrence(db, owner, UUID(draft["occurrence_id"]))
    parent = recurring(db, owner, occ.recurring_id)
    expected = Decimal(draft.get("revised") or draft["expected"])
    remaining = expected - Decimal(draft["paid"])
    choices = [["Вся сумма"]] + ([["Уточнить сумму"]] if parent.variable else []) + [["Отмена"]]
    return (
        f"{parent.title}\nСрок: {occ.due_date:%d.%m.%Y}\nУже внесено: {Decimal(draft['paid']):.2f} RUB\n"
        f"Осталось: {remaining:.2f} RUB\n\nСколько фактически внесено? Можно часть суммы.",
        keyboard(choices),
    )


def advance_payment(db, owner, state, raw, update_id):
    text = raw.strip()
    low = text.casefold()
    draft = dict(state.draft)
    try:
        if state.step == "pay_select":
            if low in {"дальше", "назад"}:
                return start_payment(db, owner, state, draft["page"] + (1 if low == "дальше" else -1))
            number = text.split(" · ", 1)[0]
            if number not in draft["options"]:
                raise ValueError("Выбери платёж кнопкой или введи его номер из списка.")
            occ = occurrence(db, owner, UUID(draft["options"][number]))
            if occ.status not in {"pending", "partial"}:
                return start_payment(db, owner, state, draft["page"])
            state.draft = {
                "occurrence_id": str(occ.id),
                "expected": str(occ.expected_amount),
                "paid": str(paid_amount(db, occ.id)),
                "due": occ.due_date.isoformat(),
            }
            state.step = "pay_amount"
            return amount_prompt(db, owner, state)
        if state.step == "pay_amount":
            if low == "уточнить сумму":
                state.step = "pay_revise"
                return (
                    "Какая новая общая сумма начисления? Она должна быть больше уже внесённой части.",
                    keyboard([["Отмена"]]),
                )
            amount = (
                (Decimal(draft.get("revised") or draft["expected"]) - Decimal(draft["paid"]))
                if low == "вся сумма"
                else money(text)
            )
            if amount <= 0 or amount + Decimal(draft["paid"]) > Decimal(
                draft.get("revised") or draft["expected"]
            ):
                raise ValueError("Сумма превышает остаток. Переплата запрещена. Введи сумму ещё раз.")
            draft["amount"] = str(amount)
            state.draft, state.step = draft, "pay_date"
            return (
                "Когда фактически оплатил? Введи ДД.ММ.ГГГГ или выбери дату. Будущая дата не допускается.",
                keyboard([["Сегодня", "Вчера"], ["Отмена"]]),
            )
        if state.step == "pay_revise":
            occ = occurrence(db, owner, UUID(draft["occurrence_id"]))
            if not recurring(db, owner, occ.recurring_id).variable:
                raise ValueError("У этого обязательства фиксированная сумма.")
            amount = money(text)
            if amount <= Decimal(draft["paid"]):
                raise ValueError("Новая сумма должна быть больше уже внесённой части.")
            draft["revised"] = str(amount)
            state.draft, state.step = draft, "pay_amount"
            return amount_prompt(db, owner, state)
        if state.step == "pay_date":
            day = (
                today()
                if low == "сегодня"
                else today() - timedelta(days=1)
                if low == "вчера"
                else (datetime.strptime(text, "%d.%m.%Y").date() if "." in text else date.fromisoformat(text))
            )
            if not date(2000, 1, 1) <= day <= today():
                raise ValueError("Дата должна быть с 01.01.2000 по сегодня.")
            draft["paid_on"] = day.isoformat()
            state.draft, state.step = draft, "pay_confirm"
            occ = occurrence(db, owner, UUID(draft["occurrence_id"]))
            name = recurring(db, owner, occ.recurring_id).title
            remaining = (
                Decimal(draft.get("revised") or draft["expected"])
                - Decimal(draft["paid"])
                - Decimal(draft["amount"])
            )
            return (
                f"Проверь оплату:\n\n{name}\nСрок: {date.fromisoformat(draft['due']):%d.%m.%Y}\n"
                f"Фактически оплачено: {Decimal(draft['amount']):.2f} RUB\nДата оплаты: {day:%d.%m.%Y}\n"
                f"Общая сумма начисления: {Decimal(draft.get('revised') or draft['expected']):.2f} RUB\n"
                f"Остаток после оплаты: {remaining:.2f} RUB\n\nЗаписать оплату?",
                keyboard([["Подтвердить оплату"], ["Отмена"]]),
            )
        if state.step == "pay_confirm":
            if low != "подтвердить оплату":
                return "Оплата ещё не записана. Подтверди её или нажми «Отмена».", keyboard(
                    [["Подтвердить оплату"], ["Отмена"]]
                )
            occ = occurrence(db, owner, UUID(draft["occurrence_id"]))
            if (
                str(occ.expected_amount) != draft["expected"]
                or str(paid_amount(db, occ.id)) != draft["paid"]
                or occ.due_date.isoformat() != draft["due"]
            ):
                state.step, state.draft = "idle", {}
                return "Начисление изменилось во время ввода. Оплата не записана. Начни заново: /pay.", menu()
            with db.begin_nested():
                payment = register_payment(
                    db,
                    owner,
                    occ.id,
                    PaymentCreate(
                        amount=draft["amount"],
                        paid_on=draft["paid_on"],
                        revised_expected_amount=draft.get("revised"),
                        idempotency_key=f"botpay:{owner}:{update_id}",
                    ),
                )
            state.step, state.draft = "idle", {}
            return (
                f"Оплата {payment.amount:.2f} RUB записана. Осталось {occ.expected_amount - paid_amount(db, occ.id):.2f} RUB.\nИстория и исправление: /app.",
                menu(),
            )
    except HTTPException as exc:
        state.step, state.draft = "idle", {}
        return f"Оплата не записана: {exc.detail}. Начни заново: /pay.", menu()
    except (ValueError, ArithmeticError):
        if state.step == "pay_select":
            return "Введи номер платежа из списка или начни заново: /pay.", keyboard([["Отмена"]])
        return (
            "Проверь ввод: сумма — положительное число с двумя знаками после запятой; дата — ДД.ММ.ГГГГ, не в будущем. Переплата запрещена. Повтори ответ или /cancel.",
            keyboard([["Отмена"]]),
        )
    return "Начни запись оплаты заново: /pay.", menu()
