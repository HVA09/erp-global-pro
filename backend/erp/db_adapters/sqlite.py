"""SQLite adapter preserving the application's current DB-API semantics."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class SQLiteAdapter:
    name = "sqlite"
    supports_online_backup = True
    supports_sql_script = True

    def connect(
        self,
        database: str,
        *,
        timeout: float = 30,
        check_same_thread: bool = False,
        isolation_level: str | None = None,
    ) -> sqlite3.Connection:
        connection = sqlite3.connect(
            database,
            isolation_level=isolation_level,
            timeout=timeout,
            check_same_thread=check_same_thread,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        if database != ":memory:":
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def execute(self, connection: sqlite3.Connection, sql: str, params: Any = ()) -> sqlite3.Cursor:
        return connection.execute(sql, params)

    def executemany(self, connection: sqlite3.Connection, sql: str, rows: Any) -> sqlite3.Cursor:
        return connection.executemany(sql, rows)

    def executescript(self, connection: sqlite3.Connection, script: str) -> None:
        connection.executescript(script)

    def begin(self, connection: sqlite3.Connection, *, write: bool = False) -> None:
        connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")

    def commit(self, connection: sqlite3.Connection) -> None:
        connection.commit()

    def rollback(self, connection: sqlite3.Connection) -> None:
        connection.rollback()

    def in_transaction(self, connection: sqlite3.Connection) -> bool:
        return connection.in_transaction

    def last_insert_id(self, cursor: sqlite3.Cursor) -> int | None:
        return cursor.lastrowid

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        if not isinstance(identifier, str) or not identifier or "\x00" in identifier:
            raise ValueError("Invalid SQL identifier")
        return '"' + identifier.replace('"', '""') + '"'

    def table_columns(self, connection: sqlite3.Connection, table: str) -> set[str]:
        quoted = self._quote_identifier(table)
        return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({quoted})")}

    def close(self, connection: sqlite3.Connection) -> None:
        connection.close()

    def integrity_check(self, connection: sqlite3.Connection) -> list[str]:
        return [str(row[0]) for row in connection.execute("PRAGMA integrity_check").fetchall()]

    def foreign_key_violations(self, connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
        return [tuple(row) for row in connection.execute("PRAGMA foreign_key_check").fetchall()]

    def backup_to_path(self, connection: sqlite3.Connection, target_path: str | Path) -> None:
        """Create a consistent SQLite backup using the native online backup API."""
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.is_symlink():
            raise ValueError("Backup target must not be a symlink")
        destination = sqlite3.connect(str(target), timeout=30)
        try:
            connection.backup(destination)
        finally:
            destination.close()


SQLITE_ADAPTER = SQLiteAdapter()
