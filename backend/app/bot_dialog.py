"""Пошаговое создание обязательств. Данные и подтверждение сохраняются одной транзакцией."""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.bot_payments import advance_payment, start_payment
from app.bot_reports import report
from app.browser_auth import issue_code, revoke_sessions
from app.models import BotReply, BotState, Occurrence, utcnow
from app.schemas import RecurringCreate
from app.services import create_recurring, lock_owner, today

MENU = [
    ["Добавить обязательство", "Записать оплату"],
    ["Осталось оплатить", "Просрочено"],
    ["Ближайший платёж", "Сводка месяца"],
    ["Код для браузера"],
    ["Выйти из браузеров", "Помощь"],
]
BUTTON_COMMANDS = {
    "записать оплату": "/pay",
    "осталось оплатить": "/remaining",
    "просрочено": "/overdue",
    "ближайший платёж": "/next",
    "сводка месяца": "/summary",
    "открыть приложение": "/app",
    "код для браузера": "/login",
    "выйти из браузеров": "/logout",
    "помощь": "/help",
    "добавить обязательство": "/add",
    "отмена": "/cancel",
}
FREQUENCIES = {"ежедневно": "days", "еженедельно": "weeks", "ежемесячно": "months", "ежегодно": "years"}
FREQUENCY_LABELS = {"days": "дн.", "weeks": "нед.", "months": "мес.", "years": "г."}


