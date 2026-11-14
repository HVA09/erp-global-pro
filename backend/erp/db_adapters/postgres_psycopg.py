"""Experimental PostgreSQL adapter spike using Psycopg 3.

This is intentionally NOT wired into the production app. The current schema,
migrations, backup/restore, and a number of direct sqlite3 call sites remain
SQLite-specific. The adapter exists to validate a narrow DB-API contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Sequence


class PlaceholderMismatch(ValueError):
    """Raised when legacy qmark SQL cannot be translated without ambiguity."""


class HybridRow:
    """SQLite-Row-like row supporting integer and name lookup plus dict(row)."""

    __slots__ = ("_names", "_values", "_index")

    def __init__(self, names: Sequence[str], values: Sequence[Any]):
        if len(names) != len(values):
            raise ValueError("Row names and values length mismatch")
        self._names = tuple(str(name) for name in names)
        self._values = tuple(values)
        self._index: dict[str, int] = {}
        for index, name in enumerate(self._names):
            self._index.setdefault(name, index)

    def keys(self) -> tuple[str, ...]:
        return self._names

    def __getitem__(self, key: int | slice | str) -> Any:
        if isinstance(key, str):
            try:
                key = self._index[key]
            except KeyError:
                raise IndexError(key) from None
        return self._values[key]

    def __iter__(self):
        # sqlite3.Row iterates over values, while dict(row) uses keys().
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        pairs = ", ".join(f"{name}={value!r}" for name, value in zip(self._names, self._values))
        return f"HybridRow({pairs})"


def hybrid_row_factory(cursor: Any):
    """Psycopg row factory preserving both row[0] and row['column'] access."""
    description = cursor.description or ()
    names = tuple(
        column.name if hasattr(column, "name") else column[0]
        for column in description
    )

    def make_row(values: Sequence[Any]) -> HybridRow:
        return HybridRow(names, values)

    return make_row


def _is_escape_string_prefix(sql: str, quote_index: int) -> bool:
    """Whether a quote is prefixed by PostgreSQL E/e without an identifier."""
    if quote_index <= 0 or sql[quote_index - 1] not in "eE":
        return False
    prefix_index = quote_index - 2
    return prefix_index < 0 or not (sql[prefix_index].isalnum() or sql[prefix_index] in "_$")


def translate_qmark_sql(sql: str, params: Any = ()) -> str:
    """Translate legacy SQLite qmark placeholders to Psycopg %s safely.

    The scanner ignores question marks in quoted strings/identifiers, comments,
    and dollar-quoted bodies. It refuses to run when the count of bare qmarks
    differs from the supplied positional parameter count. This deliberately
    rejects ambiguous PostgreSQL JSON '?' operators; rewrite those expressions
    with jsonb_exists()/jsonb_exists_any() before enabling PostgreSQL.

    If parameters are bound, literal percent signs are doubled for Psycopg's
    pyformat parser. Parameter values are never interpolated into SQL text.
    """
    if not isinstance(sql, str):
        raise TypeError("SQL statement must be a string")
    if params is None:
        params = ()
    if isinstance(params, (str, bytes, bytearray, dict)):
        raise TypeError("Psycopg adapter spike accepts positional parameter sequences only")
    try:
        expected = len(params)
    except TypeError as exc:
        raise TypeError("SQL parameters must be a sized positional sequence") from exc

    out: list[str] = []
    qmarks = 0
    i = 0
    n = len(sql)
    state = "normal"
    block_depth = 0
    dollar_tag = ""
    escape_string = False

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if state == "normal":
            if ch == "'":
                escape_string = _is_escape_string_prefix(sql, i)
                state = "single"
                out.append(ch)
                i += 1
                continue
            if ch == '"':
                state = "double"
                out.append(ch)
                i += 1
                continue
            if ch == "-" and nxt == "-":
                state = "line_comment"
                out.append("--")
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block_comment"
                block_depth = 1
                out.append("/*")
                i += 2
                continue
            if ch == "$":
                match = re.match(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$", sql[i:])
                if match:
                    dollar_tag = match.group(0)
                    state = "dollar"
                    out.append(dollar_tag)
                    i += len(dollar_tag)
                    continue
            if ch == "?":
                qmarks += 1
                out.append("%s")
                i += 1
                continue
            if expected and ch == "%":
                out.append("%%")
            else:
                out.append(ch)
            i += 1
            continue

        if state == "single":
            out.append("%%" if expected and ch == "%" else ch)
            if escape_string and ch == "\\" and i + 1 < n:
                out.append(sql[i + 1])
                i += 2
                continue
            if ch == "'":
                if nxt == "'":
                    out.append(nxt)
                    i += 2
                    continue
                state = "normal"
            i += 1
            continue

        if state == "double":
            out.append("%%" if expected and ch == "%" else ch)
            if ch == '"':
                if nxt == '"':
                    out.append(nxt)
                    i += 2
                    continue
                state = "normal"
            i += 1
            continue

        if state == "line_comment":
            out.append("%%" if expected and ch == "%" else ch)
            if ch in "\r\n":
                state = "normal"
            i += 1
            continue

        if state == "block_comment":
            if ch == "/" and nxt == "*":
                block_depth += 1
                out.append("/*")
                i += 2
                continue
            if ch == "*" and nxt == "/":
                block_depth -= 1
                out.append("*/")
                i += 2
                if block_depth == 0:
                    state = "normal"
                continue
            out.append("%%" if expected and ch == "%" else ch)
            i += 1
            continue

        if state == "dollar":
            if sql.startswith(dollar_tag, i):
                out.append(dollar_tag)
                i += len(dollar_tag)
                state = "normal"
                dollar_tag = ""
                continue
            out.append("%%" if expected and ch == "%" else ch)
            i += 1
            continue

    if state in {"single", "double", "block_comment", "dollar"}:
        raise ValueError(f"Unterminated SQL lexical construct: {state}")
    if qmarks != expected:
        raise PlaceholderMismatch(
            f"Found {qmarks} unquoted qmark placeholders but received {expected} parameters; "
            "query may contain a PostgreSQL '?' operator or mismatched bindings"
        )
    return "".join(out)


@dataclass
class PsycopgCursorProxy:
    """Small cursor wrapper carrying explicit INSERT RETURNING metadata."""

    cursor: Any
    returns_id: bool = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.cursor, name)

    def __iter__(self):
        return iter(self.cursor)


class Psycopg3Adapter:
    """Experimental blocking DB-API adapter; not selected by app config."""

    name = "postgres-psycopg3-spike"
    supports_online_backup = False
    supports_sql_script = False

    def connect(self, database: str, **options: Any) -> Any:
        # Ignore SQLite-only knobs passed by the current request DB factory.
        check_same_thread = options.pop("check_same_thread", None)
        isolation_level = options.pop("isolation_level", None)
        timeout = options.pop("timeout", None)
        autocommit = options.pop("autocommit", True)
        if isolation_level is not None:
            raise ValueError("SQLite isolation_level is not portable; configure PostgreSQL autocommit explicitly")
        if timeout is not None:
            options.setdefault("connect_timeout", max(1, int(timeout)))
        del check_same_thread

        try:
            import psycopg  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL prototype requires the optional dependency in requirements-postgres.txt"
            ) from exc

        return psycopg.connect(
            database,
            autocommit=autocommit,
            row_factory=hybrid_row_factory,
            **options,
        )

    def execute(self, connection: Any, sql: str, params: Any = ()) -> PsycopgCursorProxy:
        supplied = () if params is None else params
        translated = translate_qmark_sql(sql, supplied)
        cursor = connection.cursor()
        if len(supplied):
            cursor.execute(translated, supplied)
        else:
            cursor.execute(translated)
        returns_id = bool(re.match(r"\s*INSERT\b", sql, re.IGNORECASE)) and bool(
            re.search(r"\bRETURNING\s+id\b", sql, re.IGNORECASE)
        )
        return PsycopgCursorProxy(cursor=cursor, returns_id=returns_id)

    def executemany(self, connection: Any, sql: str, rows: Iterable[Sequence[Any]]) -> PsycopgCursorProxy:
        materialized = list(rows)
        if not materialized:
            return PsycopgCursorProxy(cursor=connection.cursor(), returns_id=False)
        translated = translate_qmark_sql(sql, materialized[0])
        expected = len(materialized[0])
        if any(len(row) != expected for row in materialized[1:]):
            raise PlaceholderMismatch("executemany rows have inconsistent parameter counts")

        cursor = connection.cursor()
        cursor.executemany(translated, materialized)
        return PsycopgCursorProxy(cursor=cursor, returns_id=False)

    def executescript(self, connection: Any, script: str) -> None:
        raise NotImplementedError(
            "PostgreSQL requires versioned migrations; generic executescript is intentionally unsupported"
        )

    def begin(self, connection: Any, *, write: bool = False) -> None:
        # SQLite's BEGIN IMMEDIATE is a database-wide writer reservation. A
        # plain PostgreSQL BEGIN does not provide the same semantics, so fail
        # closed until call sites adopt PostgreSQL row/advisory locking.
        if write:
            raise NotImplementedError(
                "PostgreSQL write-lock semantics are not mapped from SQLite BEGIN IMMEDIATE"
            )
        self.execute(connection, "BEGIN")

    def commit(self, connection: Any) -> None:
        connection.commit()

    def rollback(self, connection: Any) -> None:
        connection.rollback()

    def in_transaction(self, connection: Any) -> bool:
        status = connection.info.transaction_status
        if isinstance(status, str):
            return status.upper() != "IDLE"
        name = getattr(status, "name", None)
        if name is not None:
            return name.upper() != "IDLE"
        return bool(status)

    def last_insert_id(self, cursor: PsycopgCursorProxy) -> int | None:
        if not cursor.returns_id:
            return None
        row = cursor.fetchone()
        return int(row[0]) if row is not None else None

    def table_columns(self, connection: Any, table: str) -> set[str]:
        cursor = self.execute(
            connection,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ? "
            "ORDER BY ordinal_position",
            (table,),
        )
        return {str(row[0]) for row in cursor.fetchall()}

    def close(self, connection: Any) -> None:
        connection.close()
