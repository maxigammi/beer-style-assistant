"""
Сквозной тест доступа по инвайтам: настоящие апдейты идут через диспетчер aiogram,
Telegram подменён (сессия только записывает исходящие сообщения).
Запуск: python tests/test_bot_access.py
"""

import asyncio
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Токен формально валидный, в сеть не ходим
os.environ.setdefault("TELEGRAM_TOKEN", "123456:TEST")

from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.exceptions import TelegramBadRequest  # noqa: E402
from aiogram.methods import GetChat, GetMe, SendMessage, SetMyCommands  # noqa: E402
from aiogram.types import Chat, ChatFullInfo, Message, Update, User  # noqa: E402

import bot as botmod  # noqa: E402
import config  # noqa: E402
from access import AccessStore  # noqa: E402

ADMIN, STRANGER = 1, 2


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []  # (chat_id, text)
        self.menus = []  # (scope, [команды])
        self.no_chat = {404}  # админы, ещё не писавшие боту: для них Telegram отвечает "chat not found"

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            self.sent.append((method.chat_id, method.text))
            return Message(message_id=len(self.sent), date=datetime.now(),
                           chat=Chat(id=method.chat_id, type="private"), text=method.text).as_(bot)
        if isinstance(method, SetMyCommands):
            scope = method.scope
            if getattr(scope, "chat_id", None) in self.no_chat:
                raise TelegramBadRequest(method=method, message="Bad Request: chat not found")
            self.menus.append((scope, [c.command for c in method.commands]))
            return True
        if isinstance(method, GetChat):
            if method.chat_id != STRANGER:  # Telegram знает только тех, кто писал боту
                raise TelegramBadRequest(method=method, message="Bad Request: chat not found")
            return ChatFullInfo.model_construct(id=STRANGER, type="private", first_name="Василий",
                                                last_name="Пупкин", username="vasya")
        if isinstance(method, GetMe):
            return User(id=999, is_bot=True, first_name="beer", username="beer_test_bot")
        return True  # delete_message и прочее

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


async def say(user_id: int, text: str, username: str = None):
    msg = Message(message_id=1, date=datetime.now(), chat=Chat(id=user_id, type="private"),
                  from_user=User(id=user_id, is_bot=False, first_name="u", username=username), text=text)
    await botmod.dp.feed_update(botmod.bot, Update(update_id=1, message=msg))


def last_to(session, user_id, n=1):
    """n-е с конца сообщение, отправленное пользователю."""
    return [t for c, t in session.sent if c == user_id][-n]


def invite_code(session):
    return re.search(r"start=(\S+)", last_to(session, ADMIN)).group(1)


