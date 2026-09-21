"""Векторное хранилище на FAISS (косинусная близость через inner product)."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import faiss
import numpy as np

from config import FAISS_INDEX_PATH, METADATA_PATH

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    text: str
    source: str  # например "BJCP 1A — American Light Lager"
    code: str    # код стиля, например "1A"


@dataclass
class SearchHit:
    chunk: Chunk
    score: float  # косинусная близость, больше — лучше


class FAISSVectorStore:
    def __init__(self, index_path: Path = FAISS_INDEX_PATH, metadata_path: Path = METADATA_PATH):
        self.index_path = Path(index_path)
        self.metadata_path = Path(metadata_path)
        self.index: Optional[faiss.Index] = None
        self.chunks: List[Chunk] = []
        self.embed_model: str = ""

    def build(self, chunks: List[Chunk], vectors: np.ndarray, embed_model: str) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("Число чанков и векторов должно совпадать")
        self.index = faiss.IndexFlatIP(vectors.shape[1])
        self.index.add(vectors)
        self.chunks = list(chunks)
        self.embed_model = embed_model
        logger.info(f"Индекс построен: {len(chunks)} чанков, размерность {vectors.shape[1]}")

    def search(self, query_vector: np.ndarray, k: int) -> List[SearchHit]:
        if self.index is None or self.index.ntotal == 0:
            return []
        scores, ids = self.index.search(query_vector, min(k, self.index.ntotal))
        return [SearchHit(self.chunks[i], float(s)) for s, i in zip(scores[0], ids[0]) if i >= 0]

    def save(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(self.index_path))
        meta = {
            "embed_model": self.embed_model,
            "chunks": [vars(c) for c in self.chunks],
        }
        self.metadata_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Индекс сохранён: {self.index_path}")

    def load(self) -> bool:
        if not (self.index_path.exists() and self.metadata_path.exists()):
            return False
        try:
            self.index = faiss.read_index(str(self.index_path))
            meta = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            self.chunks = [Chunk(**c) for c in meta["chunks"]]
            self.embed_model = meta["embed_model"]
        except Exception as e:
            logger.error(f"Не удалось загрузить индекс: {e}")
            self.index, self.chunks = None, []
            return False
        logger.info(f"Индекс загружен: {len(self.chunks)} чанков, модель {self.embed_model}")
        return True

    def stats(self) -> dict:
        return {
            "chunks": len(self.chunks),
            "dimension": self.index.d if self.index else 0,
            "embed_model": self.embed_model,
        }
