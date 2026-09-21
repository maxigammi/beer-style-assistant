"""Тесты клиента GigaChat на подменённом HTTP (без сети). Запуск: python tests/test_gigachat.py"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import gigachat  # noqa: E402
from llm.gigachat import GigaChatBusy, GigaChatClient, GigaChatError  # noqa: E402


def response(status, payload=None, text=""):
    r = MagicMock()
    r.status_code, r.text = status, text
    r.json.return_value = payload or {}
    return r


OAUTH_OK = response(200, {"access_token": "T", "expires_at": (time.time() + 1800) * 1000})


def chat_ok(usage):
    return response(200, {"choices": [{"message": {"content": "ответ"}}], "usage": usage})


SLEEPS = []  # паузы последнего run(): по ним проверяем расписание ожидания


def run(*replies, max_wait=None):
    """Клиент, у которого requests.post отвечает по очереди; возвращает (результат, вызовы post)."""
    SLEEPS.clear()
    with patch.object(gigachat.requests, "post", side_effect=list(replies)) as post, \
         patch.object(gigachat.time, "sleep", side_effect=SLEEPS.append), \
         patch.object(gigachat.random, "uniform", return_value=0.0), \
         patch.object(gigachat, "GIGACHAT_MAX_WAIT_SEC", max_wait if max_wait is not None else 45.0):
        client = GigaChatClient()
        result = client.chat_ex([{"role": "user", "content": "q"}])
    return result, post


def test_tokens_include_precached_prompt():
    result, _ = run(OAUTH_OK, chat_ok({"prompt_tokens": 50, "completion_tokens": 40, "total_tokens": 90,
                                       "precached_prompt_tokens": 237}))
    assert result.text == "ответ" and result.tokens == 90 + 237


def test_tokens_without_precached_field():
    result, _ = run(OAUTH_OK, chat_ok({"total_tokens": 1973}))
    assert result.tokens == 1973
    assert run(OAUTH_OK, response(200, {"choices": [{"message": {"content": "x"}}]}))[0].tokens == 0


def test_expired_token_is_refreshed_once():
    result, post = run(OAUTH_OK, response(401, text="expired"), OAUTH_OK, chat_ok({"total_tokens": 5}))
    assert result.tokens == 5 and post.call_count == 4  # oauth, чат 401, новый oauth, чат ок


def test_server_errors_are_retried_then_reported():
    result, post = run(OAUTH_OK, response(500), response(503), chat_ok({"total_tokens": 7}))
    assert result.tokens == 7 and SLEEPS == [2, 4]
    try:
        run(OAUTH_OK, response(500), response(500), response(500))
        assert False, "должно быть исключение"
    except GigaChatError as e:
        assert not isinstance(e, GigaChatBusy) and "недоступен" in str(e) and "500" in str(e)


def test_busy_key_is_waited_out_with_growing_pauses():
    """Ключ занят соседним ботом: 429 — это «подожди», а не «ошибка»; ждём 2, 4, 8, потом по 10 с."""
    busy = [response(429) for _ in range(5)]
    result, post = run(OAUTH_OK, *busy, chat_ok({"total_tokens": 9}))
    assert result.tokens == 9
    assert SLEEPS == [2, 4, 8, 10, 10]  # всего 34 с в пределах бюджета 45 с
    assert post.call_count == 1 + 5 + 1


def test_waiting_gives_up_after_the_budget_with_a_dedicated_error():
    try:
        run(OAUTH_OK, *[response(429) for _ in range(20)], max_wait=45)
        assert False, "должно быть исключение"
    except GigaChatBusy as e:
        assert "429" in str(e)
        assert isinstance(e, GigaChatError)  # прежние обработчики GigaChatError продолжают работать
    assert sum(SLEEPS) <= 45 and sum(SLEEPS) > 30  # ждали почти весь бюджет, но не больше него


def test_wait_budget_is_configurable_and_zero_means_no_waiting():
    try:
        run(OAUTH_OK, response(429), max_wait=0)
        assert False
    except GigaChatBusy:
        pass
    assert SLEEPS == []


def test_rate_limit_does_not_consume_the_error_retries():
    """429 и 500 считаются отдельно: несколько 429 не должны съесть попытки на настоящие сбои."""
    result, _ = run(OAUTH_OK, response(429), response(429), response(500), response(429), chat_ok({"total_tokens": 1}))
    assert result.tokens == 1


def test_client_error_is_not_retried():
    try:
        run(OAUTH_OK, response(400, text="bad request"))
        assert False
    except GigaChatError as e:
        assert "400" in str(e)


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} тестов пройдено")
