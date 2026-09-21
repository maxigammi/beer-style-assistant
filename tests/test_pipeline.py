"""
Тесты пайплайна на подставных LLM и поиске (без API): разбор вопроса, сравнение,
проверка чисел, отказы. Запуск: python tests/test_pipeline.py
"""

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from llm.gigachat import ChatResult, GigaChatError  # noqa: E402
from rag.loader import load_styles  # noqa: E402
from rag.pipeline import RAGPipeline, parse_plan  # noqa: E402
from rag.vectorstore import SearchHit  # noqa: E402

CHUNKS = load_styles(Path(__file__).resolve().parent.parent / "data" / "styles")
BY_CODE = {c.code: c for c in CHUNKS}

COMPARE_JSON = json.dumps({"intent": "compare", "groups": [
    {"label": "портеры", "keyword": "porter"}, {"label": "стауты", "keyword": "stout"}]})
LOOKUP_JSON = json.dumps({"intent": "lookup", "query": "American IPA bitterness"})


class StubLLM:
    """Отвечает по очереди заготовленными ответами; записывает все вызовы."""
    model = "stub"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat_ex(self, messages, temperature=0.3, max_tokens=1000):
        self.calls.append({"messages": messages, "temperature": temperature})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(reply, 100)


class StubEmbedder:
    model = "stub-embed"

    def __init__(self):
        self.queries = []

    def embed_query(self, text):
        self.queries.append(text)
        return np.zeros((1, 4), dtype=np.float32)


class StubStore:
    """Настоящие чанки; поиск возвращает то, что заказал тест (по умолчанию — ничего)."""
    embed_model = "stub-embed"
    chunks = CHUNKS

    def __init__(self, hits=()):
        self.hits = list(hits)
        self.searches = 0

    def load(self):
        return True

    def search(self, vec, k):
        self.searches += 1
        return self.hits[:k]

    def stats(self):
        return {"chunks": len(CHUNKS), "dimension": 4, "embed_model": "stub-embed"}


def hit(code, score=0.6):
    return SearchHit(BY_CODE[code], score)


def make(llm, hits=()):
    return RAGPipeline(embedder=StubEmbedder(), vectorstore=StubStore(hits), llm=llm)


# ---------- разбор ответа модели ----------

def test_parse_plan_variants():
    assert parse_plan(COMPARE_JSON, "q").groups == [("портеры", "porter"), ("стауты", "stout")]
    wrapped = f"Вот JSON:\n```json\n{COMPARE_JSON}\n```"
    assert parse_plan(wrapped, "q").intent == "compare"          # мусор вокруг JSON не мешает
    lookup = parse_plan(LOOKUP_JSON, "Какая горечь у ИПА?")
    assert lookup.intent == "lookup" and "Какая горечь у ИПА?" in lookup.query and "bitterness" in lookup.query
    for bad in ("не json", "{сломанный", "[]", '{"intent": "lookup"}',
                json.dumps({"intent": "compare", "groups": [{"label": "а", "keyword": "porter"}]}),  # одна сторона
                json.dumps({"intent": "compare", "groups": [{"keyword": "stout"}, {"keyword": "Stout"}]})):  # дубли
        assert parse_plan(bad, "q") is None, bad


def test_understand_falls_back_to_lookup():
    for reply in ("не json", GigaChatError("недоступна")):
        pipe = make(StubLLM(reply))
        plan = pipe.understand("Какая горечь у IPA?")
        assert plan.intent == "lookup" and plan.query == "Какая горечь у IPA?"


def test_understand_gets_recent_history_only():
    llm = StubLLM(LOOKUP_JSON)
    history = [{"role": "user", "content": f"q{i}"} for i in range(10)]
    make(llm).understand("а какой у него IBU?", history)
    sent = llm.calls[0]["messages"]
    assert sent[0]["role"] == "system" and sent[-1]["content"] == "а какой у него IBU?"
    assert len(sent) == 1 + 4 + 1  # система + 4 последних + вопрос
    assert llm.calls[0]["temperature"] == 0.0


# ---------- сравнение ----------

