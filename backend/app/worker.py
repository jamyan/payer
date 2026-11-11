import logging
import time

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_engine
from app.logging_config import configure_logging
from app.notifications import dispatch
from app.services import generate, today

logger = logging.getLogger("platezhnik.worker")


def tick():
    settings = get_settings()
    with Session(get_engine()) as db, db.begin():
        generate(db, settings.owner_telegram_id, today())
    # Delivery is at least once; see the external-send/DB-commit ambiguity in README.
    with Session(get_engine()) as db, db.begin():
        return dispatch(db, settings)


def main():
    configure_logging()
    while True:
        try:
            count = tick()
            if count:
                logger.info("Sent %s reminder(s)", count)
        except Exception as exc:
            logger.error("Worker tick failed (%s); retry in 30 seconds", type(exc).__name__)
        time.sleep(30)


if __name__ == "__main__":
    main()
