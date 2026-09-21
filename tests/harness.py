"""
Подменённый Telegram для тестов и демонстрации: настоящие апдейты идут через диспетчер aiogram,
а исходящие сообщения, кнопки, правки и меню команд запоминаются.
"""

import os
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TELEGRAM_TOKEN", "123456:TEST")  # формально валидный, в сеть не ходим

from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.exceptions import TelegramBadRequest  # noqa: E402
from aiogram.methods import (  # noqa: E402
    AnswerCallbackQuery, EditMessageText, GetChat, GetMe, SendMessage, SetMyCommands,
)
from aiogram.types import (  # noqa: E402
    CallbackQuery, Chat, ChatFullInfo, Message, Update, User,
)

import bot as botmod  # noqa: E402
import config  # noqa: E402
from access import AccessStore  # noqa: E402

ADMIN, STRANGER = 1, 2


@dataclass
class Sent:
    message_id: int
    chat_id: int
    text: str
    markup: Optional[object] = None  # InlineKeyboardMarkup

    def buttons(self) -> List[List[str]]:
        rows = self.markup.inline_keyboard if self.markup else []
        return [[b.text for b in row] for row in rows]

    def callback_data(self, button_text: str) -> str:
        for row in self.markup.inline_keyboard:
            for b in row:
                if button_text in b.text:
                    return b.callback_data
        raise KeyError(button_text)


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.records: List[Sent] = []
        self.menus = []  # (scope, [команды])
        self.callback_answers = []  # (текст, alert)
        self.no_chat = set()  # ID, для которых Telegram отвечает «chat not found»
        self.known_users = {}  # user_id -> ChatFullInfo для get_chat
        self.log: List[str] = []  # хронология для показа переписки

    @property
    def sent(self):
        return [(r.chat_id, r.text) for r in self.records]

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            rec = Sent(len(self.records) + 1, method.chat_id, method.text, method.reply_markup)
            self.records.append(rec)
            self.log.append(("bot", replace(rec)))  # снимок: сообщение потом могут отредактировать
            return self._message(rec).as_(bot)
        if isinstance(method, EditMessageText):
            rec = next(r for r in self.records if r.message_id == method.message_id and r.chat_id == method.chat_id)
            rec.text, rec.markup = method.text, method.reply_markup
            self.log.append(("edit", replace(rec)))
            return self._message(rec).as_(bot)
        if isinstance(method, AnswerCallbackQuery):
            self.callback_answers.append((method.text, bool(method.show_alert)))
            self.log.append(("toast", method.text or ""))
            return True
        if isinstance(method, SetMyCommands):
            scope = method.scope
            if getattr(scope, "chat_id", None) in self.no_chat:
                raise TelegramBadRequest(method=method, message="Bad Request: chat not found")
            self.menus.append((scope, [c.command for c in method.commands]))
            return True
        if isinstance(method, GetChat):
            info = self.known_users.get(method.chat_id)
            if info is None:  # Telegram знает только тех, кто писал боту
                raise TelegramBadRequest(method=method, message="Bad Request: chat not found")
            return info
        if isinstance(method, GetMe):
            return User(id=999, is_bot=True, first_name="beer", username="beer_test_bot")
        return True  # delete_message и прочее

    @staticmethod
    def _message(rec: Sent) -> Message:
        return Message(message_id=rec.message_id, date=datetime.now(), chat=Chat(id=rec.chat_id, type="private"),
                       text=rec.text, reply_markup=rec.markup)

    async def close(self):
        pass

    async def stream_content(self, *a, **kw):
        raise NotImplementedError


class FakePipeline:
    def __init__(self):
        self.calls = 0

    def answer(self, query, history=None):
        from rag.pipeline import Answer
        self.calls += 1
        return Answer("ответ про пиво", ["BJCP 21A"], 0.9, True, tokens=1234)


def new_world(admins=(ADMIN,)) -> RecordingSession:
    """Чистый бот с подменённым Telegram: возвращает сессию для проверок."""
    session = RecordingSession()
    botmod.bot = Bot(token="123456:TEST", session=session)
    botmod.bot_username = "beer_test_bot"
    botmod.pipeline = FakePipeline()
    botmod.access = AccessStore(Path(tempfile.mkdtemp()) / "access.db")
    botmod.history.clear()
    botmod.pending.clear()
    botmod.bad_codes.clear()
    config.ADMIN_IDS = set(admins)
    return session


async def say(user_id: int, text: str, username: str = None, session: RecordingSession = None):
    if session is not None:
        session.log.append(("user", (user_id, text)))
    msg = Message(message_id=1, date=datetime.now(), chat=Chat(id=user_id, type="private"),
                  from_user=User(id=user_id, is_bot=False, first_name="u", username=username), text=text)
    await botmod.dp.feed_update(botmod.bot, Update(update_id=1, message=msg))


async def press(user_id: int, rec: Sent, button_text: str = "", session: RecordingSession = None,
                data: str = None):
    """Нажатие inline-кнопки под сообщением rec (data — для устаревшей кнопки, которой уже нет в сообщении)."""
    data = data or rec.callback_data(button_text)
    if session is not None:
        session.log.append(("press", (user_id, button_text)))
    cb = CallbackQuery(
        id="cb1", chat_instance="x", data=data,
        from_user=User(id=user_id, is_bot=False, first_name="u"),
        message=Message(message_id=rec.message_id, date=datetime.now(),
                        chat=Chat(id=rec.chat_id, type="private"), text=rec.text),
    )
    await botmod.dp.feed_update(botmod.bot, Update(update_id=1, callback_query=cb))


def last_to(session: RecordingSession, user_id: int, n: int = 1) -> str:
    """n-е с конца сообщение, отправленное пользователю."""
    return [r.text for r in session.records if r.chat_id == user_id][-n]


def last_rec(session: RecordingSession, user_id: int, contains: str = "") -> Sent:
    return [r for r in session.records if r.chat_id == user_id and contains in r.text][-1]


def invite_code(session: RecordingSession) -> str:
    import re
    return re.search(r"start=(\S+)", last_to(session, ADMIN)).group(1)


def vasya() -> ChatFullInfo:
    return ChatFullInfo.model_construct(id=STRANGER, type="private", first_name="Василий",
                                        last_name="Пупкин", username="vasya")
