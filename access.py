"""
Контроль доступа по инвайтам (SQLite).

Админы (ADMIN_IDS) имеют доступ всегда. Остальные попадают в список, только
погасив действующий инвайт-код. Хранилище живёт в отдельном файле и не
зависит от индекса, поэтому переживает переиндексацию.

Все операции, меняющие состав пользователей или инвайтов, идут в транзакции
BEGIN IMMEDIATE: блокировка записи берётся сразу, поэтому даже два процесса
(например, старый и новый экземпляр бота при рестарте) не могут одновременно
погасить один код или создать дубль пользователя.
"""

import logging
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id   INTEGER PRIMARY KEY,   -- единственные данные о человеке: его Telegram ID
    joined_at INTEGER NOT NULL,
    invite    TEXT,
    note      TEXT NOT NULL DEFAULT '',  -- заметка админа из инвайта («Вася с работы»)
    blocked   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS invites (
    code       TEXT PRIMARY KEY,
    created_by INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    max_uses   INTEGER NOT NULL,
    uses       INTEGER NOT NULL DEFAULT 0,
    revoked    INTEGER NOT NULL DEFAULT 0,
    note       TEXT NOT NULL DEFAULT ''
);
"""

# Колонки, добавленные после первой версии схемы: для баз, созданных раньше
MIGRATIONS = {
    "users": {
        "note": "TEXT NOT NULL DEFAULT ''",
        "blocked": "INTEGER NOT NULL DEFAULT 0",
    },
    "invites": {
        "note": "TEXT NOT NULL DEFAULT ''",
    },
}

DAY = 86400

# Результаты redeem(). Пользователю показываем одно и то же для всех неудач,
# а причина нужна логам и админу (по ней видно перебор и повторное использование).
OK, ALREADY = "ok", "already"
FAILURES = ("not_found", "used_up", "expired", "revoked", "blocked", "busy")


@dataclass
class Invite:
    code: str
    expires_at: int
    note: str


@dataclass
class User:
    user_id: int
    joined_at: int
    note: str
    blocked: bool


class AccessStore:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: транзакциями управляем сами (см. _tx);
        # timeout: другой процесс, держащий запись, подождёт, а не упадёт сразу
        self.db = sqlite3.connect(str(path), timeout=10, isolation_level=None)
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        for table, columns in MIGRATIONS.items():
            existing = {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}
            for name, ddl in columns.items():
                if name not in existing:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                    logger.info(f"Миграция: {table}.{name} добавлена")

    @contextmanager
    def _tx(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    # ---------- проверка доступа ----------

    def is_member(self, user_id: int) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM users WHERE user_id = ? AND blocked = 0", (user_id,)
        ).fetchone()
        return row is not None

    # ---------- инвайты ----------

    def create_invite(self, created_by: int, days: int = 7, note: str = "") -> str:
        """Один инвайт — один вход одного пользователя (max_uses в схеме всегда 1)."""
        code = secrets.token_urlsafe(8)  # ~64 бита; символы безопасны для deep link
        now = int(time.time())
        self.db.execute(
            "INSERT INTO invites (code, created_by, created_at, expires_at, max_uses, note) VALUES (?,?,?,?,?,?)",
            (code, created_by, now, now + days * DAY, 1, note),
        )
        return code

    def redeem(self, code: str, user_id: int) -> str:
        """
        Гасит инвайт. Возвращает OK, ALREADY (уже участник — инвайт не тратится,
        чтобы админ мог проверить собственную ссылку) либо причину отказа из FAILURES.
        Заблокированному инвайт тоже не тратится: его можно выдать другому.
        """
        now = int(time.time())
        try:
            with self._tx():
                # Проверки и списание в одной транзакции: между ними никто не вклинится
                user = self.db.execute("SELECT blocked FROM users WHERE user_id = ?", (user_id,)).fetchone()
                if user is not None:
                    return "blocked" if user[0] else ALREADY

                cur = self.db.execute(
                    "UPDATE invites SET uses = uses + 1 "
                    "WHERE code = ? AND revoked = 0 AND uses < max_uses AND expires_at > ?",
                    (code, now),
                )
                if cur.rowcount != 1:
                    return self._why_failed(code, now)

                self.db.execute(
                    "INSERT INTO users (user_id, joined_at, invite, note) "
                    "SELECT ?, ?, code, note FROM invites WHERE code = ?",
                    (user_id, now, code),
                )
                return OK
        except sqlite3.OperationalError as e:  # база занята дольше timeout
            logger.warning(f"redeem: база занята: {e}")
            return "busy"

    def _why_failed(self, code: str, now: int) -> str:
        row = self.db.execute(
            "SELECT revoked, uses, max_uses, expires_at FROM invites WHERE code = ?", (code,)
        ).fetchone()
        if row is None:
            return "not_found"
        revoked, uses, max_uses, expires_at = row
        if revoked:
            return "revoked"
        if uses >= max_uses:
            return "used_up"
        return "expired"

    def active_invites(self) -> List[Invite]:
        rows = self.db.execute(
            "SELECT code, expires_at, note FROM invites "
            "WHERE revoked = 0 AND uses < max_uses AND expires_at > ? ORDER BY created_at",
            (int(time.time()),),
        ).fetchall()
        return [Invite(*r) for r in rows]

    def revoke(self, code: str) -> bool:
        cur = self.db.execute("UPDATE invites SET revoked = 1 WHERE code = ? AND revoked = 0", (code,))
        return cur.rowcount == 1

    def revoke_all(self) -> int:
        """Отзывает все действующие инвайты (например, после тестов). Возвращает их число."""
        cur = self.db.execute(
            "UPDATE invites SET revoked = 1 WHERE revoked = 0 AND uses < max_uses AND expires_at > ?",
            (int(time.time()),),
        )
        return cur.rowcount

    # ---------- пользователи ----------

    def users(self) -> List[User]:
        rows = self.db.execute(
            "SELECT user_id, joined_at, note, blocked FROM users ORDER BY joined_at"
        ).fetchall()
        return [User(r[0], r[1], r[2], bool(r[3])) for r in rows]

    def note_of(self, user_id: int) -> str:
        row = self.db.execute("SELECT note FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row[0] if row else ""

    def block_user(self, user_id: int) -> bool:
        """
        Блокирует, а не удаляет: заблокированный не сможет вернуться по новому инвайту,
        пока админ не сделает /unblock.
        """
        cur = self.db.execute("UPDATE users SET blocked = 1 WHERE user_id = ? AND blocked = 0", (user_id,))
        return cur.rowcount == 1

    def unblock_user(self, user_id: int) -> bool:
        cur = self.db.execute("UPDATE users SET blocked = 0 WHERE user_id = ? AND blocked = 1", (user_id,))
        return cur.rowcount == 1
