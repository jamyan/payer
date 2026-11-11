import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException

from app.auth import validate_init_data
from app.config import Settings


def signed(user=1, auth_date=1000):
    data = {"auth_date": str(auth_date), "user": json.dumps({"id": user}), "query_id": "q"}
    secret = hmac.new(b"WebAppData", b"test-bot-token", hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


def settings():
    return Settings(auth_mode="telegram", telegram_bot_token="test-bot-token", owner_telegram_id=1)


def test_valid_telegram():
    assert validate_init_data(signed(), settings(), now=1000) == 1


@pytest.mark.parametrize(
    "raw,now",
    [
        (signed(user=2), 1000),
        (signed(), 5000),
        (signed(), 900),
        (signed() + "&auth_date=1000", 1000),
        (signed().replace("query_id=q", "query_id=x"), 1000),
        ("hash=x&user=invalid", 1000),
    ],
)
def test_invalid_telegram(raw, now):
    with pytest.raises(HTTPException) as exc:
        validate_init_data(raw, settings(), now=now)
    assert exc.value.status_code == 401
