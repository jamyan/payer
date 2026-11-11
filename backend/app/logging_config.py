"""Не допускаем попадания токена Telegram из URL в журналы HTTP-библиотек."""

import logging


def silence_http_logging():
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        logger.setLevel(logging.CRITICAL + 1)


def configure_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    silence_http_logging()
