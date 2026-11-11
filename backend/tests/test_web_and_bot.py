from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.bot import configure, handle_update
from app.config import Settings
from app.main import app


def test_web_shell_assets_and_no_secrets():
    with TestClient(app) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert "Платёжник" in home.text
        assert "Content-Security-Policy" in home.headers
        assert "unsafe-inline" not in home.headers["Content-Security-Policy"]
        assert home.headers["Cache-Control"] == "no-store"
        for asset in ("app.js", "money.mjs", "style.css", "theme.css", "favicon.svg"):
            assert client.get(f"/assets/{asset}").status_code == 200
        config = client.get("/client-config").json()
        assert set(config) == {"auth_mode", "version", "timezone"}
        assert client.get("/assets/../config.py").status_code == 404


@pytest.mark.parametrize(
    "overrides",
    [
        {"auth_mode": "local", "local_api_token": "x" * 32},
        {"app_url": "http://pay.example.com"},
        {"telegram_bot_token": ""},
        {"app_url": "https://pay.example.com/path"},
        {"app_url": "https://user:pass@pay.example.com"},
    ],
)
def test_production_rejects_insecure_settings(overrides):
    values = dict(
        app_env="production",
        app_url="https://pay.example.com",
        auth_mode="telegram",
        telegram_bot_token="123:test-only",
        owner_telegram_id=42,
    )
    values.update(overrides)
    with pytest.raises(ValidationError):
        Settings(**values)


class FakeTelegram:
    def __init__(self, webhook=""):
        self.calls = []
        self.webhook = webhook

    def call(self, method, **payload):
        self.calls.append((method, payload))
        if method == "getWebhookInfo":
            return {"url": self.webhook}
        return True


def settings():
    return Settings(
        auth_mode="telegram",
        app_url="https://pay.example.com",
        owner_telegram_id=42,
        telegram_bot_token="123:test-only",
    )


def test_bot_opens_app_for_owner_only(engine):
    telegram = FakeTelegram()
    update = {
        "update_id": 1,
        "message": {"from": {"id": 42}, "chat": {"id": 42, "type": "private"}, "text": "/app"},
    }
    handle_update(update, settings(), telegram, engine)
    assert len(telegram.calls) == 1
    method, payload = telegram.calls[0]
    assert method == "sendMessage" and payload["chat_id"] == 42
    assert payload["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"] == "https://pay.example.com"
    update["message"]["from"]["id"] = 999
    update["update_id"] = 2
    handle_update(update, settings(), telegram, engine)
    assert len(telegram.calls) == 1
    update["message"]["from"]["id"] = 42
    update["message"]["chat"]["type"] = "group"
    update["update_id"] = 3
    handle_update(update, settings(), telegram, engine)
    assert len(telegram.calls) == 1


def test_bot_configures_owner_menu_and_preserves_existing_webhook():
    telegram = FakeTelegram()
    configure(settings(), telegram)
    commands = telegram.calls[-2][1]["commands"]
    assert {"add", "cancel", "app", "help"} <= {c["command"] for c in commands}
    assert telegram.calls[-1][0] == "setChatMenuButton"
    assert telegram.calls[-1][1]["chat_id"] == 42
    other = FakeTelegram("https://existing.example.com/hook")
    with pytest.raises(RuntimeError):
        configure(settings(), other)
    assert len(other.calls) == 1


def test_production_has_no_public_db_ports():
    import yaml

    root = Path(__file__).resolve().parents[2]
    # In a Docker build only backend/ is present; production file is checked on the host.
    manifest = root / "compose.production.yaml"
    if not manifest.exists():
        return
    config = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    assert "ports" not in config["services"]["db"]
    assert "ports" not in config["services"]["api"]
    assert config["services"]["api"]["environment"]["AUTH_MODE"] == "telegram"
    assert config["services"]["api"]["read_only"] is True
