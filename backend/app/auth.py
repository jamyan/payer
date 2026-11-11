import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.browser_auth import session_owner
from app.config import Settings, get_settings
from app.db import get_db


def validate_init_data(raw: str, settings: Settings, now: int | None = None):
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=30)
        data = dict(pairs)
        if len(data) != len(pairs):
            raise ValueError("Duplicate keys")
        received = data.pop("hash")
        check = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
        token = settings.telegram_bot_token.get_secret_value()
        if not token:
            raise ValueError("Missing bot token")
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received):
            raise ValueError("Invalid signature")
        age = (int(time.time()) if now is None else now) - int(data["auth_date"])
        if age < -30 or age > settings.init_data_max_age_seconds:
            raise ValueError("Expired initData")
        owner = json.loads(data["user"])["id"]
        if type(owner) is not int or owner != settings.owner_telegram_id:
            raise ValueError("Owner only")
        return owner
    except (ValueError, KeyError, TypeError):
        raise HTTPException(401, "Недействительные или просроченные данные Telegram") from None


def get_owner(
    request: Request,
    authorization: str = Header(default="", max_length=16384),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db, scope="function"),
):
    if not authorization:
        return session_owner(db, request, settings)
    scheme, _, credentials = authorization.partition(" ")
    if settings.auth_mode == "local":
        if scheme.lower() == "bearer" and hmac.compare_digest(
            credentials.encode(), settings.local_api_token.get_secret_value().encode()
        ):
            return settings.owner_telegram_id
        raise HTTPException(401, "Нужен локальный Bearer-токен")
    if scheme.lower() != "tma":
        raise HTTPException(401, "Нужен Authorization: tma <Telegram.WebApp.initData>")
    return validate_init_data(credentials, settings)
