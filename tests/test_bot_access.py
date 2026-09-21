"""
Сквозные тесты бота: настоящие апдейты идут через диспетчер aiogram, Telegram подменён
(см. harness.py). Запуск: python tests/test_bot_access.py (или pytest).
"""

import asyncio
import time

import harness
from harness import (ADMIN, STRANGER, invite_code, last_rec, last_to, new_world, press, say,
                     vasya)

import bot as botmod  # noqa: E402
import smalltalk  # noqa: E402


# ---------- доступ по инвайтам ----------

async def access_flow():
    session = new_world()

    # 1. Чужой не получает ответа и не тратит токены
    await say(STRANGER, "какая горечь у IPA?")
    assert "по приглашениям" in last_to(session, STRANGER)
    assert botmod.pipeline.calls == 0

    # 2. Закрытые команды чужому недоступны (в т.ч. /ingest и /invite)
    for cmd in ("/ingest", "/invite", "/users", "/ask IPA"):
        await say(STRANGER, cmd)
        assert "по приглашениям" in last_to(session, STRANGER), cmd
    assert botmod.pipeline.calls == 0

    # 3. Админ создаёт инвайт; пересылаемое сообщение чистое
    await say(ADMIN, "/invite 7 Вася с работы")
    info, forward = last_to(session, ADMIN, 2), last_to(session, ADMIN)
    assert "Вася с работы" in info and "7 дн." in info and "Код для отзыва" in info
    code = invite_code(session)
    assert f"https://t.me/beer_test_bot?start={code}" in forward
    assert "Код" not in forward and "Вася" not in forward and "Инвайт создан" not in forward
    assert "Start" not in forward and "Старт" not in forward  # кнопку Telegram показывает сам

    # 4. Неверный код не открывает доступ
    await say(STRANGER, "/start wrongcode")
    assert "недействителен" in last_to(session, STRANGER)
    await say(STRANGER, "снова вопрос")
    assert botmod.pipeline.calls == 0

    # 5. Верный код открывает доступ, админ получает уведомление
    await say(STRANGER, f"/start {code}", username="vasya")
    assert "Доступ открыт" in last_to(session, STRANGER, 2)
    assert "Beer Style Assistant" in last_to(session, STRANGER)  # сразу рассказали, что умеем
    assert "Новый пользователь" in last_to(session, ADMIN) and "Вася с работы" in last_to(session, ADMIN)
    await say(STRANGER, "какая горечь у IPA?")
    assert botmod.pipeline.calls == 1
    assert "ответ про пиво" in last_to(session, STRANGER)

    # 6. Одноразовый код второй раз не работает
    await say(3, f"/start {code}")
    assert "недействителен" in last_to(session, 3)

    # 7. Участник не может админить
    for cmd in ("/invite", "/kick 1", "/users", "/ingest", f"/note {STRANGER} я себя назначу"):
        await say(STRANGER, cmd)
        assert "только администратору" in last_to(session, STRANGER), cmd
    assert botmod.access.note_of(STRANGER) == "Вася с работы"

    # 8. Админ блокирует — доступ пропадает
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
    await say(STRANGER, f"/start {fresh}")
    assert "недействителен" in last_to(session, STRANGER)
    assert "blocked" in last_to(session, ADMIN)
    assert botmod.access.redeem(fresh, 77) == "ok"  # тот же инвайт спокойно достаётся другому
    await say(ADMIN, f"/unblock {STRANGER}")
    assert "возвращён" in last_to(session, ADMIN)
    await say(STRANGER, "вопрос про стиль")
    assert "ответ про пиво" in last_to(session, STRANGER)

    # 12. /invite: форматы; /revoke all
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

    # 13. /whois на лету, ничего не сохраняет
    session.known_users[STRANGER] = vasya()
    await say(ADMIN, f"/whois {STRANGER}")
    who = last_to(session, ADMIN)
    assert "@vasya" in who and "Василий Пупкин" in who and "Вася с работы" in who
    await say(ADMIN, "/whois 555")
    assert "не отдаёт данные" in last_to(session, ADMIN)
    cols = {r[1] for r in botmod.access.db.execute("PRAGMA table_info(users)")}
    assert "username" not in cols

    # 14. /note напрямую
    await say(ADMIN, f"/note {STRANGER} Петя из гаража")
    assert botmod.access.note_of(STRANGER) == "Петя из гаража"
    await say(ADMIN, f"/note {STRANGER} -")
    assert botmod.access.note_of(STRANGER) == ""


