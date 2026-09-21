"""
Демонстрация: печатает «запись переписки» с ботом в подменённом Telegram (настоящий код бота,
без сети и ключей). Показывает, как выглядят инвайты, кнопки под пользователями и
ожидание аргумента. Запуск: python tests/demo_transcript.py
"""

import asyncio

from harness import ADMIN, STRANGER, invite_code, last_rec, new_world, press, say, vasya

import bot as botmod  # noqa: E402

NAMES = {ADMIN: "Админ", STRANGER: "Вася", 3: "Незнакомец"}


def show(session, start: int) -> int:
    """Печатает события журнала начиная с индекса start; возвращает новый индекс."""
    for kind, payload in session.log[start:]:
        if kind == "user":
            uid, text = payload
            print(f"\n👤 {NAMES.get(uid, uid)}: {text}")
        elif kind == "press":
            uid, button = payload
            print(f"\n👆 {NAMES.get(uid, uid)} нажимает кнопку «{button}»")
        elif kind in ("bot", "edit"):
            if payload.text == "Ищу в справочнике…":
                continue
            label = "🤖 Бот → " + NAMES.get(payload.chat_id, payload.chat_id) if kind == "bot" \
                else "✏️  Бот правит сообщение"
            body = payload.text.replace("\n", "\n      ")
            print(f"   {label}:\n      {body}")
            for row in payload.buttons():
                print("      " + "  ".join(f"[ {b} ]" for b in row))
        elif kind == "toast" and payload:
            print(f"   🔔 всплывающая подсказка: «{payload}»")
    return len(session.log)


async def main():
    session = new_world()
    session.known_users[STRANGER] = vasya()
    pos = 0

    async def step(title, coro):
        nonlocal pos
        print(f"\n{'=' * 70}\n{title}\n{'=' * 70}", end="")
        await coro
        pos = show(session, pos)

    async def scene_invite():
        await say(ADMIN, "/invite", session=session)

    await step("1. Админ выбирает /invite из меню — бот присылает ссылку для пересылки", scene_invite())

    async def scene_join():
        await say(STRANGER, f"/start {invite_code(session)}", username="vasya", session=session)
        await say(3, "привет", session=session)
        await say(STRANGER, "что ты умеешь?", session=session)

    await step("2. Вася переходит по ссылке (Telegram сам показывает кнопку Start), "
               "незнакомец пишет без инвайта", scene_join())

    async def scene_users():
        await say(ADMIN, "/users", session=session)
        card = last_rec(session, ADMIN, str(STRANGER))
        await press(ADMIN, card, "Заметка", session=session)
        await say(ADMIN, "Вася-пивовар", session=session)
        await say(ADMIN, "/users", session=session)
        card = last_rec(session, ADMIN, "Вася-пивовар")
        await press(ADMIN, card, "Кто это", session=session)
        await press(ADMIN, card, "Заблокировать", session=session)
        await say(STRANGER, "какая горечь у IPA?", session=session)
        await press(ADMIN, card, "Разблокировать", session=session)

    await step("3. /users: карточки с кнопками под каждым человеком", scene_users())

    async def scene_prompt():
        await say(ADMIN, "/kick", session=session)
        await say(ADMIN, "не число", session=session)
        await say(ADMIN, "/cancel", session=session)
        await say(ADMIN, "/whois", session=session)
        await say(ADMIN, str(STRANGER), session=session)

    await step("4. Команда с аргументом из меню: бот просит дописать и ждёт", scene_prompt())

    async def scene_invites():
        await say(ADMIN, "/invite 14 Маша", session=session)
        await say(ADMIN, "/invites", session=session)
        await press(ADMIN, last_rec(session, ADMIN, "Маша"), "Отозвать", session=session)

    await step("5. /invites: список с кнопкой «Отозвать»", scene_invites())


if __name__ == "__main__":
    asyncio.run(main())
