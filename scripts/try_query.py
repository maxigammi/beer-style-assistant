"""
Проверка поиска и ответа без Telegram:
    python scripts/try_query.py "Какая горечь у American IPA?"
    python scripts/try_query.py --retrieval-only "..."   # только поиск, без GigaChat
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")

if __name__ == "__main__":
    args = sys.argv[1:]
    retrieval_only = "--retrieval-only" in args
    query = " ".join(a for a in args if not a.startswith("--"))
    if not query:
        raise SystemExit(__doc__)
    config.validate(need_telegram=False)

    from rag.pipeline import RAGPipeline

    pipe = RAGPipeline()
    if not pipe.is_loaded:
        raise SystemExit("Индекс не загружен: сначала python scripts/ingest.py")
    if retrieval_only:
        plan = pipe.understand(query)
        if plan.intent == "compare":
            for g in pipe.build_groups(plan):
                print(f"группа «{g.label}» ({g.keyword}): {[c.source for c in g.chunks]}")
        else:
            # top_k расширен, чтобы увидеть score и ниже порога
            for h in pipe.vectorstore.search(pipe.embedder.embed_query(plan.query), 8):
                mark = "✓" if h.score >= config.MIN_SCORE else "✗"
                print(f"{mark} {h.score:.3f}  {h.chunk.source}")
    else:
        ans = pipe.answer(query)
        print(ans.text)
        print(f"\n[mode={ans.mode} rag={ans.used_rag} top_score={ans.top_score:.3f} tokens={ans.tokens} "
              f"warnings={ans.warnings} sources={ans.sources}]")
