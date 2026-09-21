"""Индексация базы знаний из командной строки:  python scripts/ingest.py"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

logging.basicConfig(level=logging.INFO, format=config.LOG_FORMAT)

if __name__ == "__main__":
    config.validate(need_telegram=False, need_llm=False)
    from rag.pipeline import RAGPipeline

    count = RAGPipeline().ingest()
    print(f"Проиндексировано стилей: {count}")
