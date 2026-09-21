"""
Beer Style Assistant — Telegram-бот со справочником пивных стилей BJCP (RAG).

Запуск:  python bot.py
"""

import asyncio
import functools
import logging
import re
import time
from datetime import datetime
from typing import Dict, List

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import config
from access import AccessStore

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format=config.LOG_FORMAT,
    handlers=[logging.FileHandler("bot.log", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

dp = Dispatcher()
pipeline = None  # создаётся в main() после проверки конфигурации
bot = None
bot_username = ""
access = None  # AccessStore, создаётся в main()
history: Dict[int, List[Dict[str, str]]] = {}

DENIED_TEXT = ("Этот бот работает по приглашениям. Если у тебя есть код, отправь: /start КОД\n"
               "Или открой пригласительную ссылку.")
# Без доступа разрешены только /start и /myid — чтобы можно было погасить инвайт и узнать свой ID
OPEN_COMMANDS = re.compile(r"^/(start|myid)(@\w+)?(\s|$)")
MAX_BAD_CODES = 5        # неверных кодов ...
BAD_CODES_WINDOW = 3600  # ... за этот период (сек) — и пользователя перестаём слушать
bad_codes: Dict[int, List[float]] = {}


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def has_access(user_id: int) -> bool:
    return is_admin(user_id) or access.is_member(user_id)


class AccessMiddleware(BaseMiddleware):
    """Пропускает к хендлерам только админов и участников; остальным — отказ без вызова LLM."""

    async def __call__(self, handler, event: Message, data):
        user = event.from_user
        if user is None:
            return None
        if has_access(user.id) or OPEN_COMMANDS.match(event.text or ""):
            return await handler(event, data)
        logger.info(f"Отказ в доступе: id={user.id} username={user.username}")
        await event.answer(DENIED_TEXT)
        return None


dp.message.outer_middleware(AccessMiddleware())


def too_many_bad_codes(user_id: int) -> bool:
    now = time.time()
    attempts = [t for t in bad_codes.get(user_id, []) if now - t < BAD_CODES_WINDOW]
    bad_codes[user_id] = attempts
    return len(attempts) >= MAX_BAD_CODES


def fmt_date(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


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
async def cmd_start(message: Message, command: CommandObject):
    user = message.from_user
    if not has_access(user.id):
        code = (command.args or "").strip()
        if not code:
            await message.answer(DENIED_TEXT)
            return
        if too_many_bad_codes(user.id):
            logger.warning(f"Слишком много неверных кодов от {user.id}")
            return  # молчим: перебор кодов ничего не даёт
        result = access.redeem(code, user.id, user.username)
        if result == "invalid":
            bad_codes.setdefault(user.id, []).append(time.time())
            logger.info(f"Неверный инвайт от {user.id}")
            await message.answer("Код недействителен: он неверный, просрочен или уже использован.")
            return
        logger.info(f"Новый пользователь {user.id} ({user.username}) по инвайту")
        for admin_id in config.ADMIN_IDS:
            try:
                await bot.send_message(admin_id, f"Новый пользователь: id={user.id}, "
                                                 f"username=@{user.username or '—'}")
            except Exception:
                logger.warning(f"Не удалось уведомить админа {admin_id}")
        await message.answer("Доступ открыт!")
    await message.answer(
        "Привет! Я Beer Style Assistant — справочник по пивным стилям BJCP 2021.\n\n"
        "Спроси, например:\n"
        "• Чем отличается British Bitter от Best Bitter?\n"
        "• Какая горечь у American IPA?\n"
        "• Какое пиво пить к стейку?\n\n"
        "Команды: /help, /ask <вопрос>, /stats, /clear"
    )


ADMIN_HELP = (
    "\n\nАдминистратор:\n"
    "/invite [чел=1] [дней=7] — создать инвайт\n"
    "/invites — действующие инвайты, /revoke КОД — отозвать\n"
    "/users — список пользователей, /kick ID — убрать\n"
    "/ingest — переиндексация базы"
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
        "База знаний пока на английском, ответы я перевожу на лету."
        + ADMIN_HELP * is_admin(message.from_user.id)
    )


@dp.message(Command("ask"))
async def cmd_ask(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Напиши вопрос после команды, например: /ask Какая горечь у American IPA?")
        return
    await ask(message, command.args)


# ========== УПРАВЛЕНИЕ ДОСТУПОМ (только админы) ==========

def admin_only(handler):
    """Декоратор: команда доступна только ADMIN_IDS."""
    @functools.wraps(handler)  # aiogram определяет нужные аргументы по сигнатуре оригинала
    async def wrapper(message: Message, *args, **kwargs):
        if not is_admin(message.from_user.id):
            await message.answer("Эта команда доступна только администратору.")
            return
        return await handler(message, *args, **kwargs)
    return wrapper


@dp.message(Command("invite"))
@admin_only
async def cmd_invite(message: Message, command: CommandObject):
    """/invite [использований=1] [дней=7]"""
    args = (command.args or "").split()
    try:
        uses = int(args[0]) if len(args) > 0 else 1
        days = int(args[1]) if len(args) > 1 else 7
    except ValueError:
        uses = days = 0
    if not (1 <= uses <= 100 and 1 <= days <= 90):
        await message.answer("Формат: /invite [сколько человек: 1–100] [срок в днях: 1–90]\n"
                             "Например: /invite 1 7")
        return
    code = access.create_invite(message.from_user.id, uses, days)
    await message.answer(
        f"Инвайт создан: на {uses} чел., действует {days} дн.\n\n"
        f"Ссылка: https://t.me/{bot_username}?start={code}\n"
        f"Код: {code}"
    )


@dp.message(Command("invites"))
@admin_only
async def cmd_invites(message: Message):
    items = access.active_invites()
    if not items:
        await message.answer("Действующих инвайтов нет. Создать: /invite")
        return
    lines = [f"{i.code} — использовано {i.uses}/{i.max_uses}, до {fmt_date(i.expires_at)}" for i in items]
    await message.answer("Действующие инвайты:\n" + "\n".join(lines) + "\n\nОтозвать: /revoke КОД")


@dp.message(Command("revoke"))
@admin_only
async def cmd_revoke(message: Message, command: CommandObject):
    code = (command.args or "").strip()
    if not code:
        await message.answer("Формат: /revoke КОД")
        return
    ok = access.revoke(code)
    await message.answer("Инвайт отозван." if ok else "Такой действующий инвайт не найден.")


@dp.message(Command("users"))
@admin_only
async def cmd_users(message: Message):
    users = access.users()
    if not users:
        await message.answer("Приглашённых пользователей пока нет.")
        return
    lines = [f"{u.user_id} @{u.username or '—'} — с {fmt_date(u.joined_at)}" for u in users]
    await message.answer(f"Пользователи ({len(users)}):\n" + "\n".join(lines) + "\n\nУбрать: /kick ID")


@dp.message(Command("kick"))
@admin_only
async def cmd_kick(message: Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg.lstrip("-").isdigit():
        await message.answer("Формат: /kick ID (список: /users)")
        return
    user_id = int(arg)
    removed = access.remove_user(user_id)
    history.pop(user_id, None)
    await message.answer("Доступ отозван." if removed else "Такого пользователя нет в списке.")


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
    global pipeline, bot, bot_username, access
    config.validate()
    from rag.pipeline import RAGPipeline  # импорт после проверки конфигурации

    pipeline = RAGPipeline()
    if not pipeline.is_loaded:
        logger.warning("База знаний не загружена — выполните /ingest или python scripts/ingest.py")
    access = AccessStore(config.ACCESS_DB_PATH)
    bot = Bot(token=config.TELEGRAM_TOKEN)
    bot_username = (await bot.me()).username
    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
