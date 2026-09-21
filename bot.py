"""
Beer Style Assistant — Telegram-бот со справочником пивных стилей BJCP (RAG).

Запуск:  python bot.py
"""

import asyncio
import functools
import logging
import logging.handlers
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BotCommand, BotCommandScopeChat, BotCommandScopeDefault, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

import config
import smalltalk
from access import AccessStore, User

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format=config.LOG_FORMAT,
    handlers=[
        # Лог рядом с кодом (а не в текущей папке) и с ротацией: на сервере файл не должен расти вечно
        logging.handlers.RotatingFileHandler(config.BASE_DIR / "bot.log", maxBytes=5_000_000,
                                             backupCount=3, encoding="utf-8"),
        logging.StreamHandler(),
    ],
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
CANCEL_COMMAND = re.compile(r"^/cancel(@\w+)?(\s|$)")
MAX_BAD_CODES = 5        # неверных кодов ...
BAD_CODES_WINDOW = 3600  # ... за этот период (сек) — и пользователя перестаём слушать
bad_codes: Dict[int, List[float]] = {}
MAX_NOTE_LEN = 60
MAX_USER_CARDS = 30      # больше карточек в один ответ не шлём


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def has_access(user_id: int) -> bool:
    return is_admin(user_id) or access.is_member(user_id)


# ========== ОЖИДАНИЕ АРГУМЕНТА ОТ АДМИНА ==========
# Команда с аргументом, выбранная из меню без него («/kick»), переводит админа в состояние
# ожидания: следующее его текстовое сообщение считается аргументом. Состояние живёт в памяти,
# сбрасывается любой другой командой, /cancel или по таймауту.

PENDING_TTL = 300  # секунд


@dataclass
class Pending:
    command: str
    created: float
    user_id: Optional[int] = None  # для /note: человек уже выбран (кнопкой или первым шагом)


pending: Dict[int, Pending] = {}


def get_pending(admin_id: int) -> Optional[Pending]:
    pend = pending.get(admin_id)
    if pend and time.time() - pend.created > PENDING_TTL:
        del pending[admin_id]
        return None
    return pend


class AccessMiddleware(BaseMiddleware):
    """Пропускает к хендлерам только админов и участников; остальным — отказ без вызова LLM."""

    async def __call__(self, handler, event: Message, data):
        user = event.from_user
        if user is None:
            return None
        text = event.text or ""
        if has_access(user.id) or OPEN_COMMANDS.match(text):
            # Любая новая команда админа отменяет незавершённый ввод аргумента
            # (/cancel исключение: она сама скажет, было ли что отменять)
            if is_admin(user.id) and text.startswith("/") and not CANCEL_COMMAND.match(text):
                pending.pop(user.id, None)
            return await handler(event, data)
        logger.info(f"Отказ в доступе: id={user.id}")
        await event.answer(DENIED_TEXT)
        return None


dp.message.outer_middleware(AccessMiddleware())


async def notify_admins(text: str) -> None:
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            logger.warning(f"Не удалось уведомить админа {admin_id}")


def too_many_bad_codes(user_id: int) -> bool:
    now = time.time()
    attempts = [t for t in bad_codes.get(user_id, []) if now - t < BAD_CODES_WINDOW]
    bad_codes[user_id] = attempts
    return len(attempts) >= MAX_BAD_CODES


