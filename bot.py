"""
Beer Style Assistant — Telegram-бот со справочником пивных стилей BJCP (RAG).

Запуск:  python bot.py
"""

import asyncio
import logging
from typing import Dict, List

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import config

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format=config.LOG_FORMAT,
    handlers=[logging.FileHandler("bot.log", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

dp = Dispatcher()
pipeline = None  # создаётся в main() после проверки конфигурации
bot = None
history: Dict[int, List[Dict[str, str]]] = {}


def format_answer(answer) -> str:
    text = answer.text
    if answer.sources:
        text += "\n\nИсточники: " + "; ".join(answer.sources)
    return text


async def send_long(message: Message, text: str, limit: int = 4000) -> None:
    for i in range(0, len(text), limit):
        await message.answer(text[i:i + limit])


async def ask(message: Message, query: str) -> None:
    user_id = message.from_user.id
    logger.info(f"Вопрос от {user_id}: {query}")
    status = await message.answer("Ищу в справочнике…")
    try:
        user_history = history.setdefault(user_id, [])
        answer = await asyncio.to_thread(pipeline.answer, query, user_history)
        if answer.used_rag:  # в историю пишем только содержательные обмены
            user_history += [{"role": "user", "content": query},
                             {"role": "assistant", "content": answer.text}]
            del user_history[:-config.MAX_HISTORY_PAIRS * 2]
        await status.delete()
        await send_long(message, format_answer(answer))
    except Exception:
        logger.exception("Ошибка при обработке вопроса")
        await status.delete()
        await message.answer("Что-то пошло не так. Подробности в логе, попробуй ещё раз.")


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я Beer Style Assistant — справочник по пивным стилям BJCP 2021.\n\n"
        "Спроси, например:\n"
        "• Чем отличается British Bitter от Best Bitter?\n"
        "• Какая горечь у American IPA?\n"
        "• Какое пиво пить к стейку?\n\n"
        "Команды: /help, /ask <вопрос>, /stats, /clear"
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "Просто напиши вопрос про пивной стиль — я найду его в базе BJCP и отвечу по-русски "
        "с указанием источника. Понимаю уточняющие вопросы («а какой у него IBU?»).\n\n"
        "/ask <вопрос> — явный запрос к базе\n"
        "/stats — состояние базы знаний\n"
        "/clear — забыть историю диалога\n"
        "/myid — показать твой Telegram ID\n"
        "/ingest — переиндексация базы (только админ)\n\n"
        "База знаний пока на английском, ответы я перевожу на лету."
    )


@dp.message(Command("ask"))
async def cmd_ask(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Напиши вопрос после команды, например: /ask Какая горечь у American IPA?")
        return
    await ask(message, command.args)


@dp.message(Command("ingest"))
async def cmd_ingest(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("Эта команда доступна только администратору. Твой ID: /myid")
        return
    status = await message.answer("Индексирую справочник…")
    try:
        count = await asyncio.to_thread(pipeline.ingest)
        stats = pipeline.stats()
        await status.edit_text(f"Готово. Стилей проиндексировано: {count}, "
                               f"размерность: {stats['dimension']}.")
    except Exception as e:
        logger.exception("Ошибка индексации")
        await status.edit_text(f"Ошибка индексации: {e}")


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    s = pipeline.stats()
    await message.answer(
        f"База знаний: {'загружена' if s['is_loaded'] else 'не загружена (/ingest)'}\n"
        f"Стилей: {s['chunks']}, размерность векторов: {s['dimension']}\n"
        f"Эмбеддинги: {s['embed_model'] or s['configured_embed_model']}\n"
        f"Генерация: {s['chat_model']}\n"
        f"Сообщений в твоей истории: {len(history.get(message.from_user.id, []))}"
    )


@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    history.pop(message.from_user.id, None)
    await message.answer("История диалога очищена.")


@dp.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"Твой Telegram ID: {message.from_user.id}")


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    await ask(message, message.text)


@dp.message()
async def handle_other(message: Message):
    await message.answer("Я понимаю только текстовые вопросы про пивные стили.")


async def main():
    global pipeline, bot
    config.validate()
    from rag.pipeline import RAGPipeline  # импорт после проверки конфигурации

    pipeline = RAGPipeline()
    if not pipeline.is_loaded:
        logger.warning("База знаний не загружена — выполните /ingest или python scripts/ingest.py")
    if not config.ADMIN_IDS:
        logger.warning("ADMIN_IDS пуст: /ingest в боте недоступен, используйте scripts/ingest.py")
    bot = Bot(token=config.TELEGRAM_TOKEN)
    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
