"""Telegram-бот: Mini App и создание обязательств в пошаговом диалоге."""

import logging
import time

import httpx
from sqlalchemy.orm import Session

from app.bot_dialog import next_offset, process_update, send_pending
from app.config import get_settings
from app.db import get_engine
from app.logging_config import configure_logging, silence_http_logging

logger = logging.getLogger("platezhnik.bot")


class Telegram:
    def __init__(self, settings):
        silence_http_logging()
        self.base = f"https://api.telegram.org/bot{settings.telegram_bot_token.get_secret_value()}"
        self.client = httpx.Client(timeout=40, trust_env=False)

    def call(self, method, **payload):
        response = self.client.post(
            f"{self.base}/{method}", json=payload, timeout=40 if method == "getUpdates" else 10
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError("Telegram rejected request")
        return result["result"]


def handle_update(update, settings, telegram, engine=None):
    engine = engine or get_engine()
    with Session(engine) as db, db.begin():
        process_update(db, update, settings)
    send_pending(engine, settings, telegram)


def configure(settings, telegram):
    webhook = telegram.call("getWebhookInfo")
    if webhook.get("url"):
        raise RuntimeError("Bot already has a webhook; use a separate bot or remove its webhook explicitly")
    telegram.call(
        "setMyCommands",
        commands=[
            {"command": "add", "description": "Добавить обязательство в чате"},
            {"command": "pay", "description": "Записать полную или частичную оплату"},
            {"command": "cancel", "description": "Отменить ввод"},
            {"command": "remaining", "description": "Осталось оплатить в этом месяце"},
            {"command": "overdue", "description": "Просроченные платежи"},
            {"command": "next", "description": "Ближайший неоплаченный платёж"},
            {"command": "summary", "description": "Сводка месяца"},
            {"command": "login", "description": "Код для входа в браузере"},
            {"command": "logout", "description": "Выйти из всех браузеров"},
            {"command": "app", "description": "Открыть Платёжник"},
            {"command": "help", "description": "Как пользоваться"},
            {"command": "menu", "description": "Показать кнопки команд"},
        ],
        scope={"type": "chat", "chat_id": settings.owner_telegram_id},
    )
    telegram.call(
        "setChatMenuButton",
        chat_id=settings.owner_telegram_id,
        menu_button={
            "type": "web_app",
            "text": "Платёжник",
            "web_app": {"url": settings.app_url.rstrip("/")},
        },
    )


def main():
    configure_logging()
    settings = get_settings()
    if not settings.telegram_bot_token.get_secret_value() or not settings.app_url.startswith("https://"):
        raise RuntimeError("Bot requires TELEGRAM_BOT_TOKEN and HTTPS APP_URL")
    telegram = Telegram(settings)
    configured = False
    try:
        while True:
            try:
                if not configured:
                    configure(settings, telegram)
                    configured = True
                    logger.info("Bot configured; polling for owner commands")
                send_pending(get_engine(), settings, telegram)
                updates = telegram.call(
                    "getUpdates",
                    offset=next_offset(get_engine(), settings.owner_telegram_id),
                    timeout=25,
                    allowed_updates=["message"],
                )
                for update in updates:
                    handle_update(update, settings, telegram)
            except Exception as exc:
                # httpx exceptions may include URLs with bot tokens. Log only their type.
                logger.error("Bot request failed (%s); retry in 15 seconds", type(exc).__name__)
                time.sleep(15)
    finally:
        telegram.client.close()


if __name__ == "__main__":
    main()
