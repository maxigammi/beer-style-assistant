"""
RAG-пайплайн: поиск стилей (OpenAI-эмбеддинги + FAISS) → ответ GigaChat.

Все методы синхронные; из async-кода вызывать через asyncio.to_thread.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config import (
    EMBED_MODEL, MAX_CONTEXT_CHARS, MIN_SCORE, NO_CONTEXT_REPLY, RAG_PROMPT_TEMPLATE,
    STYLES_DIR, SYSTEM_PROMPT, TOP_K_RESULTS,
)
from llm.gigachat import GigaChatClient, GigaChatError
from rag.embedder import OpenAIEmbedder
from rag.loader import load_styles
from rag.vectorstore import FAISSVectorStore, SearchHit

logger = logging.getLogger(__name__)


@dataclass
class Answer:
    text: str
    sources: List[str] = field(default_factory=list)
    top_score: float = 0.0
    used_rag: bool = False


class RAGPipeline:
    def __init__(self):
        self.embedder = OpenAIEmbedder()
        self.vectorstore = FAISSVectorStore()
        self.llm = GigaChatClient()
        self.is_loaded = self.vectorstore.load()
        if self.is_loaded and self.vectorstore.embed_model != self.embedder.model:
            # Векторы из разных моделей несравнимы — поиск вернул бы мусор
            logger.error(
                f"Индекс построен моделью {self.vectorstore.embed_model}, а сейчас "
                f"{self.embedder.model}. Выполните /ingest для переиндексации."
            )
            self.is_loaded = False

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

    def retrieve(self, query: str, top_k: int = TOP_K_RESULTS) -> List[SearchHit]:
        """Поиск стилей; отбрасывает результаты ниже порога MIN_SCORE."""
        hits = self.vectorstore.search(self.embedder.embed_query(query), top_k)
        for h in hits:
            logger.info(f"  {h.score:.3f}  {h.chunk.source}")
        return [h for h in hits if h.score >= MIN_SCORE]

    def answer(self, query: str, history: Optional[List[Dict[str, str]]] = None) -> Answer:
        if not self.is_loaded:
            return Answer("База знаний не загружена. Администратор должен выполнить /ingest.")

        # Для уточняющих вопросов («а какой у него IBU?») добавляем прошлый вопрос к поиску
        search_query = query
        if history:
            last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
            search_query = f"{last_user}\n{query}" if last_user else query

        hits = self.retrieve(search_query)
        if not hits:
            return Answer(NO_CONTEXT_REPLY, top_score=0.0)

        context = self._build_context(hits)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history or [])
        messages.append({"role": "user", "content": RAG_PROMPT_TEMPLATE.format(context=context, query=query)})

        try:
            text = self.llm.chat(messages)
        except GigaChatError as e:
            logger.error(f"Ошибка GigaChat: {e}")
            return Answer("Не получилось получить ответ от языковой модели. Попробуй ещё раз чуть позже.")

        return Answer(text=text, sources=[h.chunk.source for h in hits],
                      top_score=hits[0].score, used_rag=True)

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