def test_compare_default_has_only_facts_and_one_llm_call():
    assert config.COMPARE_TRAITS is False  # по умолчанию выключено
    llm = StubLLM(COMPARE_JSON)
    ans = make(llm).answer("Чем отличается портер от стаута?")
    assert len(llm.calls) == 1  # только разбор вопроса: текст модель не пишет
    assert "• Портеры: 18–50, в среднем 31.3" in ans.text and "Характер" not in ans.text
    assert ans.tokens == 100 and ans.warnings == []


def with_traits(fn):
    """Тест для необязательной характеристики групп: включает COMPARE_TRAITS на время теста."""
    def wrapper():
        config.COMPARE_TRAITS = True
        try:
            fn()
        finally:
            config.COMPARE_TRAITS = False
    wrapper.__name__ = fn.__name__
    return wrapper


@with_traits
def test_compare_uses_code_numbers_and_llm_only_for_traits():
    traits = "Портеры: тёмные и солодовые.\nСтауты: чёрные, с жареным вкусом."
    llm = StubLLM(COMPARE_JSON, traits)
    ans = make(llm).answer("Чем отличается портер от стаута?")
    assert ans.mode == "compare" and ans.used_rag
    assert "• Портеры: 18–50, в среднем 31.3" in ans.text          # число посчитал код
    assert "Характер по описаниям BJCP" in ans.text and "чёрные, с жареным вкусом" in ans.text
    assert len(llm.calls) == 2                                      # разбор + характеристика, без проверок-повторов
    prompt = llm.calls[1]["messages"][1]["content"]
    assert "Baltic Porter" in prompt and "Imperial Stout" in prompt and "Overall Impression" not in prompt
    assert "IBU" not in prompt  # модель чисел вообще не получает: считать ей нечего
    assert len(ans.sources) == 11 and ans.warnings == []
    assert llm.calls[1]["temperature"] == config.ANSWER_TEMPERATURE


@with_traits
def test_compare_drops_traits_that_contain_numbers():
    llm = StubLLM(COMPARE_JSON, "Портеры: около 65 IBU.\nСтауты: чёрные.")
    ans = make(llm).answer("Чем отличается портер от стаута?")
    assert "Характер по описаниям" not in ans.text and "65" not in ans.text
    assert any("были числа" in w for w in ans.warnings)
    assert "• Стауты: 20–90" in ans.text  # таблица на месте


@with_traits
def test_compare_survives_traits_failure():
    ans = make(StubLLM(COMPARE_JSON, GigaChatError("упала"))).answer("Чем отличается портер от стаута?")
    assert "• Портеры: 18–50" in ans.text and "Характер" not in ans.text
    assert ans.warnings == ["характеристика групп не получена"]


def test_compare_with_unknown_side_refuses_honestly():
    llm = StubLLM(json.dumps({"intent": "compare", "groups": [
        {"label": "портеры", "keyword": "porter"}, {"label": "ристретто", "keyword": "ristretto"}]}))
    ans = make(llm, hits=[hit("1A", 0.1)]).answer("Чем отличается портер от ристретто?")  # поиск ниже порога
    assert "«ристретто»" in ans.text and "Сравнивать нечего" in ans.text
    assert len(llm.calls) == 1  # на характеристику токены не тратим


def test_compare_side_found_by_meaning_when_title_does_not_match():
    llm = StubLLM(json.dumps({"intent": "compare", "groups": [
        {"label": "портеры", "keyword": "porter"}, {"label": "вайцены", "keyword": "wheaty"}]}))
    pipe = make(llm, hits=[hit("10A", 0.7), hit("10B", 0.65), hit("1A", 0.1)])
    ans = pipe.answer("Портер или вайцен?")
    assert "Вайцены — 2 стиля" in ans.text and "подобрано по смыслу" in ans.text  # 1A ниже порога отброшен
    assert pipe.embedder.queries[-1] == "wheaty"  # «голый» ключ: суффикс «beer style» размывал разницу с мусором


