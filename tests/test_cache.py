"""Тесты кеша ответов (без API). Запуск: python tests/test_cache.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.cache import AnswerCache, normalize  # noqa: E402


def test_normalize_ignores_case_punctuation_and_yo():
    assert normalize("Какая горечь у IPA?") == normalize("какая горечь у ipa")
    assert normalize("Ещё раз,  пожалуйста!!") == normalize("еще раз пожалуйста")
    assert normalize("  двойные   пробелы  ") == "двойные пробелы"


def test_get_set_roundtrip_and_miss():
    cache = AnswerCache()
    assert cache.get("Какая горечь у IPA?") is None
    cache.set("Какая горечь у IPA?", "ответ")
    assert cache.get("какая горечь у ipa?") == "ответ"  # регистр и пунктуация не важны
    assert cache.hits == 1 and cache.misses == 1


def test_different_questions_do_not_collide():
    cache = AnswerCache()
    cache.set("Какая горечь у стаута?", "про стаут")
    assert cache.get("Какая горечь у портера?") is None


def test_clear_resets_store_and_counters():
    cache = AnswerCache()
    cache.set("вопрос", "ответ")
    cache.get("вопрос")
    cache.get("другой")
    cache.clear()
    assert len(cache) == 0 and cache.hits == 0 and cache.misses == 0
    assert cache.get("вопрос") is None


def test_max_size_evicts_oldest_first():
    cache = AnswerCache(max_size=2)
    cache.set("первый", 1)
    cache.set("второй", 2)
    cache.set("третий", 3)
    assert len(cache) == 2
    assert cache.get("первый") is None  # вытеснен первым
    assert cache.get("второй") == 2 and cache.get("третий") == 3


def test_recent_get_protects_from_eviction():
    cache = AnswerCache(max_size=2)
    cache.set("первый", 1)
    cache.set("второй", 2)
    cache.get("первый")  # обновляет порядок — теперь самый свежий
    cache.set("третий", 3)
    assert cache.get("первый") == 1  # уцелел
    assert cache.get("второй") is None  # вытеснен вместо первого


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} тестов пройдено")
