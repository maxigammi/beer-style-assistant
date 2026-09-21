"""Загрузка базы знаний: data/styles/*.md → чанки (один стиль = один чанк)."""

import logging
import re
from pathlib import Path
from typing import List

from rag.vectorstore import Chunk

logger = logging.getLogger(__name__)

# Заголовок стиля: "## 1A. American Light Lager"
STYLE_HEADING = re.compile(r"^## (?P<code>\d+[A-Z]?)\.\s*(?P<title>.+)$")


def split_styles(text: str) -> List[Chunk]:
    """Режет markdown-файл по заголовкам второго уровня (## ...)."""
    chunks: List[Chunk] = []
    current: List[str] = []

    def flush() -> None:
        if not current:
            return
        m = STYLE_HEADING.match(current[0])
        body = "\n".join(current).strip()
        if m and body:
            chunks.append(Chunk(text=body, source=f"BJCP {m['code']} — {m['title'].strip()}", code=m["code"]))

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            current = [line]
        elif current:
            current.append(line)
    flush()
    return chunks


def load_styles(directory: Path) -> List[Chunk]:
    chunks: List[Chunk] = []
    for path in sorted(directory.glob("*.md")):
        found = split_styles(path.read_text(encoding="utf-8"))
        logger.info(f"{path.name}: {len(found)} стилей")
        chunks.extend(found)
    return chunks
