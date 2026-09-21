# Развёртывание Beer Style Assistant на VPS

Инструкция для агента, который выкладывает и обслуживает бота. Все команды выполняются на VPS
(Ubuntu/Debian, systemd). Не пропускай проверки: они написаны по реальным граблям.

## Что нужно от человека (без этого не начинать)

1. **SSH-доступ и sudo** на VPS.
2. **Секреты**, переданные вне git и не выводимые в логи и историю команд:
   `TELEGRAM_TOKEN`, `OPENAI_API_KEY`, `GIGACHAT_AUTH_KEY`, `ADMIN_IDS` (Telegram ID админа, число).
3. **Доступ к приватному репозиторию** `github.com/maxigammi/beer-style-assistant` (см. шаг 2).
4. **Подтверждение, что локальный бот остановлен.** Один токен — один процесс: если работают два
   экземпляра, Telegram отвечает `409 Conflict`, и они мешают друг другу.
5. **Разово сверить отпечаток сертификата Минцифры** с официальным источником (шаг 4). Отпечаток
   ниже снят с машины человека, где всё работает, но это не независимая проверка.

## Как это устроено на практике

Агент работает на самом VPS. Человек **сам** кладёт на сервер файлы, которых нет в git: обязательный
`.env` и, по желанию, `data/access.db`. Агент их не создаёт, не читает и не печатает, а только
устанавливает на место с правильными правами (шаг 5). Доступ агента к git делается через deploy key (шаг 2):
это единственное, что требует действия человека в GitHub.

## Схема

| Что | Где |
|---|---|
| Код | `/opt/beer-bot` (git-клон, владелец `beerbot`) |
| Окружение Python | `/opt/beer-bot/.venv` |
| Секреты | `/opt/beer-bot/.env`, права `600` |
| Список пользователей и инвайты | `/opt/beer-bot/data/access.db` — **невоспроизводимые данные** |
| Векторный индекс | `/opt/beer-bot/data/index/` — производный, строится `scripts/ingest.py` |
| Логи | `journalctl -u beer-bot` и `/opt/beer-bot/bot.log` (ротация 5 МБ × 3) |
| Служба | `beer-bot.service` (systemd), пользователь `beerbot`, один экземпляр |

Бот работает через long polling: входящие порты открывать не нужно.

## Установка

### 0. Разведка (ничего не менять)

На этом VPS могут уже работать другие боты. Перед установкой выясни и **сообщи человеку**, а не чини сам:

```bash
whoami; python3 --version; lsb_release -ds 2>/dev/null
systemctl list-units --type=service --state=running | grep -i -E "bot|craft|beer" || true   # соседние боты
[ -n "$(ls -A /opt/beer-bot 2>/dev/null)" ] && echo "/opt/beer-bot НЕ ПУСТ: не перезаписывать, спросить человека"
```

Что важно:
- Если `/opt/beer-bot` уже есть и не пустой, ничего не удалять и не клонировать поверх: спросить.
- **Соседний бот на GigaChat.** Бесплатный тариф GigaChat однопоточный на ключ. Если новый бот получит тот же
  `GIGACHAT_AUTH_KEY`, что и уже работающий, два бота будут мешать друг другу (ответы `429`). Бот делает 3 повтора
  с паузой, но при одновременной нагрузке пользователи будут получать ошибки. Спроси человека: тот же ключ или
  отдельный проект/ключ.
- Никогда не подставлять сюда токен Telegram другого бота: у каждого бота свой токен.

### 1. Пользователь и каталог

```bash
sudo useradd --system --create-home --home-dir /home/beerbot --shell /usr/sbin/nologin beerbot 2>/dev/null || true
sudo mkdir -p /opt/beer-bot && sudo chown beerbot:beerbot /opt/beer-bot
sudo apt-get update && sudo apt-get install -y git python3 python3-venv curl ca-certificates
python3 --version   # нужен 3.11+; проверено на 3.14.4, на других версиях не проверялось
```

Если Python старше 3.11: поставить новее (`python3.12` из репозитория дистрибутива или `uv python install 3.12`)
и создавать venv им.

### 2. Код

Репозиторий приватный. Предпочтительный способ: **read-only deploy key**.

```bash
sudo -u beerbot mkdir -p -m 700 /home/beerbot/.ssh
[ -f /home/beerbot/.ssh/beer_deploy ] || sudo -u beerbot ssh-keygen -q -t ed25519 -N '' -f /home/beerbot/.ssh/beer_deploy -C "beer-bot-vps"
sudo cat /home/beerbot/.ssh/beer_deploy.pub
```

Покажи человеку публичный ключ (одна строка, начинается с `ssh-ed25519`) и **дождись**, пока он добавит его в GitHub
(репозиторий → Settings → Deploy keys → Add deploy key, **без** Allow write access). Пока он не подтвердил,
клонирование не сработает: `Permission denied (publickey)` означает именно это. Затем:

```bash
sudo -u beerbot tee /home/beerbot/.ssh/config >/dev/null <<'E'
Host github.com
  IdentityFile /home/beerbot/.ssh/beer_deploy
  IdentitiesOnly yes
E
sudo -u beerbot chmod 600 /home/beerbot/.ssh/config
sudo -u beerbot ssh-keyscan github.com 2>/dev/null | sudo -u beerbot tee -a /home/beerbot/.ssh/known_hosts >/dev/null
sudo -u beerbot git clone git@github.com:maxigammi/beer-style-assistant.git /opt/beer-bot
cd /opt/beer-bot && git log --oneline | head -3
```

### 3. Окружение Python

```bash
cd /opt/beer-bot
sudo -u beerbot python3 -m venv .venv
sudo -u beerbot .venv/bin/pip install --upgrade pip
sudo -u beerbot .venv/bin/pip install -r requirements.lock
```

Если `requirements.lock` не ставится (версии под другой Python): использовать `requirements.txt` и
**обязательно** прогнать офлайн-тесты (шаг 8), затем сообщить человеку, какие версии встали.

### 4. Сертификат Минцифры (нужен для GigaChat)

GigaChat подписан корневым сертификатом Минцифры, которого нет в стандартных хранилищах.

```bash
curl -fsSL -o /tmp/russian_trusted_root_ca.crt https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt
openssl x509 -in /tmp/russian_trusted_root_ca.crt -noout -subject -fingerprint -sha256
```

Вывод должен совпасть **дословно**:

```
subject=C=RU, O=The Ministry of Digital Development and Communications, CN=Russian Trusted Root CA
sha256 Fingerprint=D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31
```

**Если не совпало или файл не скачался — остановись и сообщи человеку, сертификат не ставить.**
Запасной путь: человек копирует файл с рабочей машины (`/usr/local/share/ca-certificates/russian_trusted_root_ca.crt`)
по `scp`, и его отпечаток сверяется так же.

Совпало — установить в системное хранилище (бот берёт сертификаты ОС через `truststore`):

```bash
sudo install -m 644 /tmp/russian_trusted_root_ca.crt /usr/local/share/ca-certificates/russian_trusted_root_ca.crt
sudo update-ca-certificates
```

Отключать проверку TLS (`verify=False`, `curl -k`) **нельзя** ни при каких условиях.

### 5. Настройки (секреты приносит человек)

Человек заранее кладёт файл с настройками на сервер, например в домашний каталог агента как
`~/beer-bot.env` (копия его локального `.env`; при желании ещё `~/access.db`). Путь он сообщает.
Так сделано, потому что `git clone` не работает в непустой каталог, а секретам нельзя лежать с широкими правами.

```bash
ENV_SRC=~/beer-bot.env        # путь, который назвал человек
test -f "$ENV_SRC" || { echo "Нет файла $ENV_SRC: попроси человека"; exit 1; }
sudo install -m 600 -o beerbot -g beerbot "$ENV_SRC" /opt/beer-bot/.env
shred -u "$ENV_SRC" 2>/dev/null || rm -f "$ENV_SRC"    # исходную копию убрать
```

Содержимое `.env` **не читать и не выводить**. Проверку наличия ключей делает `deploy/preflight.sh` (шаг 6),
он печатает только «задан / не задан». Если файла нет вовсе, попроси человека, а не собирай его сам из шаблона
с выдуманными значениями (`.env.example` показывает, какие переменные бывают).

Значения по умолчанию, которые нужны боту: `GIGACHAT_MODEL=GigaChat-2-Pro`, `SHOW_SOURCES=admin`,
`OPENAI_BASE_URL` пустой (прямой доступ к OpenAI), `ADMIN_IDS` — число (Telegram ID админа). Если что-то
из этого выглядит неправильно, сообщи человеку, но не правь.

Если человек передал `access.db` (сохранить пользователей и инвайты):

```bash
sudo install -m 600 -o beerbot -g beerbot -D ~/access.db /opt/beer-bot/data/access.db && shred -u ~/access.db
```

Если не передал, на сервере начнётся чистая база: админ выдаст инвайты заново.

### 6. Проверка готовности и индексация

```bash
cd /opt/beer-bot
sudo -u beerbot deploy/preflight.sh
```

Скрипт проверяет версию Python, права и наличие ключей в `.env` (значения не печатает) и доступность
четырёх сервисов. **Любой `[FAIL]` — блокирующая проблема**, старт не выполнять. Предупреждение
«индекса нет» на этом этапе нормально. Типичные причины сбоев сети:

| Симптом | Причина |
|---|---|
| OpenAI: `000` или 403 | VPS в регионе, где OpenAI недоступен: нужен другой регион или прокси (`OPENAI_BASE_URL`), сообщить человеку |
| GigaChat: `000` | не установлен сертификат Минцифры (шаг 4) или VPS не видит Сбер |
| Telegram: `000` | у VPS нет доступа к `api.telegram.org` |

Построить индекс (примерно 123 запроса эмбеддингов, стоит копейки):

```bash
sudo -u beerbot .venv/bin/python scripts/ingest.py
# ожидаемо: «Проиндексировано стилей: 123»
```

### 7. Служба

```bash
sudo install -m 644 /opt/beer-bot/deploy/beer-bot.service /etc/systemd/system/beer-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now beer-bot
```

### 8. Проверка

```bash
systemctl is-active beer-bot                    # active
journalctl -u beer-bot -n 40 --no-pager         # ищем «Бот запущен», без ERROR / 409 / SSL
sudo -u beerbot .venv/bin/python tests/test_pipeline.py && \
sudo -u beerbot .venv/bin/python tests/test_compare.py && \
sudo -u beerbot .venv/bin/python tests/test_access.py      # офлайн, без ключей и сети
```

**Приёмка человеком** (агент сам это проверить не может, попросить):
1. Написать боту `/start` со своего аккаунта: в меню «/» появились команды админа.
2. `/invite` → переслать ссылку со второго аккаунта → вход, ответ на вопрос «Какая горечь у American IPA?»
   (ожидается «40 - 70 IBU»).
3. Со сторонним аккаунтом без инвайта бот отвечает «работает по приглашениям».

## Обновление

```bash
cd /opt/beer-bot
sudo -u beerbot git pull --ff-only
sudo -u beerbot .venv/bin/pip install -r requirements.lock
# Только если изменились data/styles/*.md или EMBED_MODEL:
sudo -u beerbot .venv/bin/python scripts/ingest.py
sudo systemctl restart beer-bot && sleep 3 && journalctl -u beer-bot -n 20 --no-pager
```

**Откат:** `sudo -u beerbot git checkout <предыдущий-коммит>`, при необходимости `ingest.py`, `systemctl restart beer-bot`.
Файлы `data/access.db` и `.env` в git не лежат, обновление их не затрагивает.

## Резервная копия списка пользователей

`data/access.db` (инвайты, пользователи, блокировки) не восстановить из репозитория. Консистентная копия
без остановки бота:

```bash
sudo -u beerbot mkdir -p /opt/beer-bot/backups
sudo -u beerbot /opt/beer-bot/.venv/bin/python - <<'E'
import sqlite3, datetime
src = sqlite3.connect("/opt/beer-bot/data/access.db")
dst = sqlite3.connect(f"/opt/beer-bot/backups/access-{datetime.date.today()}.db")
src.backup(dst)
E
```

Ежедневный запуск через cron пользователя `beerbot`, старые копии (старше 14 дней) удалять.

## Чего делать нельзя

- Печатать или коммитить значения из `.env`; выводить токены в отчёты.
- Запускать `bot.py` вручную, пока работает служба (второй экземпляр = `409 Conflict`).
- Удалять или пересоздавать `data/access.db`: пользователям придётся заново выдавать инвайты.
- Запускать бота от root или отключать проверку сертификатов.
- Менять `ADMIN_IDS`, модели и `SHOW_SOURCES` без просьбы человека.
- Делать `git push` с сервера (deploy key только для чтения, так и должно быть).

## Диагностика

| Симптом в логе | Причина и действие |
|---|---|
| `TelegramConflictError` / `409` | Работает второй экземпляр с этим токеном (локальный бот человека?). Остановить лишний |
| `TelegramUnauthorizedError` | Неверный `TELEGRAM_TOKEN` |
| `SSLError` при обращении к GigaChat | Не установлен сертификат Минцифры (шаг 4), затем `systemctl restart beer-bot` |
| `OAuth: HTTP 401` | Неверный `GIGACHAT_AUTH_KEY` или `GIGACHAT_SCOPE` |
| `Не заданы переменные окружения: …` | Пустое значение в `.env` (см. `preflight.sh`) |
| «База знаний не загружена» в ответах | Нет индекса: `scripts/ingest.py`, затем `systemctl restart beer-bot` |
| «Индекс построен моделью X, а сейчас Y» | Сменили `EMBED_MODEL`: выполнить `scripts/ingest.py` |
| Служба падает и перезапускается | `journalctl -u beer-bot -n 100`; после 5 падений за 5 минут systemd остановится сам |

## Что не проверено

Развёртывание на реальном VPS ещё не выполнялось: инструкция проверена на чистом клоне репозитория
(установка из lock-файла и офлайн-тесты) и через `systemd-analyze verify` для службы. Доступность
OpenAI и GigaChat зависит от региона VPS, поэтому `preflight.sh` нужно выполнять до запуска.
