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
from aiogram.methods import GetMe, SendMessage  # noqa: E402
from aiogram.types import Chat, Message, Update, User  # noqa: E402

import bot as botmod  # noqa: E402
import config  # noqa: E402
from access import AccessStore  # noqa: E402

ADMIN, STRANGER = 1, 2


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []  # (chat_id, text)

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            self.sent.append((method.chat_id, method.text))
            return Message(message_id=len(self.sent), date=datetime.now(),
                           chat=Chat(id=method.chat_id, type="private"), text=method.text).as_(bot)
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
        return Answer("ответ про пиво", ["BJCP 21A"], 0.9, True)


async def say(user_id: int, text: str, username: str = None):
    msg = Message(message_id=1, date=datetime.now(), chat=Chat(id=user_id, type="private"),
                  from_user=User(id=user_id, is_bot=False, first_name="u", username=username), text=text)
    await botmod.dp.feed_update(botmod.bot, Update(update_id=1, message=msg))


def last_to(session, user_id):
    return next(t for c, t in reversed(session.sent) if c == user_id)


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
    await say(ADMIN, "/invite 1 7")
    reply = last_to(session, ADMIN)
    code = re.search(r"Код: (\S+)", reply).group(1)
    assert f"https://t.me/beer_test_bot?start={code}" in reply

    # 4. Неверный код не открывает доступ
    await say(STRANGER, "/start wrongcode")
    assert "недействителен" in last_to(session, STRANGER)
    await say(STRANGER, "снова вопрос")
    assert botmod.pipeline.calls == 0

    # 5. Верный код открывает доступ, админ получает уведомление
    await say(STRANGER, f"/start {code}", username="vasya")
    assert "Доступ открыт" in [t for c, t in session.sent if c == STRANGER][-2]
    assert "Новый пользователь" in last_to(session, ADMIN) and "vasya" in last_to(session, ADMIN)
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
    assert "Доступ отозван" in last_to(session, ADMIN)
    calls = botmod.pipeline.calls
    await say(STRANGER, "ещё вопрос")
    assert "по приглашениям" in last_to(session, STRANGER)
    assert botmod.pipeline.calls == calls

    # 9. Перебор кодов: после 5 неудач бот замолкает
    before = len(session.sent)
    for i in range(5):
        await say(4, f"/start bad{i}")
    assert len(session.sent) == before + 5
    await say(4, "/start bad-again")
    assert len(session.sent) == before + 5  # шестая попытка проигнорирована

    # 10. Админ работает без инвайта
    await say(ADMIN, "какая горечь у IPA?")
    assert botmod.pipeline.calls == calls + 1


def test_access_flow():
    asyncio.run(scenario())


if __name__ == "__main__":
    test_access_flow()
    print("ok  test_access_flow: 10 сценариев пройдено")
