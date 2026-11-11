from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.bot import handle_update
from app.bot_dialog import next_offset, process_update
from app.models import BotReply, BotState, Occurrence, Recurring, Tariff
from app.services import today
from tests.test_web_and_bot import FakeTelegram, settings

pytestmark = pytest.mark.integration


def update(ident, text, owner=42, chat_type="private"):
    return {
        "update_id": ident,
        "message": {"from": {"id": owner}, "chat": {"id": owner, "type": chat_type}, "text": text},
    }


def run(engine, messages, start=1, telegram=None):
    telegram = telegram or FakeTelegram()
    for ident, text in enumerate(messages, start):
        handle_update(update(ident, text), settings(), telegram, engine)
    return telegram


def fill(engine, **options):
    return run(
        engine,
        [
            "/add",
            options.get("title", "Интернет"),
            options.get("amount", "1 200,50"),
            options.get("date", "Сегодня"),
            options.get("frequency", "Ежемесячно"),
            options.get("interval", "1"),
            options.get("variable", "Нет"),
        ],
    )


def test_dialog_creates_only_after_confirmation_and_generates(engine):
    telegram = fill(engine)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Recurring)) == 0
        state = db.get(BotState, 42)
        assert state.step == "confirm"
        assert "1200.50 RUB" in telegram.calls[-1][1]["text"]
    run(engine, ["Подтвердить"], start=8)
    with Session(engine) as db:
        row = db.scalar(select(Recurring))
        assert row.owner_id == 42 and row.title == "Интернет" and row.variable is False
        assert db.scalar(select(Tariff.amount)) == Decimal("1200.50")
        assert db.scalar(select(func.count()).select_from(Occurrence)) >= 3
        assert db.get(BotState, 42).draft == {}
    assert next_offset(engine, 42) == 9


@pytest.mark.parametrize(
    "frequency,expected",
    [("Ежедневно", "days"), ("Еженедельно", "weeks"), ("Ежемесячно", "months"), ("Ежегодно", "years")],
)
def test_frequency_interval_variable_and_tomorrow(engine, frequency, expected):
    fill(engine, frequency=frequency, interval="3", variable="Да", date="Завтра")
    run(engine, ["Подтвердить"], start=8)
    with Session(engine) as db:
        row = db.scalar(select(Recurring))
        assert row.frequency == expected and row.interval == 3 and row.variable is True
        assert row.anchor_date == today() + timedelta(days=1)


@pytest.mark.parametrize("value", ["0", "-100", "NaN", "100.001", "1000000000000", "сто рублей"])
def test_invalid_amount_keeps_step(engine, value):
    telegram = run(engine, ["/add", "Интернет", value])
    with Session(engine) as db:
        assert db.get(BotState, 42).step == "amount"
        assert db.scalar(select(func.count()).select_from(Recurring)) == 0
    assert "положительная сумма" in telegram.calls[-1][1]["text"]


@pytest.mark.parametrize("value", ["31.02.2027", "в пятницу", "1999-12-31", "2201-01-01"])
def test_invalid_date_keeps_step(engine, value):
    run(engine, ["/add", "Интернет", "100", value])
    with Session(engine) as db:
        assert db.get(BotState, 42).step == "date"


@pytest.mark.parametrize("value", ["0", "-1", "3661", "1.5", "abc"])
def test_invalid_interval_keeps_step(engine, value):
    run(engine, ["/add", "Интернет", "100", "Сегодня", "Ежемесячно", value])
    with Session(engine) as db:
        assert db.get(BotState, 42).step == "interval"


def test_cancel_and_restart(engine):
    telegram = run(engine, ["/add", "Отменить меня", "/cancel", "/cancel"])
    assert "Незаконченного ввода нет" in telegram.calls[-1][1]["text"]
    with Session(engine) as db:
        assert db.get(BotState, 42).step == "idle"
        assert db.scalar(select(func.count()).select_from(Recurring)) == 0
    run(engine, ["Добавить обязательство", "Черновик", "Начать заново"], start=5)
    with Session(engine) as db:
        assert db.get(BotState, 42).step == "title" and db.get(BotState, 42).draft == {}


def test_repeated_update_and_repeated_confirmation_do_not_duplicate(engine):
    fill(engine)
    telegram = FakeTelegram()
    confirmation = update(8, "Подтвердить")
    handle_update(confirmation, settings(), telegram, engine)
    handle_update(confirmation, settings(), telegram, engine)
    assert len(telegram.calls) == 1
    run(engine, ["Подтвердить"], start=9)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Recurring)) == 1


def test_failed_reply_keeps_creation_and_retries_without_duplicate(engine):
    fill(engine)

    class BrokenTelegram:
        def call(self, *args, **kwargs):
            raise RuntimeError("network error")

    with pytest.raises(RuntimeError):
        handle_update(update(8, "Подтвердить"), settings(), BrokenTelegram(), engine)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Recurring)) == 1
        assert db.scalar(select(BotReply).where(BotReply.update_id == 8)).sent_at is None
    telegram = FakeTelegram()
    handle_update(update(8, "Подтвердить"), settings(), telegram, engine)
    assert len(telegram.calls) == 1
    assert "создано" in telegram.calls[0][1]["text"]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Recurring)) == 1


