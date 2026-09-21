"""
Контроль доступа по инвайтам (SQLite).

Админы (ADMIN_IDS) имеют доступ всегда. Остальные попадают в список, только
погасив действующий инвайт-код. Хранилище живёт в отдельном файле и не
зависит от индекса, поэтому переживает переиндексацию.
"""

import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id   INTEGER PRIMARY KEY,
    username  TEXT,
    joined_at INTEGER NOT NULL,
    invite    TEXT
);
CREATE TABLE IF NOT EXISTS invites (
    code       TEXT PRIMARY KEY,
    created_by INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    max_uses   INTEGER NOT NULL,
    uses       INTEGER NOT NULL DEFAULT 0,
    revoked    INTEGER NOT NULL DEFAULT 0
);
"""

DAY = 86400


@dataclass
class Invite:
    code: str
    expires_at: int
    max_uses: int
    uses: int


@dataclass
class User:
    user_id: int
    username: Optional[str]
    joined_at: int


class AccessStore:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def is_member(self, user_id: int) -> bool:
        row = self.db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None

    def create_invite(self, created_by: int, max_uses: int = 1, days: int = 7) -> str:
        code = secrets.token_urlsafe(8)  # ~64 бита; символы безопасны для deep link
        now = int(time.time())
        self.db.execute(
            "INSERT INTO invites (code, created_by, created_at, expires_at, max_uses) VALUES (?,?,?,?,?)",
            (code, created_by, now, now + days * DAY, max_uses),
        )
        self.db.commit()
        return code

    def redeem(self, code: str, user_id: int, username: Optional[str]) -> str:
        """Возвращает 'ok', 'already' (уже в списке, инвайт не тратится) или 'invalid'."""
        if self.is_member(user_id):
            return "already"
        now = int(time.time())
        # Один атомарный UPDATE: проверка лимита, срока и отзыва + списание использования
        cur = self.db.execute(
            "UPDATE invites SET uses = uses + 1 "
            "WHERE code = ? AND revoked = 0 AND uses < max_uses AND expires_at > ?",
            (code, now),
        )
        if cur.rowcount != 1:
            return "invalid"
        self.db.execute(
            "INSERT INTO users (user_id, username, joined_at, invite) VALUES (?,?,?,?)",
            (user_id, username, now, code),
        )
        self.db.commit()
        return "ok"

    def active_invites(self) -> List[Invite]:
        rows = self.db.execute(
            "SELECT code, expires_at, max_uses, uses FROM invites "
            "WHERE revoked = 0 AND uses < max_uses AND expires_at > ? ORDER BY created_at",
            (int(time.time()),),
        ).fetchall()
        return [Invite(*r) for r in rows]

    def revoke(self, code: str) -> bool:
        cur = self.db.execute("UPDATE invites SET revoked = 1 WHERE code = ? AND revoked = 0", (code,))
        self.db.commit()
        return cur.rowcount == 1

    def users(self) -> List[User]:
        rows = self.db.execute("SELECT user_id, username, joined_at FROM users ORDER BY joined_at").fetchall()
        return [User(*r) for r in rows]

    def remove_user(self, user_id: int) -> bool:
        cur = self.db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        self.db.commit()
        return cur.rowcount == 1
