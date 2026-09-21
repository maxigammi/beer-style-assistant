"""
Тесты настройки логов. Регресс: в Docker каталог кода принадлежал root, бот от обычного пользователя не мог
открыть /app/bot.log и падал при старте (контейнер уходил в цикл перезапусков).
Запуск: python tests/test_logging.py
"""

import logging.handlers
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TELEGRAM_TOKEN", "123456:TEST")

import bot  # noqa: E402


def kinds(handlers):
    return [type(h).__name__ for h in handlers]


def test_no_file_means_stdout_only():
    handlers, warning = bot.build_log_handlers(None)
    assert kinds(handlers) == ["StreamHandler"] and warning is None


def test_writable_path_gives_rotating_file():
    with tempfile.TemporaryDirectory() as d:
        handlers, warning = bot.build_log_handlers(Path(d) / "bot.log")
        file_handler = handlers[1]
        assert kinds(handlers) == ["StreamHandler", "RotatingFileHandler"] and warning is None
        assert (file_handler.maxBytes, file_handler.backupCount) == (5_000_000, 3)  # файл не растёт вечно
        assert (Path(d) / "bot.log").exists()
        file_handler.close()


def test_unwritable_log_does_not_kill_the_bot():
    """Каталог принадлежит другому пользователю или файловая система только для чтения."""
    handlers, warning = bot.build_log_handlers(Path("/nonexistent/dir/bot.log"))
    assert kinds(handlers) == ["StreamHandler"]
    assert "/nonexistent/dir/bot.log" in warning and "недоступен для записи" in warning
    if os.geteuid() != 0:  # от root запись проходит всегда
        with tempfile.TemporaryDirectory() as d:
            os.chmod(d, 0o500)
            handlers, warning = bot.build_log_handlers(Path(d) / "bot.log")
            os.chmod(d, 0o700)
        assert kinds(handlers) == ["StreamHandler"] and "Permission denied" in warning


def run_python(code, **env):
    full_env = {k: v for k, v in os.environ.items() if k not in ("LOG_FILE",)} | env
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=full_env, capture_output=True, text=True)


def test_config_log_file_settings():
    show = "import config; print(repr(config.LOG_FILE))"
    assert run_python(show).stdout.strip() == f"PosixPath('{ROOT / 'bot.log'}')"      # по умолчанию: рядом с кодом
    assert run_python(show, LOG_FILE="").stdout.strip() == "None"                      # пусто: без файла
    assert run_python(show, LOG_FILE="  ").stdout.strip() == "None"
    assert run_python(show, LOG_FILE="/var/log/beer.log").stdout.strip() == "PosixPath('/var/log/beer.log')"


def test_process_starts_when_log_file_cannot_be_opened():
    """Именно та ситуация, в которой падал контейнер: import bot в чистом процессе."""
    result = run_python("import bot; print('IMPORT_OK')", LOG_FILE="/nonexistent/dir/bot.log", TELEGRAM_TOKEN="1:T")
    assert result.returncode == 0 and "IMPORT_OK" in result.stdout, result.stderr[-400:]
    assert "недоступен для записи" in result.stderr  # причина видна в логе, а не проглочена молча


def test_stdout_only_mode_is_silent_about_the_missing_file():
    result = run_python("import bot; print('IMPORT_OK')", LOG_FILE="", TELEGRAM_TOKEN="1:T")
    assert result.returncode == 0 and "IMPORT_OK" in result.stdout
    assert "недоступен" not in result.stderr  # отключённый файл — не предупреждение


def test_default_log_path_in_a_read_only_code_directory():
    """
    Точное воспроизведение боевой ошибки: LOG_FILE не задан (путь по умолчанию: bot.log рядом с кодом),
    а каталог кода недоступен для записи, как /app, созданный от root при запуске под обычным пользователем.
    """
    if os.geteuid() == 0:
        return  # от root запись проходит, воспроизвести нечем
    with tempfile.TemporaryDirectory() as d:
        app = Path(d) / "app"
        app.mkdir()
        for name in ("bot.py", "config.py", "access.py", "smalltalk.py"):
            (app / name).write_text((ROOT / name).read_text())
        for pkg in ("llm", "rag"):
            (app / pkg).mkdir()
            for f in (ROOT / pkg).glob("*.py"):
                (app / pkg / f.name).write_text(f.read_text())
        os.chmod(app, 0o555)  # читать можно, создавать файлы нельзя
        try:
            env = {k: v for k, v in os.environ.items() if k != "LOG_FILE"} | {"TELEGRAM_TOKEN": "1:T"}
            result = subprocess.run([sys.executable, "-c", "import bot; print('IMPORT_OK')"], cwd=app, env=env,
                                    capture_output=True, text=True)
        finally:
            os.chmod(app, 0o755)
    assert result.returncode == 0 and "IMPORT_OK" in result.stdout, result.stderr[-500:]
    assert "Permission denied" in result.stderr and "логи только в stdout" in result.stderr


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} тестов пройдено")