async def menu():
    session = new_world(admins=(ADMIN, 404))
    session.no_chat.add(404)  # 404 ещё не писал боту: его меню не должно ронять запуск
    await botmod.setup_commands()

    default = [cmds for scope, cmds in session.menus if scope.type == "default"]
    per_chat = {scope.chat_id: cmds for scope, cmds in session.menus if scope.type == "chat"}
    assert default == [[]]                       # все остальные не видят ни одной команды
    assert set(per_chat) == {ADMIN}              # админское меню привязано только к чату админа
    assert per_chat[ADMIN][0] == "invite"        # /invite — первая в меню
    assert {"users", "whois", "note", "kick", "unblock", "revoke", "ingest", "cancel"} <= set(per_chat[ADMIN])

    # Админ 404 пишет боту /start — чат появился, меню ставится сразу, без перезапуска
    session.no_chat.discard(404)
    await say(404, "/start")
    assert any(scope.type == "chat" and scope.chat_id == 404 for scope, _ in session.menus)
    # А обычному пользователю меню не ставится никогда
    await say(ADMIN, "/invite")
    await say(STRANGER, f"/start {invite_code(session)}")
    assert not any(scope.type == "chat" and scope.chat_id == STRANGER for scope, _ in session.menus)


# ---------- ожидание аргумента после выбора команды из меню ----------

async def argument_prompts():
    session = new_world()
    botmod.access.redeem(botmod.access.create_invite(ADMIN, 7, "Вася"), STRANGER)
    session.known_users[STRANGER] = vasya()

    # /whois из меню -> бот просит ID; неверный ввод не сбрасывает ожидание; верный выполняет
    await say(ADMIN, "/whois")
    assert "Пришли ID" in last_to(session, ADMIN) and botmod.pending[ADMIN].command == "whois"
    await say(ADMIN, "не число")
    assert "Нужен числовой ID" in last_to(session, ADMIN) and ADMIN in botmod.pending
    await say(ADMIN, str(STRANGER))
    assert "@vasya" in last_to(session, ADMIN) and ADMIN not in botmod.pending
    assert botmod.pipeline.calls == 0  # ID не ушёл в RAG как вопрос про пиво

    # После выполнения админ снова задаёт обычные вопросы
    await say(ADMIN, "какая горечь у IPA?")
    assert botmod.pipeline.calls == 1

    # /kick -> /cancel: ничего не заблокировано, ожидание снято
    await say(ADMIN, "/kick")
    assert "заблокировать" in last_to(session, ADMIN)
    await say(ADMIN, "/cancel")
    assert last_to(session, ADMIN) == "Отменено." and ADMIN not in botmod.pending
    await say(ADMIN, "/cancel")
    assert "нечего" in last_to(session, ADMIN)
    assert botmod.access.is_member(STRANGER)

    # Другая команда отменяет предыдущее ожидание и ставит своё
    await say(ADMIN, "/kick")
    await say(ADMIN, "/unblock")
    assert botmod.pending[ADMIN].command == "unblock"
    await say(ADMIN, str(STRANGER))
    assert "не заблокирован" in last_to(session, ADMIN) and botmod.access.is_member(STRANGER)

    # /kick через ожидание реально блокирует
    await say(ADMIN, "/kick")
    await say(ADMIN, str(STRANGER))
    assert "заблокирован" in last_to(session, ADMIN) and not botmod.access.is_member(STRANGER)
    await say(ADMIN, "/unblock")
    await say(ADMIN, str(STRANGER))
    assert botmod.access.is_member(STRANGER)

    # /note: два шага (ID, затем текст) и шорткат «ID текст»
    await say(ADMIN, "/note")
    await say(ADMIN, str(STRANGER))
    assert "текст заметки" in last_to(session, ADMIN) and botmod.pending[ADMIN].user_id == STRANGER
    await say(ADMIN, "Петя из гаража")
    assert "сохранена" in last_to(session, ADMIN) and botmod.access.note_of(STRANGER) == "Петя из гаража"
    assert ADMIN not in botmod.pending
    await say(ADMIN, "/note")
    await say(ADMIN, f"{STRANGER} Маша")
    assert botmod.access.note_of(STRANGER) == "Маша"
    await say(ADMIN, "/note")
    await say(ADMIN, "999")
    assert "нет в списке" in last_to(session, ADMIN) and ADMIN not in botmod.pending

    # /revoke: код или all
    await say(ADMIN, "/invite")
    code = invite_code(session)
    await say(ADMIN, "/revoke")
    assert "код инвайта" in last_to(session, ADMIN)
    await say(ADMIN, code)
    assert "отозван" in last_to(session, ADMIN) and botmod.access.redeem(code, 55) == "revoked"

    # Ожидание истекает: через 5 минут текст снова считается вопросом
    calls = botmod.pipeline.calls
    await say(ADMIN, "/kick")
    botmod.pending[ADMIN].created = time.time() - botmod.PENDING_TTL - 1
    await say(ADMIN, "какая горечь у IPA?")
    assert botmod.pipeline.calls == calls + 1

    # Обычный участник состояние админа не трогает и своё получить не может
    await say(ADMIN, "/kick")
    await say(STRANGER, "вопрос про стиль")
    assert ADMIN in botmod.pending and botmod.pipeline.calls == calls + 2
    await say(STRANGER, "/kick")
    assert "только администратору" in last_to(session, STRANGER) and STRANGER not in botmod.pending


