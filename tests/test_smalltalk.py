"""Тесты распознавания служебных фраз. Запуск: python tests/test_smalltalk.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from smalltalk import GREETING, HELP, RESET, detect  # noqa: E402


def test_help_phrases():
    for text in ["что ты умеешь?", "Что ты умеешь", "Что умеешь?", "чем ты можешь помочь?",
                 "как тобой пользоваться", "Кто ты?", "Расскажи о себе", "помощь", "/help".lstrip("/"),
                 "Что ты можешь?!", "что это за бот"]:
        assert detect(text) == HELP, text


def test_greetings():
    for text in ["привет", "Привет!", "Здравствуйте", "добрый день", "привет бот", "hello", "Салют 🍺"]:
        assert detect(text) == GREETING, text


def test_reset_phrases():
    for text in ["начнём заново", "Начнем заново!", "забудь всё", "Забудь всё", "очисти историю", "сбрось диалог"]:
        assert detect(text) == RESET, text


def test_real_questions_are_not_intercepted():
    for text in ["Какая горечь у American IPA?",
                 "Чем отличается портер от стаута?",
                 "Что такое стаут?",
                 "Что ты знаешь про лагеры и чем они отличаются от эля в плане брожения",  # длинный
                 "привет, расскажи про вайцен, какая у него горечь и цвет?",  # приветствие + вопрос
                 "начни с самого светлого стиля в списке лагеров, пожалуйста",
                 ""]:
        assert detect(text) is None, text


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} тестов пройдено")
