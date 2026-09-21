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
    assert s.redeem(code, 11, None) == "invalid"
    assert not s.is_member(11)


def test_multi_use_invite():
    s = make_store()
    code = s.create_invite(1, max_uses=2)
    assert [s.redeem(code, u, None) for u in (10, 11, 12)] == ["ok", "ok", "invalid"]


def test_member_redeeming_again_does_not_burn_a_use():
    s = make_store()
    code = s.create_invite(1, max_uses=2)
    assert s.redeem(code, 10, None) == "ok"
    assert s.redeem(code, 10, None) == "already"
    assert s.redeem(code, 11, None) == "ok"


def test_unknown_code_is_invalid():
    s = make_store()
    assert s.redeem("nope", 10, None) == "invalid"
    assert not s.is_member(10)


def test_expired_invite_is_invalid():
    s = make_store()
    code = s.create_invite(1)
    s.db.execute("UPDATE invites SET expires_at = ? WHERE code = ?", (int(time.time()) - 1, code))
    s.db.commit()
    assert s.redeem(code, 10, None) == "invalid"
    assert s.active_invites() == []


def test_revoked_invite_is_invalid():
    s = make_store()
    code = s.create_invite(1)
    assert s.revoke(code)
    assert not s.revoke(code)  # повторный отзыв ничего не меняет
    assert s.redeem(code, 10, None) == "invalid"


def test_remove_user_cuts_access():
    s = make_store()
    s.redeem(s.create_invite(1), 10, "a")
    assert s.remove_user(10)
    assert not s.is_member(10)
    assert not s.remove_user(10)


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
