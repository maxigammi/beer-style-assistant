"""
RAG-пайплайн: понять вопрос → найти стили → ответ GigaChat.

Два пути:
- справка («какая горечь у IPA?»): семантический поиск, ответ модели по контексту, числа
  в ответе проверяются кодом;
- сравнение («чем отличается портер от стаута?»): группы стилей собираются по названиям,
  числа считает код, модель пишет только короткую характеристику групп (см. rag/compare.py).

Все методы синхронные; из async-кода вызывать через asyncio.to_thread.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import config
from config import (
    ANSWER_TEMPERATURE, BUSY_REPLY, EMBED_MODEL, FAMILY_LIST_LIMIT, GROUP_FALLBACK_MIN_SCORE, GROUP_FALLBACK_TOP_K, LIST_STYLES_UP_TO,
    MAX_CONTEXT_CHARS, MIN_SCORE, NO_CONTEXT_REPLY, RAG_PROMPT_TEMPLATE, STYLES_DIR, SYSTEM_PROMPT,
    TOP_K_RESULTS, TRAITS_MAX_STYLES, TRAITS_PROMPT, UNDERSTAND_PROMPT,
)
from llm.gigachat import GigaChatBusy, GigaChatClient, GigaChatError
from rag.compare import Group, assign_groups, overall_impression, render_facts, render_family, title_of
from rag.embedder import OpenAIEmbedder
from rag.loader import load_styles
from rag.vectorstore import FAISSVectorStore, SearchHit
from rag.verify import unsupported_numbers

logger = logging.getLogger(__name__)

MAX_GROUPS = 4


@dataclass
class Answer:
    text: str
    sources: List[str] = field(default_factory=list)
    top_score: float = 0.0
    used_rag: bool = False
    tokens: int = 0  # токены GigaChat на весь запрос; основа для будущих лимитов
    mode: str = "lookup"  # lookup | compare
    warnings: List[str] = field(default_factory=list)  # для отладки: что не удалось подтвердить


@dataclass
class Plan:
    intent: str                       # lookup | compare | family
    query: str = ""                   # lookup: строка для эмбеддинга
    groups: List[Tuple[str, str]] = field(default_factory=list)  # compare: (label, keyword)
    keyword: str = ""                 # family: слово из названий стилей
    tokens: int = 0


def parse_plan(raw: str, user_query: str) -> Optional[Plan]:
    """Достаёт JSON из ответа модели; None, если он невалиден (тогда работаем как со справкой)."""
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("intent") == "compare":
        groups = [(str(g.get("label", "")).strip(), str(g.get("keyword", "")).strip())
                  for g in data.get("groups", []) if isinstance(g, dict)]
        groups = [(label or kw, kw) for label, kw in groups if kw][:MAX_GROUPS]
        keywords = {kw.lower() for _, kw in groups}
        if len(groups) >= 2 and len(keywords) == len(groups):
            return Plan("compare", groups=groups)
        return None  # одна сторона или дубли — это не сравнение
    if data.get("intent") == "family":
        keyword = str(data.get("keyword", "")).strip()
        return Plan("family", keyword=keyword) if keyword else None
    english = str(data.get("query", "")).strip()
    if english:
        # Исходный текст оставляем: в нём могут быть названия марок и стилей как есть
        return Plan("lookup", query=f"{user_query}\n{english}")
    return None


class RAGPipeline:
    def __init__(self, embedder=None, vectorstore=None, llm=None):
        self.embedder = embedder or OpenAIEmbedder()
        self.vectorstore = vectorstore or FAISSVectorStore()
        self.llm = llm or GigaChatClient()
        self.is_loaded = self.vectorstore.load()
        if self.is_loaded and self.vectorstore.embed_model != self.embedder.model:
            # Векторы из разных моделей несравнимы — поиск вернул бы мусор
            logger.error(
                f"Индекс построен моделью {self.vectorstore.embed_model}, а сейчас "
                f"{self.embedder.model}. Выполните /ingest для переиндексации."
            )
            self.is_loaded = False

    # ---------- индексация ----------

    def ingest(self) -> int:
        """Индексирует data/styles/*.md. Возвращает число проиндексированных стилей."""
        chunks = load_styles(STYLES_DIR)
        if not chunks:
            raise FileNotFoundError(f"В {STYLES_DIR} нет .md файлов со стилями")
        vectors = self.embedder.embed_texts([c.text for c in chunks])
        self.vectorstore.build(chunks, vectors, self.embedder.model)
        self.vectorstore.save()
        self.is_loaded = True
        return len(chunks)

    # ---------- понимание вопроса ----------

    def understand(self, query: str, history: Optional[List[Dict[str, str]]] = None) -> Plan:
        """Тип вопроса и поисковый запрос/группы от LLM; при любом сбое — обычная справка."""
        messages = [{"role": "system", "content": UNDERSTAND_PROMPT}]
        messages.extend((history or [])[-4:])
        messages.append({"role": "user", "content": query})
        try:
            result = self.llm.chat_ex(messages, temperature=0.0, max_tokens=200)
        except GigaChatBusy:
            raise  # ключ занят надолго: не тратить ещё столько же на ответ, сразу сказать об этом
        except GigaChatError as e:
            logger.warning(f"Разбор вопроса не удался, ищем по исходному: {e}")
            return Plan("lookup", query=query)
        plan = parse_plan(result.text, query)
        if plan is None:
            logger.warning(f"Не разобрал ответ модели, ищем по исходному вопросу: {result.text!r}")
            plan = Plan("lookup", query=query)
        plan.tokens = result.tokens
        logger.info(f"Вопрос {query!r} -> {plan.intent}: {plan.groups or plan.keyword or plan.query!r}")
        return plan

    # ---------- поиск ----------

    def retrieve(self, query: str, top_k: int = TOP_K_RESULTS) -> List[SearchHit]:
        """Поиск стилей; отбрасывает результаты ниже порога MIN_SCORE."""
        hits = self.vectorstore.search(self.embedder.embed_query(query), top_k)
        for h in hits:
            logger.info(f"  {h.score:.3f}  {h.chunk.source}")
        return [h for h in hits if h.score >= MIN_SCORE]

    def build_groups(self, plan: Plan) -> List[Group]:
        """
        Группа — все стили, в названии которых есть ключевое слово (числа считаются по всем).
        Если по названию не нашлось (алиас вроде «weizen»), берём лучшие по смыслу, но только
        уверенные: слабые совпадения хуже честного «такого стиля в базе нет».
        """
        by_title = assign_groups([kw for _, kw in plan.groups], self.vectorstore.chunks)
        groups: List[Group] = []
        for (label, keyword), chunks in zip(plan.groups, by_title):
            by_meaning = False
            if not chunks:
                hits = self.vectorstore.search(self.embedder.embed_query(keyword), GROUP_FALLBACK_TOP_K)
                chunks = [h.chunk for h in hits if h.score >= GROUP_FALLBACK_MIN_SCORE]
                by_meaning = bool(chunks)
            groups.append(Group(label=label, keyword=keyword, chunks=chunks, by_meaning=by_meaning))
        return groups

    # ---------- ответы ----------

    def answer(self, query: str, history: Optional[List[Dict[str, str]]] = None) -> Answer:
        if not self.is_loaded:
            return Answer("База знаний не загружена. Администратор должен выполнить /ingest.")
        try:
            plan = self.understand(query, history)
        except GigaChatBusy as e:
            logger.warning(str(e))
            return Answer(BUSY_REPLY)
        if plan.intent == "compare":
            return self._answer_compare(plan)
        if plan.intent == "family":
            answer = self._answer_family(plan)
            if answer is not None:
                return answer
            # Меньше двух стилей с таким словом в названии — это обычная справка
            plan = Plan("lookup", query=f"{query}\n{plan.keyword} beer style", tokens=plan.tokens)
        return self._answer_lookup(query, plan, history)

    def _answer_family(self, plan: Plan) -> Optional[Answer]:
        """Полный список стилей семейства (по названию), без участия модели. None — не семейство."""
        (chunks,) = assign_groups([plan.keyword], self.vectorstore.chunks)
        if len(chunks) < 2:
            return None
        logger.info(f"Семейство {plan.keyword!r}: {len(chunks)} стилей")
        return Answer(render_family(plan.keyword, chunks, FAMILY_LIST_LIMIT),
                      sources=[c.source for c in chunks], used_rag=True, tokens=plan.tokens, mode="family")

    def _answer_lookup(self, query: str, plan: Plan, history) -> Answer:
        hits = self.retrieve(plan.query)
        if not hits:
            return Answer(NO_CONTEXT_REPLY, tokens=plan.tokens)

        context = self._build_context(hits)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history or [])
        messages.append({"role": "user", "content": RAG_PROMPT_TEMPLATE.format(context=context, query=query)})

        tokens = plan.tokens
        try:
            result = self.llm.chat_ex(messages, temperature=ANSWER_TEMPERATURE)
            tokens += result.tokens
            text, warnings = result.text, []

            bad = unsupported_numbers(text, context)
            if bad:  # одна попытка исправить: числа из головы — самая вредная выдумка
                logger.warning(f"Числа не из контекста: {bad}; просим исправить")
                fix = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": (
                        f"В ответе есть числа, которых нет в контексте: {', '.join(bad)}. "
                        "Перепиши ответ, используя только числа из контекста. Если нужного числа "
                        "в контексте нет, скажи, что в базе его нет.")},
                ]
                retry = self.llm.chat_ex(fix, temperature=ANSWER_TEMPERATURE)
                tokens += retry.tokens
                text = retry.text
                bad = unsupported_numbers(text, context)
                if bad:
                    logger.warning(f"После исправления остались числа не из контекста: {bad}")
                    warnings.append(f"числа не из контекста: {', '.join(bad)}")
        except GigaChatBusy as e:
            logger.warning(str(e))
            return Answer(BUSY_REPLY, tokens=plan.tokens)
        except GigaChatError as e:
            logger.error(f"Ошибка GigaChat: {e}")
            return Answer("Не получилось получить ответ от языковой модели. Попробуй ещё раз чуть позже.",
                          tokens=plan.tokens)

        return Answer(text=text, sources=[h.chunk.source for h in hits], top_score=hits[0].score,
                      used_rag=True, tokens=tokens, warnings=warnings)

    def _answer_compare(self, plan: Plan) -> Answer:
        groups = self.build_groups(plan)
        missing = [g.label for g in groups if not g.chunks]
        if missing:
            names = ", ".join(f"«{m}»" for m in missing)
            return Answer(f"В базе знаний BJCP нет стилей для: {names}. Сравнивать нечего.",
                          tokens=plan.tokens, mode="compare")

        facts = render_facts(groups, LIST_STYLES_UP_TO)  # числа посчитал код, по всем стилям групп
        tokens, warnings = plan.tokens, []
        traits = ""
        if config.COMPARE_TRAITS:  # опционально: текст пишет модель, поэтому по умолчанию выключено
            try:
                traits_result = self.llm.chat_ex(self._traits_messages(groups), temperature=ANSWER_TEMPERATURE,
                                                 max_tokens=400)
                tokens += traits_result.tokens
                traits = traits_result.text.strip()
            except GigaChatError as e:
                logger.warning(f"Характеристика групп не получена, отдаём только числа: {e}")
                warnings.append("характеристика групп не получена")
            # В характеристике чисел быть не должно (они в таблице): цифры = признак выдумки
            if traits and re.search(r"\d", re.sub(r"\b\d{1,2}[A-Z]\b", "", traits)):
                logger.warning(f"В характеристике групп есть числа, отбрасываем: {traits!r}")
                warnings.append("характеристика групп отброшена: в ней были числа")
                traits = ""

        text = facts + (f"\n\nХарактер по описаниям BJCP:\n{traits}" if traits else "")
        sources = [c.source for g in groups for c in g.chunks]
        return Answer(text=text, sources=sources, used_rag=True, tokens=tokens, mode="compare",
                      warnings=warnings)

    @staticmethod
    def _traits_messages(groups: List[Group]) -> List[Dict[str, str]]:
        blocks = []
        for g in groups:
            styles = "\n".join(f"[{c.code} {title_of(c)}] {overall_impression(c)}"
                               for c in g.chunks[:TRAITS_MAX_STYLES])
            blocks.append(f"Группа «{g.display}»:\n{styles}")
        return [{"role": "system", "content": TRAITS_PROMPT},
                {"role": "user", "content": "\n\n".join(blocks)}]

    @staticmethod
    def _build_context(hits: List[SearchHit]) -> str:
        parts, total = [], 0
        for h in hits:
            block = f"[{h.chunk.source}]\n{h.chunk.text}\n"
            if total + len(block) > MAX_CONTEXT_CHARS:
                break
            parts.append(block)
            total += len(block)
        return "\n".join(parts)

    def stats(self) -> dict:
        return {
            **self.vectorstore.stats(),
            "is_loaded": self.is_loaded,
            "chat_model": self.llm.model,
            "configured_embed_model": EMBED_MODEL,
        }
