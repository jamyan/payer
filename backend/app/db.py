from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import get_settings


@lru_cache
def get_engine():
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def get_db():
    # Every request commits all changes together or rolls them all back.
    with Session(get_engine(), expire_on_commit=False) as db, db.begin():
        yield db