async def scenario():
    session = RecordingSession()
    botmod.bot = Bot(token="123456:TEST", session=session)
    botmod.bot_username = "beer_test_bot"
    botmod.pipeline = FakePipeline()
    botmod.access = AccessStore(Path(tempfile.mkdtemp()) / "access.db")
    config.ADMIN_IDS = {ADMIN}

    # 1. Чужой не получает ответа и не тратит токены
    await say(STRANGER, "какая горечь у IPA?")
    assert "по приглашениям" in last_to(session, STRANGER)
    assert botmod.pipeline.calls == 0

    # 2. Закрытые команды чужому недоступны (в т.ч. /ingest и /invite)
    for cmd in ("/ingest", "/invite", "/users", "/ask IPA"):
        await say(STRANGER, cmd)
        assert "по приглашениям" in last_to(session, STRANGER), cmd
    assert botmod.pipeline.calls == 0

    # 3. Админ создаёт инвайт
    await say(ADMIN, "/invite 7 Вася с работы")
    info, forward = last_to(session, ADMIN, 2), last_to(session, ADMIN)
    assert "Вася с работы" in info and "7 дн." in info and "Код для отзыва" in info
    code = invite_code(session)
    assert f"https://t.me/beer_test_bot?start={code}" in forward
    # пересылаемое сообщение чистое: без служебных полей админа
    assert "Код" not in forward and "Вася" not in forward and "Инвайт создан" not in forward

    # 4. Неверный код не открывает доступ
    await say(STRANGER, "/start wrongcode")
    assert "недействителен" in last_to(session, STRANGER)
    await say(STRANGER, "снова вопрос")
    assert botmod.pipeline.calls == 0

    # 5. Верный код открывает доступ, админ получает уведомление
    await say(STRANGER, f"/start {code}", username="vasya")
    assert "Доступ открыт" in [t for c, t in session.sent if c == STRANGER][-2]
    assert "Новый пользователь" in last_to(session, ADMIN) and "Вася с работы" in last_to(session, ADMIN)
    await say(STRANGER, "какая горечь у IPA?")
    assert botmod.pipeline.calls == 1
    assert "ответ про пиво" in last_to(session, STRANGER)

    # 6. Одноразовый код второй раз не работает
    await say(3, f"/start {code}")
    assert "недействителен" in last_to(session, 3)

    # 7. Участник не может админить
    await say(STRANGER, "/invite")
    assert "только администратору" in last_to(session, STRANGER)
    await say(STRANGER, "/kick 1")
    assert "только администратору" in last_to(session, STRANGER)

    # 8. Админ выгоняет — доступ пропадает
    await say(ADMIN, f"/kick {STRANGER}")
    assert "заблокирован" in last_to(session, ADMIN)
    calls = botmod.pipeline.calls
    await say(STRANGER, "ещё вопрос")
    assert "по приглашениям" in last_to(session, STRANGER)
    assert botmod.pipeline.calls == calls

    # 9. Перебор кодов: после 5 неудач бот замолкает, а админа уведомляют один раз
    to_user4 = lambda: len([1 for c, _ in session.sent if c == 4])  # noqa: E731
    to_admin = lambda: len([1 for c, t in session.sent if c == ADMIN and "Неудачная попытка" in t])  # noqa: E731
    admin_before = to_admin()
    for i in range(5):
        await say(4, f"/start bad{i}")
    assert to_user4() == 5 and to_admin() == admin_before + 1
    assert "not_found" in last_to(session, ADMIN)  # причина известна админу...
    assert "not_found" not in last_to(session, 4)  # ...но не пользователю
    await say(4, "/start bad-again")
    assert to_user4() == 5  # шестая попытка проигнорирована

    # 10. Админ работает без инвайта
    await say(ADMIN, "какая горечь у IPA?")
    assert botmod.pipeline.calls == calls + 1

    # 11. Заблокированный не входит даже по свежему инвайту (и инвайт не сгорает); /unblock возвращает
    await say(ADMIN, "/invite 7 Для Пети")
    fresh = invite_code(session)
    await say(STRANGER, f"/start {fresh}")  # STRANGER заблокирован ещё в п. 8
    assert "недействителен" in last_to(session, STRANGER)
    assert "blocked" in last_to(session, ADMIN)
    assert botmod.access.redeem(fresh, 77) == "ok"  # тот же инвайт спокойно достаётся другому
    await say(STRANGER, "вопрос")
    assert "по приглашениям" in last_to(session, STRANGER)
    await say(ADMIN, f"/unblock {STRANGER}")
    assert "возвращён" in last_to(session, ADMIN)
    await say(STRANGER, "вопрос")
    assert "ответ про пиво" in last_to(session, STRANGER)

    # 12. /users показывает активность, /revoke all отзывает всё
    await say(ADMIN, "/users")
    users_view = last_to(session, ADMIN)
    assert f"{STRANGER} — Вася с работы" in users_view  # заметка вместо ника
    assert "vasya" not in users_view
    await say(ADMIN, "/revoke all")
    assert "Отозвано инвайтов: 0" in last_to(session, ADMIN)  # «Для Пети» уже использован в п. 11

    # 13. Форматы /invite: только заметка, только срок, мусор
    await say(ADMIN, "/invite Маша")
    assert "действует 7 дн." in last_to(session, ADMIN, 2) and "Маша" in last_to(session, ADMIN, 2)
    await say(ADMIN, "/invite")  # из меню, без аргументов: 7 дней и готовая ссылка
    assert "действует 7 дн." in last_to(session, ADMIN, 2) and "start=" in last_to(session, ADMIN)
    await say(ADMIN, "/invite 30")
    assert "действует 30 дн." in last_to(session, ADMIN, 2)
    await say(ADMIN, "/invite 0")
    assert "Формат" in last_to(session, ADMIN)
    await say(ADMIN, "/revoke all")
    assert "Отозвано инвайтов: 3" in last_to(session, ADMIN)

    # 14. /whois: имя и ник берутся у Telegram на лету; для неизвестного ID — понятная ошибка
    await say(ADMIN, f"/whois {STRANGER}")
    who = last_to(session, ADMIN)
    assert "@vasya" in who and "Василий Пупкин" in who and "Вася с работы" in who
    await say(ADMIN, "/whois 555")
    assert "не отдаёт данные" in last_to(session, ADMIN)
    await say(STRANGER, "/whois 2")
    assert "только администратору" in last_to(session, STRANGER)
    cols = {r[1] for r in botmod.access.db.execute("PRAGMA table_info(users)")}
    assert "username" not in cols  # /whois ничего не сохраняет

    # 15. /note ставит и стирает заметку
    await say(ADMIN, f"/note {STRANGER} Петя из гаража")
    assert "сохранена" in last_to(session, ADMIN)
    assert botmod.access.note_of(STRANGER) == "Петя из гаража"
    await say(ADMIN, f"/note {STRANGER}")
    assert botmod.access.note_of(STRANGER) == ""
    await say(ADMIN, "/note 555 текст")
    assert "нет в списке" in last_to(session, ADMIN)
    await say(STRANGER, f"/note {STRANGER} я себя назначу")
    assert "только администратору" in last_to(session, STRANGER)
    assert botmod.access.note_of(STRANGER) == ""


