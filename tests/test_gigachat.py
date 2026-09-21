"""Тесты клиента GigaChat на подменённом HTTP (без сети). Запуск: python tests/test_gigachat.py"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import gigachat  # noqa: E402
from llm.gigachat import GigaChatClient, GigaChatError  # noqa: E402


def response(status, payload=None, text=""):
    r = MagicMock()
    r.status_code, r.text = status, text
    r.json.return_value = payload or {}
    return r


OAUTH_OK = response(200, {"access_token": "T", "expires_at": (time.time() + 1800) * 1000})


def chat_ok(usage):
    return response(200, {"choices": [{"message": {"content": "ответ"}}], "usage": usage})


def run(*replies):
    """Клиент, у которого requests.post отвечает по очереди; возвращает (клиент, вызовы post)."""
    with patch.object(gigachat.requests, "post", side_effect=list(replies)) as post, \
         patch.object(gigachat.time, "sleep"):
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
    result, post = run(OAUTH_OK, response(500), response(429), chat_ok({"total_tokens": 7}))
    assert result.tokens == 7
    try:
        run(OAUTH_OK, response(500), response(500), response(500))
        assert False, "должно быть исключение"
    except GigaChatError as e:
        assert "недоступен" in str(e) and "500" in str(e)


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