def fmt_date(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


def show_debug_info(user_id: int) -> bool:
    """Источники и служебная строка нужны для отладки, обычным пользователям их не показываем."""
    return config.SHOW_SOURCES == "all" or (config.SHOW_SOURCES == "admin" and is_admin(user_id))


def format_answer(answer, debug: bool = False) -> str:
    text = answer.text
    if debug and answer.sources:
        text += ("\n\n— отладка —\nИсточники: " + "; ".join(answer.sources)
                 + f"\nрежим {answer.mode} · score {answer.top_score:.3f} · токены {answer.tokens}"
                 + "".join(f"\n⚠ {w}" for w in answer.warnings))
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
        # Сюда же встанет учёт и проверка лимита токенов по user_id (см. README, «Планы»)
        logger.info(f"Ответ для {user_id}: токенов GigaChat {answer.tokens}")
        if answer.used_rag:  # в историю пишем только содержательные обмены
            user_history += [{"role": "user", "content": query},
                             {"role": "assistant", "content": answer.text}]
            del user_history[:-config.MAX_HISTORY_PAIRS * 2]
        await status.delete()
        await send_long(message, format_answer(answer, debug=show_debug_info(user_id)))
    except Exception:
        logger.exception("Ошибка при обработке вопроса")
        await status.delete()
        await message.answer("Что-то пошло не так. Подробности в логе, попробуй ещё раз.")


# ========== ОБЫЧНЫЕ ПОЛЬЗОВАТЕЛИ ==========

@dp.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject):
    user = message.from_user
    if is_admin(user.id):
        await set_admin_menu(user.id)  # чат с ботом уже существует — меню точно встанет
    if not has_access(user.id):
        code = (command.args or "").strip()
        if not code:
            await message.answer(DENIED_TEXT)
            return
        if too_many_bad_codes(user.id):
            logger.warning(f"Слишком много неверных кодов от {user.id}")
            return  # молчим: перебор кодов ничего не даёт
        result = access.redeem(code, user.id)
        if result != "ok":
            first_failure = not bad_codes.get(user.id)  # список уже очищен от старых попыток выше
            bad_codes.setdefault(user.id, []).append(time.time())
            who = f"id={user.id}, username=@{user.username or '—'}"  # ник — только в уведомление, не в лог
            # Пользователю причина не раскрывается (нельзя отличить «нет такого» от «уже использован»),
            # а в лог и админу она нужна: по ней видно перебор и пересылку ссылок
            logger.warning(f"Инвайт отклонён ({result}): id={user.id}, код={code[:4]}…")
            # Опечатки — не чаще раза в час на человека, чтобы перебор не заспамил админа;
            # попытка вернуться после блокировки важнее — о ней сообщаем всегда (потолок: MAX_BAD_CODES/час)
            if first_failure or result == "blocked":
                await notify_admins(f"Неудачная попытка входа: {who}, причина: {result}")
            await message.answer("Код недействителен: он неверный, просрочен или уже использован.")
            return
        note = access.note_of(user.id)
        logger.info(f"Новый пользователь {user.id} по инвайту")
        await notify_admins(f"Новый пользователь: id={user.id}" + (f", заметка: {note}" if note else ""))
        await message.answer("Доступ открыт!")
    await message.answer(smalltalk.CAPABILITIES)


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(smalltalk.CAPABILITIES + (ADMIN_HELP if is_admin(message.from_user.id) else ""))


@dp.message(Command("ask"))
async def cmd_ask(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Напиши вопрос после команды, например: /ask Какая горечь у American IPA?")
        return
    await ask(message, command.args)


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
    """Забыть контекст разговора (то же, что фраза «начнём заново»)."""
    history.pop(message.from_user.id, None)
    await message.answer(smalltalk.RESET_REPLY)


@dp.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"Твой Telegram ID: {message.from_user.id}")


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message):
    if pending.pop(message.from_user.id, None):
        await message.answer("Отменено.")
    else:
        await message.answer("Отменять нечего.")


# ========== МЕНЮ КОМАНД ==========
# Админу показывается полный список (по его чату), остальным — пустое: админские команды
# они не видят вообще. Набранные вручную всё равно отклоняются в admin_only.

ADMIN_MENU = [
    BotCommand(command="invite", description="Создать инвайт-ссылку"),
    BotCommand(command="users", description="Пользователи (с кнопками)"),
    BotCommand(command="invites", description="Действующие инвайты"),
    BotCommand(command="whois", description="Кто это (спрошу ID)"),
    BotCommand(command="note", description="Заметка о человеке"),
    BotCommand(command="kick", description="Заблокировать (спрошу ID)"),
    BotCommand(command="unblock", description="Вернуть доступ (спрошу ID)"),
    BotCommand(command="revoke", description="Отозвать инвайт (спрошу код)"),
    BotCommand(command="ingest", description="Переиндексировать базу знаний"),
    BotCommand(command="stats", description="Состояние бота"),
    BotCommand(command="cancel", description="Отменить ввод"),
    BotCommand(command="help", description="Справка"),
]