# ---------- кнопки под пользователями и инвайтами ----------

async def user_buttons():
    session = new_world()
    session.known_users[STRANGER] = vasya()
    botmod.access.redeem(botmod.access.create_invite(ADMIN, 7, "Вася с работы"), STRANGER)
    botmod.access.redeem(botmod.access.create_invite(ADMIN, 7), 3)

    await say(ADMIN, "/users")
    cards = [r for r in session.records if r.chat_id == ADMIN and r.markup]
    assert len(cards) == 2  # по карточке с кнопками на каждого
    card = last_rec(session, ADMIN, "Вася с работы")
    assert card.buttons() == [["👤 Кто это", "✏️ Заметка"], ["⛔ Заблокировать"]]

    # «Кто это»
    await press(ADMIN, card, "Кто это")
    assert "@vasya" in last_to(session, ADMIN) and "Василий Пупкин" in last_to(session, ADMIN)

    # «Заметка» -> бот просит текст -> заметка сохранена
    await press(ADMIN, card, "Заметка")
    assert "текст заметки для 2" in last_to(session, ADMIN) and botmod.pending[ADMIN].user_id == STRANGER
    await say(ADMIN, "Вася-пивовар")
    assert botmod.access.note_of(STRANGER) == "Вася-пивовар"

    # «Заблокировать»: доступ закрыт, карточка перерисована, кнопка сменилась на «Разблокировать»
    await press(ADMIN, card, "Заблокировать")
    assert not botmod.access.is_member(STRANGER)
    assert "заблокирован" in card.text and card.buttons()[1] == ["✅ Разблокировать"]
    assert session.callback_answers[-1][0].startswith("Доступ заблокирован")
    await say(STRANGER, "вопрос")
    assert "по приглашениям" in last_to(session, STRANGER)

    # «Разблокировать»
    await press(ADMIN, card, "Разблокировать")
    assert botmod.access.is_member(STRANGER) and card.buttons()[1] == ["⛔ Заблокировать"]

    # Чужие кнопки не работают: ни участник, ни посторонний
    for intruder in (STRANGER, 99):
        await press(intruder, card, "Заблокировать")
        assert session.callback_answers[-1] == ("Только для администратора", True)
        assert botmod.access.is_member(STRANGER)

    # Пустой список
    botmod.access.db.execute("DELETE FROM users")
    await say(ADMIN, "/users")
    assert "пока нет" in last_to(session, ADMIN)


