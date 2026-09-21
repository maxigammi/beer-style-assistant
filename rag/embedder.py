"""Эмбеддинги через OpenAI API (векторы нормализуются под косинусную близость)."""

import logging
from typing import List

import numpy as np
from openai import OpenAI

from config import EMBED_BATCH_SIZE, EMBED_MODEL, OPENAI_API_KEY, OPENAI_BASE_URL

logger = logging.getLogger(__name__)


class OpenAIEmbedder:
    def __init__(self, model: str = EMBED_MODEL):
        self.model = model
        self.client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

    def embed_texts(self, texts: List[str]) -> np.ndarray:
        """Батчами превращает тексты в матрицу float32 (n, dim), строки нормализованы."""
        vectors: List[List[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[start:start + EMBED_BATCH_SIZE]
            response = self.client.embeddings.create(model=self.model, input=batch)
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
            logger.info(f"Эмбеддинги: {min(start + EMBED_BATCH_SIZE, len(texts))}/{len(texts)}")
        matrix = np.array(vectors, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.clip(norms, 1e-12, None)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_texts([text])