def keyboard(rows):
    return {
        "keyboard": [[{"text": text} for text in row] for row in rows],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def prompt(step):
    text, rows = {
        "title": ("Как называется обязательство? Например: Интернет.\nОтмена: /cancel", []),
        "amount": ("Какая ожидаемая сумма в рублях? Например: 1200 или 6500,50.", []),
        "date": (
            "Первая дата оплаты? Введи ДД.ММ.ГГГГ или ГГГГ-ММ-ДД. Можно написать «сегодня» или «завтра».",
            [["Сегодня", "Завтра"]],
        ),
        "frequency": (
            "Как часто повторяется платёж?",
            [["Ежемесячно", "Ежегодно"], ["Еженедельно", "Ежедневно"]],
        ),
        "interval": (
            "Через сколько таких периодов повторять? Обычно 1. Для оплаты раз в 3 месяца введи 3.",
            [["1", "2", "3"], ["6", "12"]],
        ),
        "variable": ("Сумма может меняться от начисления к начислению?", [["Да", "Нет"]]),
    }[step]
    return text, keyboard(rows + [["Отмена"]])


def summary(draft):
    day = date.fromisoformat(draft["anchor_date"])
    text = (
        f"Проверь обязательство:\n\n{draft['title']}\n"
        f"Сумма: {Decimal(draft['amount']):.2f} RUB\n"
        f"Первая дата: {day:%d.%m.%Y}\n"
        f"Повтор: каждые {draft['interval']} {FREQUENCY_LABELS[draft['frequency']]}\n"
        f"Сумма: {'переменная' if draft['variable'] else 'фиксированная'}\n\n"
        "Напоминания: за 3/1/0 дня, в 11:00 по Иркутску."
    )
    if day < today():
        text += "\n\nНачальная дата в прошлом: начисления с этой даты тоже будут созданы."
    return text + "\n\nСоздать обязательство?", keyboard([["Подтвердить"], ["Начать заново", "Отмена"]])


def welcome(settings):
    return (
        "Платёжник — твой календарь подписок и обязательных платежей.\n\n"
        "Добавить обязательство прямо в чате: /add или кнопка «Добавить обязательство».\n"
        "Записать оплату: /pay. Отменить ввод: /cancel. Приложение: /app.\n"
        "Осталось оплатить: /remaining. Просрочено: /overdue. Ближайший: /next.\n"
        "Сводка месяца: /summary. Код входа в браузере: /login. Выйти из всех браузеров: /logout.\n\n"
        "Напоминания: за 3/1/0 дня в 11:00 по Иркутску. Деньги бот не списывает.",
        {
            "inline_keyboard": [
                [{"text": "Открыть Платёжник", "web_app": {"url": settings.app_url.rstrip("/")}}]
            ]
        },
    )


def advance(state, raw):
    text = raw.strip()
    low = text.casefold()
    command = low.split(" ", 1)[0].split("@", 1)[0]
    if command == "/cancel" or low == "отмена":
        if state.step == "idle":
            return (
                "Незаконченного ввода нет. Добавить обязательство: /add. Посмотреть созданные: /app.",
                keyboard(MENU),
            )
        state.step, state.draft = "idle", {}
        return "Ввод отменён. Черновик сброшен.", keyboard(MENU)
    if command in {"/add", "/new"} or low in {"добавить обязательство", "добавить", "начать заново"}:
        state.step, state.draft = "title", {}
        return prompt("title")
    if state.step == "idle":
        return "Для создания обязательства отправь /add. Для приложения — /app.", keyboard(MENU)
    if command.startswith("/"):
        return "Сейчас идёт ввод обязательства. Продолжи ответом или отправь /cancel.", keyboard([["Отмена"]])
    draft = dict(state.draft)
    step = state.step
    if step == "title":
        if not 1 <= len(text) <= 200 or any(ord(c) < 32 for c in text):
            raise ValueError("Название должно занимать одну строку от 1 до 200 символов.")
        draft["title"] = text
        state.step = "amount"
    elif step == "amount":
        amount = text.replace(" ", "").replace("\u00a0", "").replace(",", ".")
        if not re.fullmatch(r"\d{1,12}(\.\d{1,2})?", amount) or Decimal(amount) <= 0:
            raise ValueError(
                "Нужна положительная сумма, не больше двух знаков после запятой. Например: 6500,50."
            )
        draft["amount"] = f"{Decimal(amount):.2f}"
        state.step = "date"
    elif step == "date":
        if low in {"сегодня", "завтра"}:
            day = today() + timedelta(days=low == "завтра")
        else:
            try:
                day = datetime.strptime(text, "%d.%m.%Y").date() if "." in text else date.fromisoformat(text)
            except ValueError:
                raise ValueError("Не удалось распознать дату. Пример: 15.11.2026 или 2026-11-15.") from None
        if not date(2000, 1, 1) <= day <= date(2200, 12, 31):
            raise ValueError("Дата должна быть между 2000 и 2200 годом.")
        draft["anchor_date"] = day.isoformat()
        state.step = "frequency"
    elif step == "frequency":
        if low not in FREQUENCIES:
            raise ValueError("Выбери: Ежедневно, Еженедельно, Ежемесячно или Ежегодно.")
        draft["frequency"] = FREQUENCIES[low]
        state.step = "interval"
    elif step == "interval":
        if not re.fullmatch(r"\d{1,4}", text) or not 1 <= int(text) <= 3660:
            raise ValueError("Введи целое число от 1 до 3660.")
        draft["interval"] = int(text)
        state.step = "variable"
    elif step == "variable":
        if low not in {"да", "нет"}:
            raise ValueError("Ответь «Да» или «Нет».")
        draft["variable"] = low == "да"
        state.step = "confirm"
    elif step == "confirm":
        return summary(draft)
    state.draft = draft
    return summary(draft) if state.step == "confirm" else prompt(state.step)


def process_update(db, update, settings):
    owner = settings.owner_telegram_id
    lock_owner(db, owner)
    db.execute(
        insert(BotState)
        .values(owner_id=owner, last_update_id=-1, step="idle", draft={})
        .on_conflict_do_nothing(index_elements=[BotState.owner_id])
    )
    state = db.get(BotState, owner)
    update_id = update["update_id"]
    if update_id <= state.last_update_id:
        return
    state.last_update_id = update_id
    message = update.get("message", {})
    if (
        message.get("chat", {}).get("type") != "private"
        or message.get("chat", {}).get("id") != owner
        or message.get("from", {}).get("id") != owner
    ):
        return
    raw = message.get("text")
    if isinstance(raw, str):
        raw = BUTTON_COMMANDS.get(raw.strip().casefold(), raw)
    command = raw.strip().casefold().split(" ", 1)[0].split("@", 1)[0] if isinstance(raw, str) else ""
    sensitive = False
    if not isinstance(raw, str):
        text, markup = (
            "Нужно текстовое сообщение. Добавить обязательство: /add.",
            keyboard(MENU),
        )
    elif command == "/pay":
        text, markup = start_payment(db, owner, state)
    elif command in {"/remaining", "/list", "/overdue", "/next", "/summary"}:
        text, markup = report(db, owner, command), keyboard(MENU)
        if state.step != "idle":
            text += "\n\nТекущий ввод сохранён. Продолжи его или /cancel."
    elif command == "/login":
        code = issue_code(db, owner)
        text = (
            f"Код входа на {settings.app_url}:\n\n{code}\n\n"
            "Действует 5 минут и используется один раз. Введи его только на своём сайте Платёжника. "
            "Никому не пересылай. Новый /login заменит предыдущий код."
        )
        sensitive = True
        markup = keyboard(MENU)
    elif command == "/logout":
        revoke_sessions(db, owner)
        text, markup = (
            "Все браузерные сессии завершены, коды входа отменены. Новый вход: /login.",
            keyboard(MENU),
        )
    elif command in {"/start", "/app", "/help", "/menu"}:
        text, markup = welcome(settings)
        if command != "/app":
            markup = keyboard(MENU)
        if state.step != "idle":
            text += "\n\nВвод обязательства сохранён. Продолжи ответом или отправь /cancel."
    elif state.step.startswith("pay_") and command not in {"/cancel", "/add", "/new"}:
        text, markup = advance_payment(db, owner, state, raw, update_id)
    elif state.step == "confirm" and raw.strip().casefold() == "подтвердить":
        # The obligation, the consumed update and the reply are committed together.
        data = RecurringCreate(**state.draft)
        row = create_recurring(db, owner, data)
        state.step, state.draft = "idle", {}
        count = db.scalar(
            select(func.count()).select_from(Occurrence).where(Occurrence.recurring_id == row.id)
        )
        text = (
            f"Обязательство «{row.title}» создано. Начислений создано: {count}.\n"
            "Посмотреть: /app\nДобавить ещё: /add"
        )
        if count == 0:
            text += "\nНачисления появятся, когда первая дата войдёт в горизонт 90 дней."
        markup = keyboard(MENU)
    else:
        try:
            text, markup = advance(state, raw)
        except (ValueError, ValidationError) as exc:
            text = str(exc)
            _, markup = prompt(state.step)
    # Restore the appropriate keyboard even after reports/help/login during a draft.
    if state.step != "idle":
        if "keyboard" not in markup:
            markup = keyboard(MENU)
        if not any(button.get("text") == "Отмена" for row in markup["keyboard"] for button in row):
            markup["keyboard"].append([{"text": "Отмена"}])
    db.add(BotReply(owner_id=owner, update_id=update_id, text=text, reply_markup=markup, sensitive=sensitive))
    db.flush()


def send_pending(engine, settings, telegram):
    while True:
        with Session(engine) as db, db.begin():
            lock_owner(db, settings.owner_telegram_id)
            reply = db.scalar(
                select(BotReply)
                .where(BotReply.owner_id == settings.owner_telegram_id, BotReply.sent_at.is_(None))
                .order_by(BotReply.update_id)
                .limit(1)
                .with_for_update()
            )
            if reply is None:
                return
            telegram.call(
                "sendMessage",
                chat_id=settings.owner_telegram_id,
                text=reply.text,
                reply_markup=reply.reply_markup,
            )
            reply.sent_at = utcnow()
            if reply.sensitive:
                reply.text = "Одноразовый код отправлен (скрыт после доставки)"


def next_offset(engine, owner):
    with Session(engine) as db:
        state = db.get(BotState, owner)
        return state.last_update_id + 1 if state else 0
