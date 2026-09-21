"""
Клиент GigaChat: OAuth-токен с кэшем + /chat/completions.

Бесплатный тариф GigaChat однопоточный: пока идёт один запрос по ключу, второй мгновенно получает
429 (очереди нет; замер). Внутри бота запросы сериализуются замком, а если ключ занят другим
процессом (например, соседним ботом), клиент терпеливо ждёт: см. GIGACHAT_MAX_WAIT_SEC.
"""

import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

from config import (
    GIGACHAT_API_URL, GIGACHAT_AUTH_KEY, GIGACHAT_MAX_WAIT_SEC, GIGACHAT_MODEL,
    GIGACHAT_OAUTH_URL, GIGACHAT_SCOPE, REQUEST_TIMEOUT,
)

logger = logging.getLogger(__name__)

TOKEN_MARGIN_SEC = 60  # обновляем токен за минуту до истечения
MAX_RETRIES = 3  # для протухшего токена (401) и ошибок сервера (5xx)
RATE_LIMIT_BASE_DELAY = 2.0   # пауза при 429: 2, 4, 8 с, дальше не больше 10 с (плюс случайная добавка до 1 с)
RATE_LIMIT_MAX_DELAY = 10.0


class GigaChatError(RuntimeError):
    pass


class GigaChatBusy(GigaChatError):
    """Ключ всё время занят другим запросом: за отведённое время место так и не освободилось."""


@dataclass
class ChatResult:
    text: str
    tokens: int  # расход по usage: total_tokens + precached_prompt_tokens (см. chat_ex)


class GigaChatClient:
    def __init__(self, model: str = GIGACHAT_MODEL):
        self.model = model
        self._token: Optional[str] = None
        self._token_expires: float = 0.0
        self._token_lock = threading.Lock()
        self._chat_lock = threading.Lock()

    def _get_token(self, force: bool = False) -> str:
        with self._token_lock:
            if not force and self._token and time.time() < self._token_expires - TOKEN_MARGIN_SEC:
                return self._token
            resp = requests.post(
                GIGACHAT_OAUTH_URL,
                headers={
                    "Authorization": f"Basic {GIGACHAT_AUTH_KEY}",
                    "RqUID": str(uuid.uuid4()),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"scope": GIGACHAT_SCOPE},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                raise GigaChatError(f"OAuth: HTTP {resp.status_code} {resp.text[:200]}")
            data = resp.json()
            self._token = data["access_token"]
            self._token_expires = data["expires_at"] / 1000  # API отдаёт миллисекунды
            logger.info("Получен новый токен GigaChat")
            return self._token

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.3,
             max_tokens: int = 1000) -> str:
        return self.chat_ex(messages, temperature, max_tokens).text

    def chat_ex(self, messages: List[Dict[str, str]], temperature: float = 0.3,
                max_tokens: int = 1000) -> ChatResult:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        with self._chat_lock:
            refresh = False
            failures = 0      # 401 и 5xx
            rate_limited = 0  # 429
            waited = 0.0
            while True:
                resp = requests.post(
                    f"{GIGACHAT_API_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {self._get_token(force=refresh)}"},
                    json=payload,
                    timeout=REQUEST_TIMEOUT,
                )
                status = resp.status_code
                if status == 200:
                    data = resp.json()
                    usage = data.get("usage", {})
                    # GigaChat не включает в total_tokens повторяющийся (кэшируемый) промпт, а отдаёт его
                    # отдельно в precached_prompt_tokens; для учёта лимитов считаем всё — оценка сверху
                    tokens = usage.get("total_tokens", 0) + usage.get("precached_prompt_tokens", 0)
                    return ChatResult(text=data["choices"][0]["message"]["content"], tokens=tokens)

                if status == 429:  # ключ занят чужим запросом: ждём с нарастающей паузой в пределах бюджета
                    delay = min(RATE_LIMIT_BASE_DELAY * 2 ** rate_limited, RATE_LIMIT_MAX_DELAY) + random.uniform(0, 1)
                    rate_limited += 1
                    if waited + delay > GIGACHAT_MAX_WAIT_SEC:
                        raise GigaChatBusy(f"GigaChat занят: HTTP 429 {rate_limited} раз подряд, ждали {waited:.0f} с")
                    logger.warning(f"GigaChat HTTP 429 (ключ занят), жду {delay:.1f} с, уже ждали {waited:.0f} с")
                    time.sleep(delay)
                    waited += delay
                    continue

                if status == 401 or status >= 500:
                    failures += 1
                    if failures >= MAX_RETRIES:
                        raise GigaChatError(f"GigaChat недоступен после {MAX_RETRIES} попыток (последний HTTP {status})")
                    logger.warning(f"GigaChat HTTP {status}, попытка {failures}/{MAX_RETRIES}")
                    refresh = status == 401  # протухший токен — берём новый
                    time.sleep(2 * failures)
                    continue

                raise GigaChatError(f"HTTP {status}: {resp.text[:300]}")
