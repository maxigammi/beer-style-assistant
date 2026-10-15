"""
Быстрая проверка ПОИСКА отдельно от бота — без RAGAS и без судьи-LLM, по совету преподавателя:
для каждого вопроса заранее выписано, что обязано найтись. Два вида требований (questions.py):
- "must": [коды] — все перечисленные коды стилей обязаны быть в выдаче (для вопроса про один
  конкретный стиль, где ответ однозначен).
- "must_any_titles": [ключевые слова] — по каждому слову хотя бы ОДИН стиль из этой группы (та же
  группировка по названию, что использует сам бот для сравнения — rag.compare.assign_groups)
  обязан быть в выдаче (для вопроса про общую черту, где правильных стилей много и годится любой).

Ответ модели не запрашивается. Единственный вызов LLM — GigaChat в understand() (перевод вопроса
на английский для поиска), эмбеддинги — OpenAI. Секунды, а не минуты.

    python retrieval_check.py                # top_k как у бота (config.TOP_K_RESULTS)
    python retrieval_check.py --top-k 3
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from rag.compare import assign_groups  # noqa: E402
from rag.pipeline import RAGPipeline  # noqa: E402
from questions import QUESTIONS  # noqa: E402

RANK_DEPTH = 15  # сколько смотреть без порога MIN_SCORE, для диагностики промахов


def check_one(pipe: RAGPipeline, q: dict, top_k: int) -> bool:
    must = q.get("must") or []
    any_titles = q.get("must_any_titles") or []
    if not must and not any_titles:
        print(f"{q['id']}  (нет обязательных стилей — пропуск) {q['question']}")
        return True

    plan = pipe.understand(q["question"])
    hits = pipe.retrieve(plan.query, top_k)  # ровно то, что реально попадёт в контекст (после MIN_SCORE)
    found = {h.chunk.code for h in hits}

    raw = pipe.vectorstore.search(pipe.embedder.embed_query(plan.query), RANK_DEPTH)
    rank = {h.chunk.code: (i + 1, h.score) for i, h in enumerate(raw)}

    def show(code: str) -> None:
        r = rank.get(code)
        mark = "✓" if code in found else "✗"
        if r is None:
            where = f"нет даже в топ-{RANK_DEPTH}"
        else:
            place, score = r
            where = f"ранг {place}, score {score:.3f}" + ("" if code in found else f" (ниже MIN_SCORE={config.MIN_SCORE} или за пределами top_k={top_k})")
        print(f"   {mark} {code}: {where}")

    ok = all(code in found for code in must)
    groups_ok = []
    for title in any_titles:
        (group,) = assign_groups([title], pipe.vectorstore.chunks)
        codes = {c.code for c in group}
        groups_ok.append(bool(codes & found))
    ok = ok and all(groups_ok)

    print(f"{q['id']}  {'OK' if ok else 'ПРОВАЛ'}   {q['question']}")
    print(f"   запрос к поиску: {plan.query!r}")
    for code in must:
        show(code)
    for title, group_ok in zip(any_titles, groups_ok):
        (group,) = assign_groups([title], pipe.vectorstore.chunks)
        codes = {c.code for c in group}
        mark = "✓" if group_ok else "✗"
        detail = f"нашлись {sorted(found & codes)}" if group_ok else "ни один не найден"
        print(f"   {mark} семейство «{title}» ({len(codes)} стилей): {detail}")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--top-k", type=int, default=config.TOP_K_RESULTS)
    args = parser.parse_args()

    config.validate(need_telegram=False)
    pipe = RAGPipeline()
    if not pipe.is_loaded:
        raise SystemExit("Индекс не загружен: сначала python ingest.py")

    results = [check_one(pipe, q, args.top_k) for q in QUESTIONS]
    passed = sum(results)
    print(f"\n{passed}/{len(results)} пройдено (top_k={args.top_k})")
    if passed != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