async def menu_scenario():
    session = RecordingSession()
    botmod.bot = Bot(token="123456:TEST", session=session)
    config.ADMIN_IDS = {ADMIN, 404}  # 404 ещё не писал боту: его меню не должно ронять запуск
    await botmod.setup_commands()

    default = [cmds for scope, cmds in session.menus if scope.type == "default"]
    per_chat = {scope.chat_id: cmds for scope, cmds in session.menus if scope.type == "chat"}
    assert default == [[]]                       # все остальные не видят ни одной команды
    assert set(per_chat) == {ADMIN}              # админское меню привязано только к чату админа
    assert per_chat[ADMIN][0] == "invite"        # /invite — первая в меню
    assert {"users", "whois", "note", "kick", "unblock", "revoke", "ingest"} <= set(per_chat[ADMIN])

    # Админ 404 пишет боту /start — чат появился, меню ставится сразу, без перезапуска
    botmod.access = AccessStore(Path(tempfile.mkdtemp()) / "access.db")
    session.no_chat.discard(404)
    await say(404, "/start")
    assert any(scope.type == "chat" and scope.chat_id == 404 for scope, _ in session.menus)
    # А обычному пользователю меню не ставится никогда
    await say(ADMIN, "/invite")
    code = invite_code(session)
    await say(STRANGER, f"/start {code}")
    assert not any(scope.type == "chat" and scope.chat_id == STRANGER for scope, _ in session.menus)


def test_access_flow():
    asyncio.run(scenario())


def test_command_menu():
    asyncio.run(menu_scenario())


if __name__ == "__main__":
    test_access_flow()
    print("ok  test_access_flow: 16 сценариев пройдено")
    test_command_menu()
    print("ok  test_command_menu: меню только у админа")
