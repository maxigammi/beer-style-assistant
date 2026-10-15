"""
Оценка качества RAG через RAGAS: тот же боевой пайплайн бота, вопросы и эталоны из questions.py.
Судья (LLM) и эмбеддинги RAGAS — OpenAI; отвечает и ищет сам бот (GigaChat + FAISS).

    python evaluate_rag.py                    # top_k как в боте (config.TOP_K_RESULTS)
    python evaluate_rag.py --top-k 3          # эксперимент с другим top_k, config.py не трогаем
    python evaluate_rag.py --top-k 3 --runs 3 # три прогона подряд, в конце среднее
"""

import argparse
import json
import math
import re
import statistics
import time
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")

from langchain_openai import ChatOpenAI, OpenAIEmbeddings  # noqa: E402
from ragas import EvaluationDataset, SingleTurnSample, evaluate  # noqa: E402
from ragas.embeddings import LangchainEmbeddingsWrapper  # noqa: E402
from ragas.llms import LangchainLLMWrapper  # noqa: E402
from ragas.metrics import AnswerRelevancy, ContextPrecision, ContextRecall, Faithfulness  # noqa: E402
from ragas.run_config import RunConfig  # noqa: E402

from questions import QUESTIONS  # noqa: E402
from rag_assistant import ask_assistant, config  # noqa: E402  (rag_assistant добавляет корень проекта в sys.path)

METRICS = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
TITLES = {
    "faithfulness": "Faithfulness (верность контексту)",
    "answer_relevancy": "Answer Relevancy (релевантность ответа)",
    "context_precision": "Context Precision (точность контекста)",
    "context_recall": "Context Recall (полнота контекста, доп.)",
}
RESULTS_DIR = Path(__file__).parent / "results"
REFUSAL = re.compile(r"нет (информации|данных|сведений)|не (указан|содерж|приведен|описан)|не нашел|не нашёл", re.I)


def refused_correctly(answer: str) -> bool:
    """Для вопроса без ответа в базе: в ответе есть отказ и нет ни одной цифры (выдуманных чисел)."""
    return bool(REFUSAL.search(answer)) and not re.search(r"\d", answer)


def collect(top_k: int) -> tuple[list[SingleTurnSample], list[dict]]:
    samples, raw = [], []
    print(f"Ответы ассистента (top_k={top_k})...")
    for i, q in enumerate(QUESTIONS, 1):
        print(f"  {i}/{len(QUESTIONS)} {q['question']}")
        r = ask_assistant(q["question"], top_k)
        contexts = [c["document"] for c in r["context"]]
        samples.append(SingleTurnSample(user_input=q["question"], response=r["answer"],
                                        retrieved_contexts=contexts, reference=q["reference"]))
        raw.append({"id": q["id"], "kind": q["kind"], "question": q["question"], "answer": r["answer"],
                    "mode": r["mode"], "expects_refusal": q.get("expects_refusal", False), "sources": [c["metadata"]["source"] for c in r["context"]],
                    "scores": [round(c["score"], 3) for c in r["context"]], "warnings": r["warnings"]})
    return samples, raw


def judge(samples: list[SingleTurnSample], model: str):
    llm = LangchainLLMWrapper(ChatOpenAI(model=model, temperature=0, api_key=config.OPENAI_API_KEY,
                                         base_url=config.OPENAI_BASE_URL))
    emb = LangchainEmbeddingsWrapper(OpenAIEmbeddings(model=config.EMBED_MODEL, api_key=config.OPENAI_API_KEY,
                                                      base_url=config.OPENAI_BASE_URL))
    metrics = [Faithfulness(llm=llm), AnswerRelevancy(llm=llm, embeddings=emb, strictness=1),
               ContextPrecision(llm=llm), ContextRecall(llm=llm)]
    result = evaluate(EvaluationDataset(samples=samples), metrics=metrics, llm=llm, embeddings=emb,
                      run_config=RunConfig(timeout=180, max_workers=4), show_progress=False)
    return result.to_pandas()


def fmt(v) -> str:
    return "  n/a " if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.4f}"


def mean(values):
    good = [v for v in values if v is not None and not math.isnan(v)]
    return statistics.fmean(good) if good else float("nan")


def one_run(top_k: int, model: str) -> dict:
    samples, raw = collect(top_k)
    print(f"\nОценка метрик судьёй {model}...")
    df = judge(samples, model)
    for row, r in zip(df.to_dict("records"), raw):
        r["metrics"] = {m: (None if math.isnan(row[m]) else float(row[m])) for m in METRICS}
    for it in raw:
        if it["expects_refusal"]:
            it["refused_correctly"] = refused_correctly(it["answer"])
    answerable = [it for it in raw if not it["expects_refusal"]]
    return {"top_k": top_k, "judge": model, "bot_model": config.GIGACHAT_MODEL, "items": raw,
            "means_all": {m: mean([it["metrics"][m] for it in raw]) for m in METRICS},
            "means": {m: mean([it["metrics"][m] for it in answerable]) for m in METRICS}}


def print_run(run: dict) -> None:
    print("\n" + "=" * 70)
    print(f"РЕЗУЛЬТАТЫ RAGAS   top_k={run['top_k']}   судья: {run['judge']}   бот: {run['bot_model']}")
    print("=" * 70)
    print(f"{'':<46} {'все 5':>8} {'без Q5':>8}   (Q5 — вопрос без ответа: метрики к нему неприменимы)")
    for m in METRICS:
        print(f"{TITLES[m]:<46} {fmt(run['means_all'][m]):>8} {fmt(run['means'][m]):>8}")
    print("\nПо вопросам (F=faithfulness, R=relevancy, P=precision, C=recall):")
    for it in run["items"]:
        mm = it["metrics"]
        print(f"  {it['id']} F={fmt(mm['faithfulness'])} R={fmt(mm['answer_relevancy'])} "
              f"P={fmt(mm['context_precision'])} C={fmt(mm['context_recall'])}  "
              f"стилей в контексте: {len(it['sources'])}  {it['kind']}")
        if it["expects_refusal"]:
            print(f"      проверка отказа: {'бот корректно отказал (нет цифр, есть отказ)' if it['refused_correctly'] else 'ОТКАЗА НЕТ или есть выдуманные цифры'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--top-k", type=int, default=config.TOP_K_RESULTS)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--judge", default="gpt-4o-mini")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(exist_ok=True)
    runs = []
    for n in range(1, args.runs + 1):
        if args.runs > 1:
            print(f"\n##### Прогон {n}/{args.runs} #####")
        run = one_run(args.top_k, args.judge)
        print_run(run)
        runs.append(run)
        path = RESULTS_DIR / f"run_top{args.top_k}_{datetime.now():%Y%m%d_%H%M%S}.json"
        path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nСохранено: {path.name}")
        time.sleep(1)

    if len(runs) > 1:
        print("\n" + "=" * 70)
        print(f"СРЕДНЕЕ ПО {len(runs)} ПРОГОНАМ (top_k={args.top_k})")
        print("=" * 70)
        for m in METRICS:
            vals = [r["means"][m] for r in runs]
            print(f"{TITLES[m]:<46} {fmt(mean(vals))}   (разброс {fmt(min(vals))}..{fmt(max(vals))})")
        print("(среднее по вопросам с ответом, без Q5)")
        refusals = [r["items"][-1].get("refused_correctly") for r in runs]
        print(f"Q5, корректный отказ: {sum(1 for x in refusals if x)} из {len(runs)} прогонов")


if __name__ == "__main__":
    main()
