"""Тесты контроля доступа. Запуск: python tests/test_access.py (или pytest)."""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from access import AccessStore  # noqa: E402


def make_store() -> AccessStore:
    return AccessStore(Path(tempfile.mkdtemp()) / "access.db")


def test_redeem_ok_and_membership():
    s = make_store()
    code = s.create_invite(created_by=1)
    assert not s.is_member(42)
    assert s.redeem(code, 42, "vasya") == "ok"
    assert s.is_member(42)


def test_single_use_invite_is_consumed():
    s = make_store()
    code = s.create_invite(1, max_uses=1)
    assert s.redeem(code, 10, None) == "ok"
    assert s.redeem(code, 11, None) == "used_up"
    assert not s.is_member(11)


def test_multi_use_invite():
    s = make_store()
    code = s.create_invite(1, max_uses=2)
    assert [s.redeem(code, u, None) for u in (10, 11, 12)] == ["ok", "ok", "used_up"]


def test_member_redeeming_again_does_not_burn_a_use():
    s = make_store()
    code = s.create_invite(1, max_uses=2)
    assert s.redeem(code, 10, None) == "ok"
    assert s.redeem(code, 10, None) == "already"
    assert s.redeem(code, 11, None) == "ok"


def test_unknown_code_is_invalid():
    s = make_store()
    assert s.redeem("nope", 10, None) == "not_found"
    assert not s.is_member(10)


def test_expired_invite_is_invalid():
    s = make_store()
    code = s.create_invite(1)
    s.db.execute("UPDATE invites SET expires_at = ? WHERE code = ?", (int(time.time()) - 1, code))
    s.db.commit()
    assert s.redeem(code, 10, None) == "expired"
    assert s.active_invites() == []


def test_revoked_invite_is_invalid():
    s = make_store()
    code = s.create_invite(1)
    assert s.revoke(code)
    assert not s.revoke(code)  # повторный отзыв ничего не меняет
    assert s.redeem(code, 10, None) == "revoked"


def test_block_cuts_access_and_blocks_rejoin():
    s = make_store()
    code = s.create_invite(1, max_uses=5)
    s.redeem(code, 10, "a")
    assert s.block_user(10)
    assert not s.is_member(10)
    assert not s.block_user(10)
    # главное: исключённый не возвращается по тому же многоразовому коду
    assert s.redeem(code, 10, "a") == "blocked"
    assert not s.is_member(10)
    assert s.unblock_user(10) and s.is_member(10)


def test_blocked_attempt_does_not_burn_invite():
    s = make_store()
    code = s.create_invite(1, max_uses=1)
    s.redeem(s.create_invite(1), 10, "a")
    s.block_user(10)
    assert s.redeem(code, 10, "a") == "blocked"
    assert s.redeem(code, 11, "b") == "ok"


def test_revoke_all():
    s = make_store()
    for _ in range(3):
        s.create_invite(1)
    assert s.revoke_all() == 3
    assert s.active_invites() == []
    assert s.revoke_all() == 0


def test_activity_counters():
    s = make_store()
    s.redeem(s.create_invite(1), 10, "old")
    s.touch(10, "new")
    s.count_question(10)
    s.count_question(10)
    (u,) = s.users()
    assert (u.username, u.questions, u.blocked) == ("new", 2, False)
    assert u.last_seen is not None


def test_migration_from_first_schema():
    """База, созданная первой версией (без last_seen/questions/blocked), должна открываться."""
    import sqlite3
    path = Path(tempfile.mkdtemp()) / "access.db"
    old = sqlite3.connect(str(path))
    old.executescript("""
        CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT, joined_at INTEGER NOT NULL, invite TEXT);
        CREATE TABLE invites (code TEXT PRIMARY KEY, created_by INTEGER NOT NULL, created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL, max_uses INTEGER NOT NULL, uses INTEGER NOT NULL DEFAULT 0,
            revoked INTEGER NOT NULL DEFAULT 0);
        INSERT INTO users VALUES (7, 'legacy', 1, 'x');
    """)
    old.commit()
    old.close()
    s = AccessStore(path)
    assert s.is_member(7)
    s.count_question(7)
    assert s.users()[0].questions == 1


def test_state_survives_reopen():
    path = Path(tempfile.mkdtemp()) / "access.db"
    s = AccessStore(path)
    s.redeem(s.create_invite(1), 10, "a")
    assert AccessStore(path).is_member(10)


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} тестов пройдено")
