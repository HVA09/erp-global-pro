"""Minimal database adapter contract for ERP persistence operations.

The contract describes capabilities used by the request-scoped DB layer. It is
not a promise that the current SQL/schema/migrations are portable: migration,
backup, and platform-specific administration remain explicit capabilities.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DatabaseAdapter(Protocol):
    """Structural contract implemented by a concrete database adapter."""

    name: str
    supports_online_backup: bool
    supports_sql_script: bool

    def connect(self, database: str, **options: Any) -> Any: ...

    def execute(self, connection: Any, sql: str, params: Any = ()) -> Any: ...

    def executemany(self, connection: Any, sql: str, rows: Any) -> Any: ...

    def executescript(self, connection: Any, script: str) -> None: ...

    def begin(self, connection: Any, *, write: bool = False) -> None: ...

    def commit(self, connection: Any) -> None: ...

    def rollback(self, connection: Any) -> None: ...

    def in_transaction(self, connection: Any) -> bool: ...

    def last_insert_id(self, cursor: Any) -> int | None: ...

    def table_columns(self, connection: Any, table: str) -> set[str]: ...

    def close(self, connection: Any) -> None: ...