def test_weak_meaning_match_is_treated_as_missing():
    """Score 0.30–0.44 — зона «мусорных» ключей (ristretto→Mixed-Style Beer 0.5 с суффиксом, 0.12 без): не берём."""
    llm = StubLLM(json.dumps({"intent": "compare", "groups": [
        {"label": "портеры", "keyword": "porter"}, {"label": "ристретто", "keyword": "ristretto"}]}))
    ans = make(llm, hits=[hit("34B", 0.44), hit("34C", 0.40)]).answer("Портер или ристретто?")
    assert "«ристретто»" in ans.text and "Сравнивать нечего" in ans.text and len(llm.calls) == 1


@with_traits
def test_broad_group_uses_all_styles_but_limits_traits_prompt():
    from rag.compare import assign_groups
    n_ales = len(assign_groups(["ale"], CHUNKS)[0])
    assert n_ales > config.TRAITS_MAX_STYLES
    llm = StubLLM(json.dumps({"intent": "compare", "groups": [
        {"label": "эль", "keyword": "ale"}, {"label": "стауты", "keyword": "stout"}]}), "x: y")
    ans = make(llm).answer("Эль или стауты?")
    assert f"Эль — {n_ales} " in ans.text  # числа — по всем стилям с «ale», без обрезки
    prompt = llm.calls[1]["messages"][1]["content"]
    ale_block = prompt.split("Группа «Стауты»")[0]
    assert ale_block.count("\n[") == config.TRAITS_MAX_STYLES  # в запрос к модели — не больше лимита


def test_single_style_sides_are_named_by_style():
    llm = StubLLM(json.dumps({"intent": "compare", "groups": [
        {"label": "американские ипа", "keyword": "American IPA"}, {"label": "хайзи ипа", "keyword": "Hazy IPA"}]}))
    ans = make(llm).answer("American IPA или Hazy IPA?")
    assert "American IPA — 1 стиль: American IPA (21A)" in ans.text
    assert "• Hazy IPA: 25–60, в среднем 42.5" in ans.text and "Американские" not in ans.text


# ---------- справка: проверка чисел ----------

def lookup_pipeline(*answers, hits=None):
    llm = StubLLM(LOOKUP_JSON, *answers)
    return make(llm, hits=hits or [hit("21A", 0.6), hit("22A", 0.55)]), llm


def test_lookup_clean_answer_no_retry():
    pipe, llm = lookup_pipeline("Горечь American IPA — 40–70 IBU.")
    ans = pipe.answer("Какая горечь у American IPA?")
    assert ans.mode == "lookup" and "40–70" in ans.text and ans.warnings == []
    assert len(llm.calls) == 2  # разбор + ответ
    assert llm.calls[1]["temperature"] == config.ANSWER_TEMPERATURE
    assert ans.tokens == 200 and ans.sources[0].startswith("BJCP 21A")


def test_lookup_invented_number_triggers_one_retry():
    pipe, llm = lookup_pipeline("Горечь около 65 IBU.", "Горечь American IPA — 40–70 IBU.")
    ans = pipe.answer("Какая горечь у American IPA?")
    assert len(llm.calls) == 3 and "40–70" in ans.text and ans.warnings == []
    fix_request = llm.calls[2]["messages"][-1]["content"]
    assert "65" in fix_request and "только числа из контекста" in fix_request
    assert ans.tokens == 300  # суммируются все три вызова


def test_lookup_still_invented_after_retry_is_delivered_with_warning():
    pipe, llm = lookup_pipeline("Около 65 IBU.", "Всё-таки около 65 IBU.")
    ans = pipe.answer("Какая горечь у American IPA?")
    assert len(llm.calls) == 3 and "65" in ans.text
    assert ans.warnings == ["числа не из контекста: 65"]  # админ увидит, пользователь получит ответ


def test_lookup_without_relevant_styles_does_not_call_llm_for_answer():
    llm = StubLLM(LOOKUP_JSON)
    ans = make(llm, hits=[hit("1A", 0.05)]).answer("Как приготовить борщ?")
    assert ans.text == config.NO_CONTEXT_REPLY and not ans.used_rag
    assert len(llm.calls) == 1


def test_llm_failure_gives_friendly_message():
    llm = StubLLM(LOOKUP_JSON, GigaChatError("упала"))
    ans = make(llm, hits=[hit("21A")]).answer("Какая горечь у American IPA?")
    assert "Не получилось получить ответ" in ans.text and not ans.used_rag


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} тестов пройдено")