ADMIN_HELP = (
    "\n\nАдминистратор. Команды из меню «/»: если нужен аргумент, я спрошу его отдельным сообщением.\n"
    "/invite — создать инвайт и получить ссылку для пересылки\n"
    "/users — пользователи с кнопками (кто это, заметка, заблокировать)\n"
    "/invites — действующие инвайты с кнопкой «Отозвать»\n"
    "/whois, /note, /kick, /unblock, /revoke — то же командами; можно сразу с аргументом: "
    "/kick 123456789\n"
    "/cancel — отменить ввод, /ingest — переиндексация базы"
)


async def set_admin_menu(admin_id: int) -> bool:
    try:
        await bot.set_my_commands(ADMIN_MENU, scope=BotCommandScopeChat(chat_id=admin_id))
        return True
    except TelegramAPIError as e:
        # Telegram знает чат только после первого сообщения админа боту; меню поставится в его /start
        logger.info(f"Меню для админа {admin_id} пока не установлено ({e})")
        return False


async def setup_commands() -> None:
    # Пустой список для всех: перебивает и старое меню из BotFather
    await bot.set_my_commands([], scope=BotCommandScopeDefault())
    for admin_id in config.ADMIN_IDS:
        await set_admin_menu(admin_id)


# ========== ДЕЙСТВИЯ АДМИНА ==========
# Каждое действие принимает строку-аргумент и возвращает (текст ответа, завершено ли).
# «Не завершено» — ошибка формата: ждём аргумент ещё раз, а не заставляем вызывать команду заново.

Executor = Callable[[str, Pending], Awaitable[Tuple[str, bool]]]

NEED_ID = "Нужен числовой ID, например 123456789 (список: /users). Отмена: /cancel"
PROMPTS = {
    "whois": "Пришли ID пользователя, о котором узнать (список: /users). Отмена: /cancel",
    "kick": "Пришли ID пользователя, которого заблокировать (список: /users). Отмена: /cancel",
    "unblock": "Пришли ID пользователя, которому вернуть доступ (список: /users). Отмена: /cancel",
    "revoke": "Пришли код инвайта, который отозвать (список: /invites), или слово all — отозвать все. "
              "Отмена: /cancel",
    "note": "Пришли ID пользователя и текст заметки одним сообщением («123456789 Вася с работы») "
            "или только ID — тогда текст спрошу отдельно. Отмена: /cancel",
}


async def whois_text(user_id: int) -> str:
    """Ник и имя по ID берутся у Telegram в момент запроса и нигде не сохраняются."""
    try:
        chat = await bot.get_chat(user_id)
    except TelegramAPIError:
        return "Telegram не отдаёт данные по этому ID: человек ещё не писал боту или заблокировал его."
    name = " ".join(x for x in (chat.first_name, chat.last_name) if x) or "—"
    note = access.note_of(user_id)
    return (f"ID: {user_id}\nUsername: {'@' + chat.username if chat.username else '—'}\nИмя: {name}"
            + (f"\nЗаметка: {note}" if note else ""))


async def exec_whois(arg: str, pend: Pending) -> Tuple[str, bool]:
    if not arg.isdigit():
        return NEED_ID, False
    return await whois_text(int(arg)), True


async def exec_kick(arg: str, pend: Pending) -> Tuple[str, bool]:
    if not arg.isdigit():
        return NEED_ID, False
    blocked = access.block_user(int(arg))
    history.pop(int(arg), None)
    return ("Доступ заблокирован (по новому инвайту тоже не войдёт, пока не сделаешь /unblock)."
            if blocked else "Такого активного пользователя нет в списке."), True


async def exec_unblock(arg: str, pend: Pending) -> Tuple[str, bool]:
    if not arg.isdigit():
        return NEED_ID, False
    ok = access.unblock_user(int(arg))
    return ("Доступ возвращён." if ok else "Этот пользователь не заблокирован."), True


async def exec_revoke(arg: str, pend: Pending) -> Tuple[str, bool]:
    if arg == "all":
        return f"Отозвано инвайтов: {access.revoke_all()}", True
    ok = access.revoke(arg)
    return ("Инвайт отозван." if ok else "Такой действующий инвайт не найден."), True


