"""
Кеш ответов по точному совпадению вопроса (после нормализации). Цель — не ждать освобождения
общего freemium-ключа GigaChat (до GIGACHAT_MAX_WAIT_SEC) на повторный вопрос: попадание в кеш
вообще не обращается к GigaChat, ни на понимание вопроса, ни на ответ.

В памяти процесса, без персистентности: трафик маленький, при рестарте кеш прогреется заново за
первые же вопросы. Совпадение только точное (без похожести по смыслу): для этого бота риск отдать
уверенный, но неверный ответ из-за случайно похожего, но другого по сути вопроса, перевешивает
пользу от более частых попаданий в кеш.
"""

import re
from collections import OrderedDict
from typing import Optional

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Нижний регистр, «ё»->«е», без пунктуации, без лишних пробелов."""
    text = text.lower().replace("ё", "е")
    text = _PUNCT.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


class AnswerCache:
    """FIFO-ограничение размера — простая защита от неограниченного роста, не тонкая политика вытеснения."""

    def __init__(self, max_size: int = 500):
        self.max_size = max_size
        self._store: "OrderedDict[str, object]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, query: str) -> Optional[object]:
        key = normalize(query)
        if key in self._store:
            self._store.move_to_end(key)
            self.hits += 1
            return self._store[key]
        self.misses += 1
        return None

    def set(self, query: str, value: object) -> None:
        key = normalize(query)
        self._store[key] = value
        self._store.move_to_end(key)
        if len(self._store) > self.max_size:
            self._store.popitem(last=False)

    def clear(self) -> None:
        self._store.clear()
        self.hits = self.misses = 0

    def __len__(self) -> int:
        return len(self._store)
