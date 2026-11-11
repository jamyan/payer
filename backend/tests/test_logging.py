import logging

from app.logging_config import silence_http_logging


def test_http_client_logs_and_children_cannot_expose_token(caplog):
    with caplog.at_level(logging.DEBUG):
        silence_http_logging()
        logging.getLogger("httpx").info("URL contains fake-bot-token")
        child = logging.getLogger("httpcore.connection")
        child.setLevel(logging.DEBUG)
        try:
            child.warning("URL contains fake-bot-token")
        finally:
            child.setLevel(logging.NOTSET)
        logging.getLogger("platezhnik.bot").info("Bot ready")
    assert "fake-bot-token" not in caplog.text
    assert "Bot ready" in caplog.text
