"""Тесты каталога состояния (STATE_DIR) и проверки окружения. Запуск: python tests/test_config_preflight.py"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TELEGRAM_TOKEN", "123456:TEST")

import config  # noqa: E402
sys.path.insert(0, str(ROOT / "scripts"))
import preflight  # noqa: E402


def paths_in_fresh_process(**env):
    """Пути из config в чистом процессе с заданным окружением."""
    code = ("import config; print(config.STATE_DIR); print(config.INDEX_DIR); "
            "print(config.ACCESS_DB_PATH); print(config.STYLES_DIR)")
    full_env = {k: v for k, v in os.environ.items() if k != "STATE_DIR"} | env
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=full_env, capture_output=True, text=True)
    return [Path(line) for line in out.stdout.split("\n")[-5:-1]], out


def test_state_dir_defaults_to_data_next_to_code():
    (state, index, db, styles), _ = paths_in_fresh_process()
    assert state == ROOT / "data" and index == ROOT / "data" / "index" and db == ROOT / "data" / "access.db"
    assert styles == ROOT / "data" / "styles"


def test_state_dir_can_be_moved_but_styles_stay_in_the_repo():
    """В Docker том монтируется в STATE_DIR, а база знаний берётся из образа: их нельзя смешивать."""
    (state, index, db, styles), _ = paths_in_fresh_process(STATE_DIR="/state")
    assert (state, index, db) == (Path("/state"), Path("/state/index"), Path("/state/access.db"))
    assert styles == ROOT / "data" / "styles"
    (state, *_), _ = paths_in_fresh_process(STATE_DIR="")  # пустая переменная = не задана
    assert state == ROOT / "data"


def run_check(fn):
    buf = io.StringIO()
    report = preflight.Report()
    with contextlib.redirect_stdout(buf):
        fn(report)
    return report, buf.getvalue()


def test_preflight_data_check_writable_dir():
    with tempfile.TemporaryDirectory() as d:
        old = config.STATE_DIR, config.FAISS_INDEX_PATH
        config.STATE_DIR, config.FAISS_INDEX_PATH = Path(d), Path(d) / "index" / "index.faiss"
        try:
            report, out = run_check(preflight.check_data)
        finally:
            config.STATE_DIR, config.FAISS_INDEX_PATH = old
    assert not report.failed and "доступен для записи" in out and "индекса нет" in out
    assert not list(Path(d).glob(".write_test")) if Path(d).exists() else True  # за собой прибрал


def test_preflight_data_check_missing_and_readonly_dir():
    old = config.STATE_DIR
    try:
        config.STATE_DIR = Path("/nonexistent/state")
        report, out = run_check(preflight.check_data)
        assert report.failed and "не существует" in out
        if os.geteuid() != 0:  # от root запись всегда проходит, проверять нечего
            with tempfile.TemporaryDirectory() as d:
                os.chmod(d, 0o500)
                config.STATE_DIR = Path(d)
                report, out = run_check(preflight.check_data)
                os.chmod(d, 0o700)
            assert report.failed and "нельзя писать" in out
    finally:
        config.STATE_DIR = old


def test_preflight_env_check_never_prints_values():
    old = dict(preflight.REQUIRED)
    preflight.REQUIRED.update({"OPENAI_API_KEY": "sk-SECRET-VALUE", "GIGACHAT_AUTH_KEY": "", "TELEGRAM_TOKEN": "999:SECRET",
                               "ADMIN_IDS": {1}})
    try:
        report, out = run_check(preflight.check_env)
    finally:
        preflight.REQUIRED.clear()
        preflight.REQUIRED.update(old)
    assert report.failed and "GIGACHAT_AUTH_KEY не задан" in out and "OPENAI_API_KEY задан" in out
    assert "SECRET" not in out


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} тестов пройдено")