async def exec_note(arg: str, pend: Pending) -> Tuple[str, bool]:
    if pend.user_id is None:  # шаг 1: ID (и, возможно, сразу текст)
        head, _, rest = arg.partition(" ")
        if not head.isdigit():
            return NEED_ID, False
        if access.get_user(int(head)) is None:
            return "Такого пользователя нет в списке.", True
        if not rest.strip():
            pend.user_id = int(head)
            return f"Пришли текст заметки для {head} (или «-», чтобы стереть). Отмена: /cancel", False
        user_id, text = int(head), rest.strip()
    else:  # шаг 2: человек уже выбран, приходит только текст
        user_id, text = pend.user_id, arg.strip()
    note = "" if text == "-" else text[:MAX_NOTE_LEN]
    ok = access.set_note(user_id, note)
    return ("Заметка сохранена." if note else "Заметка стёрта.") if ok else "Такого пользователя нет в списке.", True


EXECUTORS: Dict[str, Executor] = {
    "whois": exec_whois, "kick": exec_kick, "unblock": exec_unblock,
    "revoke": exec_revoke, "note": exec_note,
}


def admin_only(handler):
    """Декоратор: команда доступна только ADMIN_IDS."""
    @functools.wraps(handler)  # aiogram определяет нужные аргументы по сигнатуре оригинала
    async def wrapper(message: Message, *args, **kwargs):
        if not is_admin(message.from_user.id):
            await message.answer("Эта команда доступна только администратору.")
            return
        return await handler(message, *args, **kwargs)
    return wrapper


async def run_or_prompt(message: Message, name: str, arg: str) -> None:
    """С аргументом — выполняем сразу; без него (выбрано из меню) — просим прислать и ждём."""
    admin_id = message.from_user.id
    pend = Pending(name, time.time())
    if not arg:
        pending[admin_id] = pend
        await message.answer(PROMPTS[name])
        return
    text, done = await EXECUTORS[name](arg, pend)
    if not done:
        pending[admin_id] = pend
    await message.answer(text)


async def continue_pending(message: Message, pend: Pending) -> None:
    text, done = await EXECUTORS[pend.command](message.text.strip(), pend)
    if done:
        pending.pop(message.from_user.id, None)
    await message.answer(text)


def make_command(name: str):
    @dp.message(Command(name))
    @admin_only
    async def handler(message: Message, command: CommandObject):
        await run_or_prompt(message, name, (command.args or "").strip())
    handler.__name__ = f"cmd_{name}"
    return handler


for _name in EXECUTORS:
    make_command(_name)


# ========== ИНВАЙТЫ ==========

@dp.message(Command("invite"))
@admin_only
async def cmd_invite(message: Message, command: CommandObject):
    """/invite [дней=7] [заметка]. Один инвайт — один вход одного пользователя."""
    words = (command.args or "").split()
    days = int(words.pop(0)) if words and words[0].isdigit() else 7
    note = " ".join(words)[:MAX_NOTE_LEN]
    if not 1 <= days <= 90:
        await message.answer("Формат: /invite [срок в днях: 1–90] [заметка]\n"
                             "Например: /invite 7 Вася с работы")
        return
    code = access.create_invite(message.from_user.id, days, note)
    await message.answer(
        f"Инвайт создан: на одного человека, действует {days} дн."
        + (f"\nЗаметка: {note}" if note else "")
        + f"\nКод для отзыва: {code}\n\nПерешли человеку сообщение ниже ⬇️"
    )
    # Отдельное сообщение без служебного текста — пересылается как есть.
    # Кнопку «Start» упоминать не нужно: при переходе по ссылке Telegram показывает её сам.
    await message.answer(
        "Приглашаю в Beer Style Assistant — справочник по пивным стилям BJCP.\n"
        f"Ссылка: https://t.me/{bot_username}?start={code}"
    )


def invite_keyboard(code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚫 Отозвать", callback_data=f"i:revoke:{code}")]])


@dp.message(Command("invites"))
@admin_only
async def cmd_invites(message: Message):
    items = access.active_invites()
    if not items:
        await message.answer("Действующих инвайтов нет. Создать: /invite")
        return
    await message.answer(
        f"Действующих инвайтов: {len(items)}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🚫 Отозвать все", callback_data="i:all:-")]]),
    )
    for i in items:
        await message.answer(f"{i.code}\nдо {fmt_date(i.expires_at)}" + (f"\n{i.note}" if i.note else ""),
                             reply_markup=invite_keyboard(i.code))


# ========== ПОЛЬЗОВАТЕЛИ С КНОПКАМИ ==========

