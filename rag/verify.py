"""Проверка ответа модели без LLM: числа в ответе должны быть в контексте."""

import re
from typing import List

# Проверяем числа с десятичной частью и двузначные и длиннее (IBU, ABV, OG/FG, годы);
# однозначные не трогаем: это нумерация списков и «3 стиля».
_NUMBER = re.compile(r"\d+[.,]\d+|\d{2,}")
_STYLE_CODE = re.compile(r"\b\d{1,2}[A-Z]\d?\b")


def _numbers(text: str) -> List[str]:
    text = _STYLE_CODE.sub(" ", text)  # «21A» — код стиля, а не число
    return [n.replace(",", ".") for n in _NUMBER.findall(text)]


def unsupported_numbers(answer: str, context: str) -> List[str]:
    """Числа из ответа, которых нет среди чисел контекста (в порядке появления, без повторов)."""
    known = set(_numbers(context))
    seen: List[str] = []
    for n in _numbers(answer):
        if n not in known and n not in seen:
            seen.append(n)
    return seen
