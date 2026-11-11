# Развёртывание «Платёжника» на Linux

Пример рассчитан на Ubuntu 24.04, Docker Compose v2 и установку в `/opt/platezhnik`. Подставь свой домен вместо `payer.example.com`. Приложение запускает PostgreSQL, миграции, API, worker и Telegram-бота. Прокси подключается к внешней Docker-сети `proxy` и обращается к `platezhnik-api:8000`. У базы и API в production нет опубликованных портов.

## Подготовка сервера и бота

1. Направь DNS-запись `A` на сервер. Добавь `AAAA`, только если сервер принимает HTTPS по IPv6. Открой входящие 80/TCP и 443/TCP для выбранного прокси. Серверу нужны исходящие HTTPS-запросы к Telegram и реестрам контейнеров. Если эти порты заняты, используй существующий прокси или перенеси его в запланированное окно.
2. Установи Git, Python 3, curl, Docker Engine и Compose v2. Для чистой Ubuntu 24.04 можно использовать пакеты дистрибутива. На сервере с работающим Docker проверь `docker compose version` и сохрани текущую схему установки.

   ```bash
   sudo apt-get update
   sudo apt-get install -y git python3 curl docker.io docker-compose-v2
   sudo systemctl enable --now docker
   docker compose version
   ```

3. Создай отдельного бота через `@BotFather`, сохрани токен и нажми Start в личном чате. Для `OWNER_TELEGRAM_ID` нужен твой числовой ID, а не ID бота. Для нового бота после отправки сообщения можно вызвать `getUpdates` до запуска polling и посмотреть `message.from.id` в своём сообщении. Токен подставляй локально, исключи его из истории команд и снимков экрана. При действующем webhook или втором polling-процессе используй выделенного бота.

   Документация [Docker Engine для Ubuntu](https://docs.docker.com/engine/install/ubuntu/), [Telegram Bot API](https://core.telegram.org/bots/api#getupdates) и [Telegram Mini Apps](https://core.telegram.org/bots/webapps).

4. Склонируй репозиторий пользователем, у которого есть доступ к Docker. Для первого запуска создай закрытый `.env`. При обновлении используй имеющийся файл.

   ```bash
   sudo install -d -o "$(id -u)" -g "$(id -g)" /opt/platezhnik
   git clone https://github.com/jamyan/payer.git /opt/platezhnik
   cd /opt/platezhnik
   install -m 600 .env.production.example .env
   python3 -c 'import secrets; print(secrets.token_hex(32))'
   ```

   | Переменная | Что вписать |
   | --- | --- |
   | `APP_DOMAIN` | Свой домен без протокола и пути. |
   | `ACME_EMAIL` | Свой адрес для ACME. Для NPM он также нужен валидатору настроек, а сертификат запрашивается в панели NPM. |
   | `POSTGRES_PASSWORD` | Отдельный случайный пароль из 32–128 букв и цифр. Подходит результат команды выше. |
   | `OWNER_TELEGRAM_ID` | Положительное целое число. |
   | `TELEGRAM_BOT_TOKEN` | Токен выделенного бота. |
   | `TELEGRAM_NOTIFICATIONS_ENABLED` | `true` для отправки напоминаний. |
   | `APP_VERSION`, `INIT_DATA_MAX_AGE_SECONDS` | Тег образа и срок проверки подписи Mini App. По умолчанию `1.4.0` и `3600` секунд. |

   Проверь заполнение без вывода секретов и создай общую сеть, если её ещё нет.

   ```bash
   python3 scripts/check_env.py .env
   docker compose -f compose.production.yaml config --quiet
   docker network inspect proxy >/dev/null 2>&1 || docker network create proxy
   ```

## HTTPS через Caddy

Из каталога приложения запусти отдельный стек прокси. Сертификаты сохраняются в томе `caddy_data`.

```bash
docker compose --env-file .env -f deploy/compose.caddy.yaml up -d
docker compose --env-file .env -f deploy/compose.caddy.yaml logs --tail=50 caddy
```

Caddy использует домен из `.env` и получает сертификат автоматически. Пока API запускается, прокси может отвечать `502`. [Документация Caddy об автоматическом HTTPS](https://caddyserver.com/docs/automatic-https).

## HTTPS через Nginx Proxy Manager

Если NPM уже работает, добавь внешнюю сеть `proxy` к его постоянной Compose-конфигурации. Сохрани уже заданные сети и тома. Применение правок может ненадолго прервать работу обслуживаемых им сайтов.

Для новой установки возьми [Compose-пример из руководства NPM](https://nginxproxymanager.com/guide/) в отдельном каталоге. У сервиса NPM добавь `networks: [proxy]`, а в конце файла опиши сеть:

```yaml
networks:
  proxy:
    external: true
    name: proxy
```

В новом стеке публикуй порт панели как `127.0.0.1:81:81`, а 80 и 443 оставь доступными для HTTP/HTTPS. Запусти NPM командой `docker compose up -d` из его каталога. Для доступа к панели с рабочей станции открой SSH-туннель и зайди на `http://127.0.0.1:8181`.

```bash
ssh -N -L 8181:127.0.0.1:81 user@payer.example.com
```

Создай **Proxy Host**. В поле Domain Names укажи свой домен, Scheme — `http`, Forward Hostname — `platezhnik-api`, Forward Port — `8000`. На вкладке **SSL** запроси сертификат Let's Encrypt и включи **Force SSL**. Если NPM не даёт сохранить хост до появления API, запусти приложение и настрой прокси сразу после старта контейнеров. Если проверка HTTPS в сценарии уже завершилась ошибкой, после настройки прокси проверь `/health` вручную.

## Запуск и проверка

```bash
cd /opt/platezhnik
bash scripts/deploy.sh
docker compose -f compose.production.yaml ps -a
curl --fail --show-error https://payer.example.com/health
```

Сценарий проверяет настройки и доступ к Telegram, собирает образ, прогоняет тесты на отдельной PostgreSQL, применяет миграции и проверяет HTTPS. При обновлении создаётся резервная копия существующей базы и сохраняется предыдущий образ. **На время миграции API, worker и бот останавливаются.** Запускай один экземпляр сценария за раз.

Ожидаемый ответ `/health` содержит `"status":"ok"`. Для контейнера `migrate` штатный результат — выход с кодом 0. Отправь своему боту `/start` или `/app` и открой Mini App. Затем отправь `/login` и введи код на своём HTTPS-сайте в обычном браузере. Проверь создание обязательства и запись оплаты на тестовых данных.

## Копии и проверка восстановления

```bash
bash scripts/backup.sh
bash scripts/verify_backup.sh backups/ИМЯ_КОПИИ.dump
```

Дампы и SHA-256 сохраняются в `backups/`. Проверка создаёт временную базу с префиксом `restorecheck_`, разворачивает в неё дамп, проверяет таблицы и удаляет временную базу. Рабочая БД остаётся доступной. Храни защищённую копию дампов вне сервера и сохраняй `.env` отдельно. Копии автоматически не ротируются.

Для ежедневного копирования в 03:15 по Иркутску установи готовые systemd unit-файлы. Если каталог отличается от `/opt/platezhnik`, сначала поправь пути в service-файле.

```bash
sudo install -m 644 deploy/systemd/platezhnik-backup.service /etc/systemd/system/
sudo install -m 644 deploy/systemd/platezhnik-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now platezhnik-backup.timer
systemctl list-timers platezhnik-backup.timer
```

## Обновление и откат

Проверь резервную копию, сохрани текущий коммит и обновляйся до нужного коммита после просмотра изменений. Затем запусти `bash scripts/deploy.sh`. Во время миграции 0004 дата старой оплаты заполняется датой записи по Иркутску. Её можно исправить в приложении, если известна фактическая дата.

Если схема совместима с прежним образом, проверь тег в `.deploy-state/previous-image` и верни образ. Пример для `APP_VERSION=1.4.0`.

```bash
previous_image="$(cat .deploy-state/previous-image)"
docker image inspect "$previous_image" >/dev/null &&
  docker tag "$previous_image" platezhnik:1.4.0 &&
  docker compose -f compose.production.yaml up -d --no-build --no-deps api worker bot
```

Если схема несовместима, сначала останови запись и сохрани текущую базу. В отдельное окно восстанови проверенный дамп до обновления и запусти прежние исходники и образ. **Восстановление старого дампа потеряет записи после момента копии**, если их заранее не перенести. Для 0004 автоматический downgrade заблокирован. **`docker compose down -v` удаляет данные томов выбранного стека.**

## Диагностика

| Симптом | Что проверить |
| --- | --- |
| Telegram preflight завершается ошибкой | Токен, числовой ID, Start в личном чате, доступ к Telegram, отсутствие webhook. |
| `502` или `503` | `docker compose -f compose.production.yaml ps -a`, миграции, подключение обоих стеков к `proxy`, адрес `platezhnik-api:8000`. |
| Сертификат не выдан | DNS A/AAAA, порты 80/443, журналы выбранного прокси. |
| Бот не отвечает | `docker compose -f compose.production.yaml logs --tail=100 bot`, отсутствие второго polling-процесса. |
| Нет напоминаний | Флаг отправки, срок и статус начисления, журнал worker, доступность чата владельца. |
| Нет копий | `journalctl -u platezhnik-backup.service`, место на диске и права каталога. |

Для проверки Compose используй `config --quiet`. Полный вывод `config` содержит подставленные секреты. Храни `.env`, ключи, дампы и журналы вне репозитория.
