from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

TZ = ZoneInfo("Asia/Irkutsk")
CURRENCY = "RUB"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: Literal["development", "production"] = "development"
    app_url: str = "http://127.0.0.1:8000"
    database_url: str = "postgresql+psycopg://platezhnik:platezhnik@localhost:5432/platezhnik"
    owner_telegram_id: int = 1
    auth_mode: Literal["telegram", "local"] = "telegram"
    local_api_token: SecretStr = SecretStr("")
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_notifications_enabled: bool = False
    init_data_max_age_seconds: int = 3600

    @model_validator(mode="after")
    def check(self):
        parsed = urlparse(self.app_url)
        if (
            not parsed.hostname
            or parsed.scheme not in ("http", "https")
            or parsed.username
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("APP_URL must be a public origin without credentials, query or fragment")
        if parsed.path not in ("", "/"):
            raise ValueError("APP_URL must not include a path")
        if self.app_env == "production":
            if self.auth_mode != "telegram":
                raise ValueError("Production requires AUTH_MODE=telegram")
            if parsed.scheme != "https" or parsed.hostname in ("localhost", "127.0.0.1"):
                raise ValueError("Production requires a public HTTPS APP_URL")
            if not self.telegram_bot_token.get_secret_value():
                raise ValueError("Production requires TELEGRAM_BOT_TOKEN")
        if self.owner_telegram_id <= 0:
            raise ValueError("OWNER_TELEGRAM_ID must be positive")
        if self.auth_mode == "local" and len(self.local_api_token.get_secret_value()) < 32:
            raise ValueError("LOCAL_API_TOKEN must contain at least 32 characters")
        if self.telegram_notifications_enabled and not self.telegram_bot_token.get_secret_value():
            raise ValueError("TELEGRAM_BOT_TOKEN is required for notifications")
        if not 60 <= self.init_data_max_age_seconds <= 86400:
            raise ValueError("initData lifetime must be between 60 and 86400 seconds")
        return self


@lru_cache
def get_settings():
    return Settings()