def test_resume_after_reopening_sessions_and_help_keeps_draft(engine):
    run(engine, ["/add", "Сохранённый ввод"])
    telegram = run(engine, ["/help", "500", "15.11.2027", "Ежемесячно", "2", "Да"], start=3)
    with Session(engine) as db:
        draft = db.get(BotState, 42)
        assert draft.step == "confirm" and draft.draft["title"] == "Сохранённый ввод"
        assert draft.draft["anchor_date"] == "2027-11-15"
    assert "Сохранённый ввод" in telegram.calls[-1][1]["text"]


def test_foreign_owner_cannot_modify_active_dialog(engine):
    run(engine, ["/add", "Моё обязательство"])
    telegram = FakeTelegram()
    handle_update(update(3, "100", owner=999), settings(), telegram, engine)
    with Session(engine) as db:
        assert db.get(BotState, 42).step == "amount"
    assert telegram.calls == []


def test_rollback_restores_cursor_draft_and_creation(engine):
    fill(engine)
    with pytest.raises(RuntimeError):
        with Session(engine) as db, db.begin():
            process_update(db, update(8, "Подтвердить"), settings())
            raise RuntimeError("failure before commit")
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Recurring)) == 0
        state = db.get(BotState, 42)
        assert state.step == "confirm" and state.last_update_id == 7
    assert next_offset(engine, 42) == 8


def test_start_shows_add_button_and_invalid_answer_retains_choices(engine):
    telegram = run(engine, ["/start", "/add", "Интернет", "100", "Сегодня", "постоянно"])
    assert telegram.calls[0][1]["reply_markup"]["keyboard"][0][0]["text"] == "Добавить обязательство"
    rows = telegram.calls[-1][1]["reply_markup"]["keyboard"]
    assert any(button["text"] == "Ежемесячно" for row in rows for button in row)
    with Session(engine) as db:
        assert db.get(BotState, 42).step == "frequency"


def test_invalid_variable_and_title_do_not_advance(engine):
    run(engine, ["/add", "x" * 201])
    with Session(engine) as db:
        assert db.get(BotState, 42).step == "title"
    run(engine, ["Интернет", "100", "Сегодня", "Ежемесячно", "1", "возможно"], start=3)
    with Session(engine) as db:
        assert db.get(BotState, 42).step == "variable"


def test_past_date_warning_and_unconfirmed_text_does_not_create(engine):
    telegram = fill(engine, date=(today() - timedelta(days=3)).isoformat())
    assert "в прошлом" in telegram.calls[-1][1]["text"]
    run(engine, ["ОК"], start=8)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Recurring)) == 0
        assert db.get(BotState, 42).step == "confirm"


def test_future_monthly_obligation_reports_zero_occurrences(engine):
    fill(engine, date=(today() + timedelta(days=400)).isoformat())
    telegram = run(engine, ["Подтвердить"], start=8)
    assert "Начислений создано: 0" in telegram.calls[-1][1]["text"]
    assert "горизонт 90 дней" in telegram.calls[-1][1]["text"]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Recurring)) == 1
        assert db.scalar(select(func.count()).select_from(Occurrence)) == 0

@pytest.mark.parametrize('text', ['Помощь', '/menu', '/start'])
def test_full_reply_keyboard(engine, text):
    telegram = run(engine, [text])
    markup = telegram.calls[-1][1]['reply_markup']
    buttons = [button for row in markup['keyboard'] for button in row]
    labels = {button['text'] for button in buttons}
    assert {'Осталось оплатить', 'Просрочено', 'Ближайший платёж', 'Сводка месяца', 'Код для браузера', 'Добавить обязательство'} <= labels
    assert 'Открыть приложение' not in labels
    assert 'Отмена' not in labels


@pytest.mark.parametrize('text', ['Осталось оплатить', 'Просрочено', 'Ближайший платёж', 'Сводка месяца'])
def test_report_buttons_do_not_start_add_dialog(engine, text):
    telegram = run(engine, [text])
    assert telegram.calls
    with Session(engine) as db:
        state = db.get(BotState, 42)
        assert state is None or state.step == 'idle'



@pytest.mark.parametrize('command', ['/remaining', '/help', '/menu', '/login', '/app'])
def test_cancel_button_follows_unfinished_draft(engine, command):
    telegram = run(engine, ['/add', command, 'Отмена', '/menu'])
    for _, payload in telegram.calls[:2]:
        assert any(b['text'] == 'Отмена' for row in payload['reply_markup']['keyboard'] for b in row)
    for _, payload in telegram.calls[2:]:
        assert all(b['text'] not in {'Отмена', 'Открыть приложение'} for row in payload['reply_markup']['keyboard'] for b in row)
    with Session(engine) as db:
        assert db.get(BotState, 42).step == 'idle'


def test_nontext_idle_has_no_cancel_button(engine):
    telegram = FakeTelegram()
    incoming = update(1, 'unused')
    del incoming['message']['text']
    handle_update(incoming, settings(), telegram, engine)
    assert all(b['text'] != 'Отмена' for row in telegram.calls[-1][1]['reply_markup']['keyboard'] for b in row)
