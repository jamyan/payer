"""Проверка серверного .env без печати паролей и токенов. Только стандартная библиотека."""

import re
import sys
from pathlib import Path


def validate(path):
    values = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key):
            raise ValueError("Некорректная строка .env")
        if key in values:
            raise ValueError(f"Повтор переменной {key}")
        values[key] = value.strip().strip("\"'")
    required = [
        "APP_DOMAIN",
        "ACME_EMAIL",
        "POSTGRES_PASSWORD",
        "OWNER_TELEGRAM_ID",
        "TELEGRAM_BOT_TOKEN",
    ]
    for key in required:
        if not values.get(key) or "replace-with" in values[key]:
            raise ValueError(f"Заполни {key}")
    if not re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", values["APP_DOMAIN"]):
        raise ValueError("APP_DOMAIN: нужен домен без https:// и пути")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", values["ACME_EMAIL"]):
        raise ValueError("ACME_EMAIL: некорректный адрес")
    if not re.fullmatch(r"[A-Za-z0-9]{32,128}", values["POSTGRES_PASSWORD"]):
        raise ValueError("POSTGRES_PASSWORD: 32–128 букв/цифр")
    if (
        not values["OWNER_TELEGRAM_ID"].isdigit()
        or int(values["OWNER_TELEGRAM_ID"]) <= 0
    ):
        raise ValueError("OWNER_TELEGRAM_ID: нужно положительное целое число")
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", values["TELEGRAM_BOT_TOKEN"]):
        raise ValueError("TELEGRAM_BOT_TOKEN: некорректный формат")
    return values


if __name__ == "__main__":
    try:
        validate(Path(sys.argv[1] if len(sys.argv) > 1 else ".env"))
    except (OSError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("Настройки заполнены; секреты не выводятся.")
