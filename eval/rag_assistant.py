"""
Ассистент для проверки качества RAG: тот же боевой пайплайн, что и в Telegram-боте
(понимание вопроса -> поиск в FAISS -> GigaChat -> проверка чисел и терминов), но с выводом
найденных фрагментов. Боевой код не меняется: поиск перехватывается обёрткой на время вызова.

    python rag_assistant.py                      # интерактивно
    python rag_assistant.py "Какая горечь у Witbier?"
    python rag_assistant.py --top-k 3 "..."      # временно другой top_k (config.py не трогаем)
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from rag.pipeline import RAGPipeline  # noqa: E402

_pipe: Optional[RAGPipeline] = None


def get_pipeline() -> RAGPipeline:
    global _pipe
    if _pipe is None:
        config.validate(need_telegram=False)
        _pipe = RAGPipeline()
        if not _pipe.is_loaded:
            raise SystemExit("Индекс не загружен: сначала python ingest.py")
    return _pipe


def ask_assistant(query: str, top_k: Optional[int] = None) -> dict:
    """
    Возвращает {"answer", "context": [{"document", "metadata", "score"}], "mode", "warnings", ...}.
    context — только те стили, что реально попали в запрос к модели (после порога и лимита символов).
    """
    pipe = get_pipeline()
    k = top_k or config.TOP_K_RESULTS
    seen = {"hits": [], "raw_hits": []}

    def retrieve(q, top_k=k):
        hits = RAGPipeline.retrieve(pipe, q, top_k)
        seen["raw_hits"] = hits
        return hits

    def build_context(hits):
        context = RAGPipeline._build_context(hits)
        seen["hits"] = [h for h in hits if f"[{h.chunk.source}]\n{h.chunk.text}\n" in context]
        return context

    pipe.retrieve, pipe._build_context = retrieve, build_context
    try:
        ans = pipe.answer(query)
    finally:
        del pipe.retrieve, pipe._build_context

    return {
        "answer": ans.text,
        "context": [{"document": h.chunk.text, "metadata": {"source": h.chunk.source, "code": h.chunk.code},
                     "score": h.score} for h in seen["hits"]],
        "mode": ans.mode,
        "used_rag": ans.used_rag,
        "warnings": ans.warnings,
        "tokens": ans.tokens,
        "top_k": k,
    }


def show(result: dict) -> None:
    print("\n" + "=" * 70)
    print("Ответ:")
    print(result["answer"])
    print("=" * 70)
    print("Признаки качества:")
    print(f"  режим: {result['mode']}, top_k={result['top_k']}, токенов GigaChat: {result['tokens']}")
    if result["context"]:
        print(f"  найдено стилей в контексте: {len(result['context'])}")
        for i, c in enumerate(result["context"], 1):
            print(f"    {i}. {c['score']:.3f}  {c['metadata']['source']}")
    else:
        print("  в контекст ничего не попало (поиск не нашёл стилей выше порога либо режим без поиска)")
    if result["warnings"]:
        print("  ПРЕДУПРЕЖДЕНИЯ проверки выдумки:")
        for w in result["warnings"]:
            print(f"    - {w}")
    else:
        print("  проверка выдумки: числа и английские термины ответа найдены в контексте")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="*")
    parser.add_argument("--top-k", type=int, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    print(f"Beer Style Assistant (оценка качества). top_k={args.top_k or config.TOP_K_RESULTS}, "
          f"модель: {config.GIGACHAT_MODEL}")
    if args.question:
        show(ask_assistant(" ".join(args.question), args.top_k))
        return
    print("Введите вопрос (exit — выход)")
    while True:
        query = input("\nВопрос: ").strip()
        if query.lower() in ("exit", "quit", "выход"):
            break
        if query:
            show(ask_assistant(query, args.top_k))


if __name__ == "__main__":
    main()
