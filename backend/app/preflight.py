"""Проверяет доступ к Telegram без отправки сообщений и изменения настроек бота."""

from app.bot import Telegram
from app.config import get_settings


def main():
    settings = get_settings()
    telegram = Telegram(settings)
    try:
        me = telegram.call("getMe")
        if not me.get("is_bot"):
            raise RuntimeError("Token does not belong to a bot")
        if telegram.call("getWebhookInfo").get("url"):
            raise RuntimeError("Existing webhook found; use a dedicated bot")
        chat = telegram.call("getChat", chat_id=settings.owner_telegram_id)
        if chat.get("type") != "private":
            raise RuntimeError("Owner must be a private Telegram chat")
        print("Telegram: bot and owner chat available, webhook absent; no messages sent.")
    except Exception:
        # Do not include HTTP exception messages: they may expose the bot token.
        raise SystemExit(
            "Telegram preflight failed. Check token, OWNER_TELEGRAM_ID, press Start in the bot, and check connectivity/webhook."
        ) from None
    finally:
        telegram.client.close()


if __name__ == "__main__":
    main()