async def invite_buttons():
    session = new_world()
    await say(ADMIN, "/invites")
    assert "нет" in last_to(session, ADMIN)

    await say(ADMIN, "/invite 7 Для Маши")
    code1 = invite_code(session)
    await say(ADMIN, "/invite")
    code2 = invite_code(session)
    await say(ADMIN, "/invites")
    header = last_rec(session, ADMIN, "Действующих инвайтов: 2")
    row = last_rec(session, ADMIN, code1)
    assert "Для Маши" in row.text and row.buttons() == [["🚫 Отозвать"]]
    assert header.buttons() == [["🚫 Отозвать все"]]

    stale = row.callback_data("Отозвать")  # у другого клиента кнопка ещё может висеть
    await press(ADMIN, row, "Отозвать")
    assert row.text.startswith("🚫 Отозван") and botmod.access.redeem(code1, 55) == "revoked"
    await press(ADMIN, row, data=stale)  # повторное нажатие на устаревшую кнопку не падает
    assert "Уже недействителен" in row.text

    all_data = header.callback_data("Отозвать все")
    await press(99, header, data=all_data)  # посторонний: ничего не отозвано
    assert session.callback_answers[-1] == ("Только для администратора", True)
    assert botmod.access.active_invites()
    await press(ADMIN, header, "Отозвать все")
    assert "Отозвано инвайтов: 1" in header.text and botmod.access.redeem(code2, 56) == "revoked"

    await press(99, header, data=all_data)
    assert session.callback_answers[-1] == ("Только для администратора", True)


# ---------- бот сам отвечает на служебные фразы ----------

async def smalltalk_in_bot():
    session = new_world()
    botmod.access.redeem(botmod.access.create_invite(ADMIN, 7), STRANGER)

    for phrase in ("что ты умеешь?", "Как тобой пользоваться", "Кто ты?"):
        await say(STRANGER, phrase)
        assert last_to(session, STRANGER) == smalltalk.CAPABILITIES, phrase
    await say(STRANGER, "Привет!")
    assert last_to(session, STRANGER) == smalltalk.GREETING_REPLY
    assert botmod.pipeline.calls == 0  # ни одного токена на служебные фразы

    # «начнём заново» стирает историю разговора, как /clear
    botmod.history[STRANGER] = [{"role": "user", "content": "x"}]
    await say(STRANGER, "начнём заново")
    assert last_to(session, STRANGER) == smalltalk.RESET_REPLY and STRANGER not in botmod.history
    botmod.history[STRANGER] = [{"role": "user", "content": "x"}]
    await say(STRANGER, "/clear")
    assert STRANGER not in botmod.history

    # Настоящий вопрос всё равно идёт в RAG
    await say(STRANGER, "Что такое стаут?")
    assert botmod.pipeline.calls == 1

    # /help: участнику — без админской части, админу — с ней
    await say(STRANGER, "/help")
    assert "Администратор" not in last_to(session, STRANGER) and "/invite" not in last_to(session, STRANGER)
    await say(ADMIN, "/help")
    assert "Администратор" in last_to(session, ADMIN) and "/invite" in last_to(session, ADMIN)


def _run(coro):
    asyncio.run(coro)


def test_access_flow():
    _run(access_flow())


def test_command_menu():
    _run(menu())


def test_argument_prompts():
    _run(argument_prompts())


def test_user_buttons():
    _run(user_buttons())


def test_invite_buttons():
    _run(invite_buttons())


def test_smalltalk_in_bot():
    _run(smalltalk_in_bot())


if __name__ == "__main__":
    tests = [(k, v) for k, v in dict(globals()).items() if k.startswith("test_")]
    for name, t in tests:
        t()
        print("ok ", name)
    print(f"{len(tests)} сквозных тестов пройдено")
