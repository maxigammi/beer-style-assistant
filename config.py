"""
Конфигурация Beer Style Assistant.

Все секреты берутся из .env (см. .env.example). Проверка обязательных
переменных вынесена в validate(), чтобы модули можно было импортировать
и без ключей (например, в тестах).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Берём корневые сертификаты из хранилища ОС (нужны для api.giga.chat / Минцифры),
# а не из certifi. Без пакета truststore — стандартное поведение requests.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

# ========== TELEGRAM ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
# ID администраторов через запятую; только им доступна команда /ingest
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

# ========== OPENAI (эмбеддинги) ==========
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# Пусто — официальный api.openai.com; иначе адрес прокси (например ProxyAPI).
# Пустое значение нельзя отдавать SDK как None: он тогда читает пустую переменную окружения.
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
EMBED_BATCH_SIZE = 64

# ========== GIGACHAT (генерация ответов) ==========
GIGACHAT_AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY", "")
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat-2")  # Lite
GIGACHAT_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_API_URL = "https://api.giga.chat/v1"
# Путь к PEM с корневым сертификатом Минцифры, если его нет в хранилище ОС
GIGACHAT_CA_BUNDLE = os.getenv("GIGACHAT_CA_BUNDLE") or None
REQUEST_TIMEOUT = 60

# ========== ХРАНИЛИЩЕ ==========
STYLES_DIR = BASE_DIR / "data" / "styles"
INDEX_DIR = BASE_DIR / "data" / "index"
FAISS_INDEX_PATH = INDEX_DIR / "index.faiss"
METADATA_PATH = INDEX_DIR / "metadata.json"

# ========== RAG ==========
TOP_K_RESULTS = 4
# Минимальная косинусная близость. Ниже — считаем, что в базе ответа нет.
# Значение подбирается экспериментально (scripts/try_query.py показывает score).
MIN_SCORE = float(os.getenv("MIN_SCORE", "0.25"))
MAX_CONTEXT_CHARS = 12000
MAX_HISTORY_PAIRS = 6

# ========== ПРОМПТЫ ==========
SYSTEM_PROMPT = """Ты — Beer Style Assistant, знаток пивных стилей по руководству BJCP 2021.
Отвечай на вопросы, опираясь ТОЛЬКО на контекст из базы знаний ниже.

Правила:
1. База знаний на английском — отвечай пользователю на русском.
2. Названия стилей давай в виде «American Light Lager (американский светлый лагер)»,
   коды BJCP (1A, 21A) сохраняй как есть.
3. Числа (IBU, SRM, ABV, OG, FG) передавай точно, без округлений и выдумок.
4. Если в контексте нет ответа — так и скажи, ничего не придумывай.
5. Коммерческие примеры называй только те, что есть в контексте.
6. Отвечай кратко и по делу, списки — когда они реально помогают.
"""

RAG_PROMPT_TEMPLATE = """Контекст из базы знаний:
{context}

Вопрос пользователя: {query}"""

# Перевод вопроса в поисковый запрос: база на английском, а эмбеддинги хуже
# сопоставляют русские термины («стаут») с английскими («Stout»)
REWRITE_PROMPT = """Преобразуй вопрос пользователя в короткий поисковый запрос на английском языке для поиска по руководству BJCP о пивных стилях.
Правила:
- Верни ТОЛЬКО запрос, без пояснений и кавычек.
- Названия стилей и термины давай по-английски (стаут -> stout, портер -> porter, горечь -> bitterness).
- Если вопрос — уточнение («а какой у него IBU?»), возьми стиль или тему из истории диалога.
- Если вопрос не про пиво, просто переведи его как есть."""

NO_CONTEXT_REPLY = (
    "В моей базе знаний нет информации по этому вопросу. "
    "Я отвечаю только о пивных стилях BJCP — попробуй переформулировать вопрос."
)

# ========== ЛОГИРОВАНИЕ ==========
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def validate(*, need_telegram: bool = True, need_llm: bool = True) -> None:
    """Проверяет обязательные переменные окружения."""
    required = {"OPENAI_API_KEY": OPENAI_API_KEY}
    if need_telegram:
        required["TELEGRAM_TOKEN"] = TELEGRAM_TOKEN
    if need_llm:
        required["GIGACHAT_AUTH_KEY"] = GIGACHAT_AUTH_KEY
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit(f"Не заданы переменные окружения: {', '.join(missing)} (см. .env.example)")
