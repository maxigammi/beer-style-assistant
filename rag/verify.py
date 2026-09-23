"""Проверка ответа модели без LLM: числа и английские термины в ответе должны быть в контексте."""

import re
from typing import List

# Проверяем числа с десятичной частью и двузначные и длиннее (IBU, ABV, OG/FG, годы);
# однозначные не трогаем: это нумерация списков и «3 стиля».
_NUMBER = re.compile(r"\d+[.,]\d+|\d{2,}")
_STYLE_CODE = re.compile(r"\b\d{1,2}[A-Z]\d?\b")

# Латинское слово с большой буквы и хотя бы одной строчной: «Citra», «El», «Munich».
# ВСЕ-ЗАГЛАВНЫЕ аббревиатуры (IPA, IBU, BJCP) не подходят под паттерн и не проверяются —
# они не бывают выдуманными названиями сортов/ингредиентов.
_TERM = re.compile(r"\b[A-Z][a-z]+\b")


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


def unsupported_terms(answer: str, allowed_text: str) -> List[str]:
    """
    Английские слова с большой буквы (потенциальные названия сортов хмеля, солода и т.п.),
    которых нет в allowed_text (контекст + исходный вопрос пользователя — то, что пользователь
    сам написал, не считается выдумкой). Найдено эмпирически: промпт не удерживает модель от
    уверенных выдуманных названий («Citra», «Mosaic», «El Dorado» — вымышлены для несуществующего
    в BJCP ответа), хотя от выдуманных чисел удерживает; нужна отдельная механическая проверка.
    """
    known = {t.lower() for t in _TERM.findall(allowed_text)}
    seen: List[str] = []
    for t in _TERM.findall(answer):
        if t.lower() not in known and t not in seen:
            seen.append(t)
    return seen
