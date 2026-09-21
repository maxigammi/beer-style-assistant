"""
Сравнение групп стилей — детерминированная часть, без LLM.

Числа (IBU, SRM, ABV) берутся из «Vital Statistics» стилей и агрегируются кодом:
модель арифметику не делает и цифр не придумывает. Группа — это все стили, в названии
которых есть ключевое слово («stout» → все стауты), а не «топ-N по похожести».
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from rag.vectorstore import Chunk

METRICS = (("IBU", "Горечь, IBU", ""), ("SRM", "Цвет, SRM", ""), ("ABV", "Крепость, ABV", "%"))

_STATS_LINE = re.compile(r"^Vital Statistics: (.+)$", re.M)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_OVERALL = re.compile(r"### Overall Impression\n(.*?)(?=\n###|\Z)", re.S)


def title_of(chunk: Chunk) -> str:
    """«BJCP 20B — American Stout» → «American Stout»."""
    return chunk.source.split(" — ", 1)[-1]


def parse_stats(text: str) -> Dict[str, Tuple[float, float]]:
    """{'IBU': (25.0, 50.0), 'ABV': (4.8, 6.5), ...} из строки Vital Statistics."""
    m = _STATS_LINE.search(text)
    if not m:
        return {}
    stats: Dict[str, Tuple[float, float]] = {}
    for part in m.group(1).split(";"):
        key, _, value = part.strip().partition(" ")
        numbers = [float(x) for x in _NUMBER.findall(value)]
        if key in ("IBU", "SRM", "ABV") and numbers:
            stats[key] = (numbers[0], numbers[-1])
    return stats


def overall_impression(chunk: Chunk) -> str:
    m = _OVERALL.search(chunk.text)
    return m.group(1).strip() if m else ""


def _tokens(keyword: str) -> List[str]:
    return re.findall(r"[\w'-]+", keyword.lower())


def matches(keyword: str, title: str) -> bool:
    """Все слова ключа встречаются в названии как отдельные слова."""
    tokens = _tokens(keyword)
    return bool(tokens) and all(re.search(rf"\b{re.escape(t)}\b", title, re.I) for t in tokens)


def _keyword_variants(keyword: str) -> List[str]:
    """Сам ключ и его вариант без множественного числа («stouts» → «stout»)."""
    variants = [keyword]
    stripped = re.sub(r"(es|s)$", "", keyword.strip(), flags=re.I)
    if stripped and stripped.lower() != keyword.strip().lower():
        variants.append(stripped)
    return variants


def assign_groups(keywords: Sequence[str], chunks: Sequence[Chunk]) -> List[List[Chunk]]:
    """
    Раскладывает стили по группам. Стиль, подходящий нескольким группам, остаётся в самой
    конкретной (больше слов в ключе): «IPA» и «Double IPA» не должны делить Double IPA.
    """
    groups: List[List[Chunk]] = [[] for _ in keywords]
    for chunk in chunks:
        best: Optional[int] = None
        for i, keyword in enumerate(keywords):
            if any(matches(v, title_of(chunk)) for v in _keyword_variants(keyword)):
                if best is None or len(_tokens(keyword)) > len(_tokens(keywords[best])):
                    best = i
        if best is not None:
            groups[best].append(chunk)
    return groups


def midpoint_mean(chunks: Sequence[Chunk], key: str) -> Optional[Dict[str, float]]:
    """Диапазон группы и среднее середин диапазонов её стилей; None, если данных нет."""
    ranges = [parse_stats(c.text)[key] for c in chunks if key in parse_stats(c.text)]
    if not ranges:
        return None
    return {
        "min": min(lo for lo, _ in ranges),
        "max": max(hi for _, hi in ranges),
        "mean": sum((lo + hi) / 2 for lo, hi in ranges) / len(ranges),
        "n": len(ranges),
    }


def _num(x: float) -> str:
    return f"{round(x, 1):g}"


def _plural_styles(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} стиль"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} стиля"
    return f"{n} стилей"


@dataclass
class Group:
    label: str            # как назвал пользователь: «стауты»
    keyword: str          # по чему искали: «stout»
    chunks: List[Chunk] = field(default_factory=list)
    by_meaning: bool = False  # True: в названиях не нашлось, стили подобраны поиском по смыслу

    @property
    def display(self) -> str:
        """Одностильная группа называется по стилю («American IPA»), остальные — как сказал пользователь."""
        name = title_of(self.chunks[0]) if len(self.chunks) == 1 else self.label
        return name[:1].upper() + name[1:]


def render_facts(groups: Sequence[Group], list_up_to: int = 8) -> str:
    """Компактная сводка чисел: без описаний стилей и без выводов. Числа — по ВСЕМ стилям групп."""
    lines = ["Сравнение по данным BJCP 2021", ""]
    for g in groups:
        head = f"{g.display} — {_plural_styles(len(g.chunks))}"
        if len(g.chunks) <= list_up_to:
            head += ": " + ", ".join(f"{title_of(c)} ({c.code})" for c in g.chunks)
        if g.by_meaning:
            head += f" [в названиях «{g.keyword}» не найдено, подобрано по смыслу]"
        lines.append(head)
    for key, title, unit in METRICS:
        lines += ["", title]
        for g in groups:
            agg = midpoint_mean(g.chunks, key)
            if agg is None:
                lines.append(f"• {g.display}: нет данных")
            else:
                lines.append(f"• {g.display}: {_num(agg['min'])}–{_num(agg['max'])}{unit}, "
                             f"в среднем {_num(agg['mean'])}{unit}")
    lines += ["", "«В среднем» — среднее из середин диапазонов стилей группы. "
                  "Группа — все стили BJCP, в названии которых есть указанное слово."]
    return "\n".join(lines)
