"""
Проверка готовности окружения, работает и на хосте, и внутри контейнера (в slim-образе нет curl).
Секретов не печатает: только «задан / не задан». Сертификаты берутся из хранилища ОС, поэтому
успешное соединение с GigaChat доказывает, что корень Минцифры установлен правильно.

Запуск:  python scripts/preflight.py        Код возврата: 0 — готово, 1 — есть проблемы.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402  (подключает truststore и читает .env / переменные окружения)
import requests  # noqa: E402

REQUIRED = {
    "TELEGRAM_TOKEN": config.TELEGRAM_TOKEN,
    "ADMIN_IDS": config.ADMIN_IDS,
    "OPENAI_API_KEY": config.OPENAI_API_KEY,
    "GIGACHAT_AUTH_KEY": config.GIGACHAT_AUTH_KEY,
}
# (название, url, допустимые HTTP-коды): без ключей сервисы отвечают 401/405, это и ждём
ENDPOINTS = [
    ("Telegram API", "https://api.telegram.org/", {200, 302, 404}),
    ("OpenAI API", f"{config.OPENAI_BASE_URL.rstrip('/')}/models", {401}),
    ("GigaChat OAuth (сертификат Минцифры)", config.GIGACHAT_OAUTH_URL, {405, 401}),
    ("GigaChat API (сертификат Минцифры)", f"{config.GIGACHAT_API_URL}/models", {401}),
]


class Report:
    def __init__(self):
        self.failed = False

    def ok(self, msg):
        print(f"  [ok]   {msg}")

    def bad(self, msg):
        print(f"  [FAIL] {msg}")
        self.failed = True

    def warn(self, msg):
        print(f"  [warn] {msg}")


def check_env(r: Report) -> None:
    print("Настройки")
    for name, value in REQUIRED.items():
        r.ok(f"{name} задан") if value else r.bad(f"{name} не задан")


def check_network(r: Report) -> None:
    print("Сеть (проверка сертификатов через хранилище ОС)")
    for name, url, expected in ENDPOINTS:
        try:
            code = requests.get(url, timeout=15).status_code
        except requests.exceptions.SSLError as e:
            r.bad(f"{name}: ошибка сертификата ({type(e).__name__}); корень Минцифры не установлен?")
            continue
        except requests.exceptions.RequestException as e:
            r.bad(f"{name}: нет соединения ({type(e).__name__})")
            continue
        r.ok(f"{name}: {code}") if code in expected else r.bad(f"{name}: {code} (ожидалось {sorted(expected)})")


def check_data(r: Report) -> None:
    print("Данные")
    styles = list(config.STYLES_DIR.glob("*.md"))
    r.ok(f"база знаний: {len(styles)} файлов") if len(styles) >= 30 else r.bad("нет data/styles/*.md")
    state = config.STATE_DIR
    if not state.is_dir():
        r.bad(f"каталог состояния {state} не существует")
        return
    # Запись проверяем реально: типичная беда bind-mount — том принадлежит другому uid
    probe = state / ".write_test"
    try:
        probe.write_text("ok")
        probe.unlink()
        r.ok(f"каталог состояния {state} доступен для записи (uid {os.getuid()})")
    except OSError as e:
        r.bad(f"в каталог состояния {state} нельзя писать от uid {os.getuid()}: {e}")
    (r.ok if config.FAISS_INDEX_PATH.exists() else r.warn)(
        "индекс есть" if config.FAISS_INDEX_PATH.exists() else "индекса нет: выполнить scripts/ingest.py")


def main() -> int:
    r = Report()
    check_env(r)
    check_network(r)
    check_data(r)
    print("\nИтог:", "есть проблемы, запускать нельзя" if r.failed else "готово к запуску")
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(main())
