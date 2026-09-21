"""
Клиент GigaChat: OAuth-токен с кэшем + /chat/completions.

Бесплатный тариф GigaChat однопоточный, поэтому запросы к чату
сериализуются общим замком.
"""

import logging
import threading
import time
import uuid
from typing import Dict, List, Optional

import requests

from config import (
    GIGACHAT_API_URL, GIGACHAT_AUTH_KEY, GIGACHAT_CA_BUNDLE, GIGACHAT_MODEL,
    GIGACHAT_OAUTH_URL, GIGACHAT_SCOPE, REQUEST_TIMEOUT,
)

logger = logging.getLogger(__name__)

TOKEN_MARGIN_SEC = 60  # обновляем токен за минуту до истечения
MAX_RETRIES = 3


class GigaChatError(RuntimeError):
    pass


class GigaChatClient:
    def __init__(self, model: str = GIGACHAT_MODEL):
        self.model = model
        self._token: Optional[str] = None
        self._token_expires: float = 0.0
        self._token_lock = threading.Lock()
        self._chat_lock = threading.Lock()
        # verify: путь к PEM, если задан; иначе системное хранилище (truststore) / certifi
        self._verify = GIGACHAT_CA_BUNDLE or True

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
                verify=self._verify,
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
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        with self._chat_lock:
            refresh = False
            last_status = None
            for attempt in range(1, MAX_RETRIES + 1):
                resp = requests.post(
                    f"{GIGACHAT_API_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {self._get_token(force=refresh)}"},
                    json=payload,
                    timeout=REQUEST_TIMEOUT,
                    verify=self._verify,
                )
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"]
                last_status = resp.status_code
                if last_status in (401, 429) or last_status >= 500:
                    logger.warning(f"GigaChat HTTP {last_status}, попытка {attempt}/{MAX_RETRIES}")
                    refresh = last_status == 401  # протухший токен — берём новый
                    time.sleep(2 * attempt)
                    continue
                raise GigaChatError(f"HTTP {last_status}: {resp.text[:300]}")
        raise GigaChatError(f"GigaChat недоступен после {MAX_RETRIES} попыток (последний HTTP {last_status})")
