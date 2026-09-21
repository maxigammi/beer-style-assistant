"""
Скачивает стили пива BJCP 2021 (английская версия) с bjcp.org и
сохраняет их в data/styles/<NN>-<slug>.md — по одному файлу на категорию.

Использование:  python scripts/fetch_bjcp.py

Источник: BJCP Beer Style Guidelines 2021, https://www.bjcp.org/
Тексты принадлежат BJCP; используются в некоммерческих учебных целях
с указанием авторства.
"""

import html
import re
import sys
import time
from pathlib import Path

import requests

BASE = "https://www.bjcp.org"
INDEX = BASE + "/style/2021/beer/?pg={page}"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "styles"
HEADERS = {"User-Agent": "beer-style-assistant/0.1 (educational RAG project)"}
DELAY = 1.0  # секунд между запросами, не грузим чужой сайт

STYLE_URL_RE = re.compile(r'href="(https://www\.bjcp\.org/style/2021/(\d+)/(\d+[A-Z])/[^"/]+/)"')
CATEGORY_URL_RE = re.compile(r'href="https://www\.bjcp\.org/style/2021/(\d+)/([a-z0-9-]+)/"')

# Секции, которые попадают в базу знаний, в порядке вывода
SECTIONS = [
    "Overall Impression", "Appearance", "Aroma", "Flavor", "Mouthfeel",
    "Comments", "History", "Characteristic Ingredients", "Style Comparison",
]
CODE_LINE_RE = re.compile(r"^\d+[A-Z]\.$")
STATS = ["IBU", "SRM", "OG", "FG", "ABV"]


def get(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    time.sleep(DELAY)
    return resp.text


def to_lines(page: str) -> list[str]:
    page = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    page = re.sub(r"<[^>]+>", "\n", page)
    page = html.unescape(page)
    return [ln.strip() for ln in page.splitlines() if ln.strip()]


def collect_style_urls() -> tuple[list[tuple[str, str, str]], dict[str, str]]:
    """
    ([(код, url, номер категории)], {номер категории: название}) по страницам индекса.
    Ключ — URL, а не код: у части кодов несколько страниц (21B — Specialty IPA и 7 её вариантов,
    27A — 9 исторических стилей), и по коду сохранялась бы только последняя.
    """
    found: dict[str, tuple[str, str, str]] = {}
    cats: dict[str, str] = {}
    for page in range(1, 12):
        text = get(INDEX.format(page=page))
        matches = STYLE_URL_RE.findall(text)
        if not matches:
            break
        for url, cat, code in matches:
            found.setdefault(url, (code, url, cat))
        for cat, slug in CATEGORY_URL_RE.findall(text):
            cats[cat] = f"{cat}. {slug.replace('-', ' ').title().replace('Ipa', 'IPA')}"
    ordered = sorted(found.values(), key=lambda t: (int(re.match(r"\d+", t[0]).group()), t[0], t[1]))
    return ordered, cats


def parse_style(url: str) -> dict:
    lines = to_lines(get(url))
    # Заголовок: строка-код "1A." → название → (необязательный вводный абзац) → "Overall Impression"
    start = next(i for i, ln in enumerate(lines) if ln == "Overall Impression")
    code_idx = max(i for i in range(start) if CODE_LINE_RE.match(lines[i]))
    code = lines[code_idx].rstrip(".")
    title = lines[code_idx + 1]
    intro = " ".join(lines[code_idx + 2:start])

    # Границы секций: от заголовка секции до следующего известного заголовка
    known = set(SECTIONS) | {"Vital Statistics", "Commercial Examples", "Past Revision",
                             "Style Attributes", "BJCP Stats"}
    sections: dict[str, str] = {}
    i = start
    while i < len(lines):
        head = lines[i]
        if head in known:
            j = i + 1
            while j < len(lines) and lines[j] not in known:
                j += 1
            sections[head] = " ".join(lines[i + 1:j])
            i = j
        else:
            i += 1

    stats = {}
    vs = lines[lines.index("Vital Statistics") + 1:] if "Vital Statistics" in lines else []
    for k in STATS:
        if k in vs:
            stats[k] = vs[vs.index(k) + 1]

    return {"code": code, "title": title, "intro": intro, "sections": sections, "stats": stats}


def render(style: dict, category: str) -> str:
    s = style
    out = [f"## {s['code']}. {s['title']}", "", f"Category: {category}", ""]
    if s["intro"]:
        out += [f"Summary: {s['intro']}", ""]
    if s["stats"]:
        out.append("Vital Statistics: " + "; ".join(f"{k} {v}" for k, v in s["stats"].items()))
        out.append("")
    for name in SECTIONS:
        if s["sections"].get(name):
            out += [f"### {name}", s["sections"][name], ""]
    examples = s["sections"].get("Commercial Examples")
    if examples:
        out += ["### Commercial Examples", examples, ""]
    return "\n".join(out)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    urls, cat_names = collect_style_urls()
    print(f"Найдено страниц стилей: {len(urls)}")

    by_cat: dict[str, list[str]] = {}
    for code, url, cat in urls:
        print(f"  {code} ...", end=" ", flush=True)
        style = parse_style(url)
        by_cat.setdefault(cat, []).append(render(style, cat_names.get(cat, cat)))
        print(style["title"])

    for cat, chunks in by_cat.items():
        path = OUT_DIR / f"{int(cat):02d}-{re.sub(r'[^a-z0-9]+', '-', cat_names.get(cat, cat).lower().split('. ', 1)[-1]).strip('-')}.md"
        header = (f"# BJCP 2021 Beer Style Guidelines — Category {cat_names.get(cat, cat)}\n\n"
                  "Source: BJCP (https://www.bjcp.org/), used for non-commercial educational purposes.\n\n")
        path.write_text(header + "\n".join(chunks), encoding="utf-8")
        print(f"Записан {path.name}: {len(chunks)} стилей")
    return 0


if __name__ == "__main__":
    sys.exit(main())