def user_card(u: User) -> str:
    return (f"{'⛔ ' if u.blocked else ''}{u.user_id}" + (f" — {u.note}" if u.note else "")
            + f"\nс {fmt_date(u.joined_at)}" + (" · заблокирован" if u.blocked else ""))


def user_keyboard(u: User) -> InlineKeyboardMarkup:
    toggle = (InlineKeyboardButton(text="✅ Разблокировать", callback_data=f"u:unblock:{u.user_id}")
              if u.blocked else
              InlineKeyboardButton(text="⛔ Заблокировать", callback_data=f"u:kick:{u.user_id}"))
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Кто это", callback_data=f"u:whois:{u.user_id}"),
         InlineKeyboardButton(text="✏️ Заметка", callback_data=f"u:note:{u.user_id}")],
        [toggle],
    ])


@dp.message(Command("users"))
@admin_only
async def cmd_users(message: Message):
    users = access.users()
    if not users:
        await message.answer("Приглашённых пользователей пока нет.")
        return
    shown = users[:MAX_USER_CARDS]
    await message.answer(f"Пользователи: {len(users)}"
                         + (f" (показаны первые {MAX_USER_CARDS})" if len(users) > len(shown) else ""))
    for u in shown:
        await message.answer(user_card(u), reply_markup=user_keyboard(u))


@dp.callback_query(F.data.startswith("u:"))
async def on_user_button(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Только для администратора", show_alert=True)
        return
    _, action, raw = cb.data.split(":", 2)
    user_id = int(raw)
    chat = cb.message if isinstance(cb.message, Message) else None
    if chat is None:
        await cb.answer()
        return
    if action == "whois":
        await chat.answer(await whois_text(user_id))
        await cb.answer()
    elif action == "note":
        pending[cb.from_user.id] = Pending("note", time.time(), user_id=user_id)
        await chat.answer(f"Пришли текст заметки для {user_id} (или «-», чтобы стереть). Отмена: /cancel")
        await cb.answer()
    elif action in ("kick", "unblock"):
        text, _ = await EXECUTORS[action](raw, Pending(action, time.time()))
        user = access.get_user(user_id)
        if user is not None:  # карточка перерисовывается: кнопка меняется на противоположную
            await chat.edit_text(user_card(user), reply_markup=user_keyboard(user))
        await cb.answer(text, show_alert=False)
    else:
        await cb.answer()


@dp.callback_query(F.data.startswith("i:"))
async def on_invite_button(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Только для администратора", show_alert=True)
        return
    _, action, arg = cb.data.split(":", 2)
    chat = cb.message if isinstance(cb.message, Message) else None
    if chat is None:
        await cb.answer()
        return
    if action == "revoke":
        ok = access.revoke(arg)
        await chat.edit_text(f"🚫 Отозван: {arg}" if ok else f"Уже недействителен: {arg}")
        await cb.answer("Отозван" if ok else "Уже недействителен")
    elif action == "all":
        await chat.edit_text(f"Отозвано инвайтов: {access.revoke_all()}")
        await cb.answer()
    else:
        await cb.answer()


# ========== ПРОЧЕЕ ==========

@dp.message(Command("ingest"))
@admin_only
async def cmd_ingest(message: Message):
    status = await message.answer("Индексирую справочник…")
    try:
        count = await asyncio.to_thread(pipeline.ingest)
        stats = pipeline.stats()
        await status.edit_text(f"Готово. Стилей проиндексировано: {count}, "
                               f"размерность: {stats['dimension']}.")
    except Exception as e:
        logger.exception("Ошибка индексации")
        await status.edit_text(f"Ошибка индексации: {e}")


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    user_id = message.from_user.id
    if is_admin(user_id):
        pend = get_pending(user_id)
        if pend:  # админ отвечает на вопрос бота («пришли ID…»), а не спрашивает про пиво
            await continue_pending(message, pend)
            return
    intent = smalltalk.detect(message.text)
    if intent == smalltalk.RESET:
        history.pop(user_id, None)
        await message.answer(smalltalk.RESET_REPLY)
    elif intent == smalltalk.GREETING:
        await message.answer(smalltalk.GREETING_REPLY)
    elif intent == smalltalk.HELP:
        await message.answer(smalltalk.CAPABILITIES)
    else:
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
    await setup_commands()
    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
