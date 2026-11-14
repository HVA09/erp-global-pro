"""Small PostgreSQL migration-template utilities used by Stage 23 prototypes."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")


def render_sql_template(template: str, values: Mapping[str, str]) -> str:
    """Render only validated unquoted SQL identifiers into a migration template."""
    if not isinstance(template, str):
        raise TypeError("SQL template must be a string")
    for key, value in values.items():
        if not _IDENTIFIER.fullmatch(str(value)):
            raise ValueError(f"Unsafe SQL identifier for {key}")
    expected = set(_PLACEHOLDER.findall(template))
    supplied = set(values)
    if expected != supplied:
        raise ValueError(
            f"SQL template placeholders mismatch: expected={sorted(expected)}, supplied={sorted(supplied)}"
        )
    rendered = _PLACEHOLDER.sub(lambda match: values[match.group(1)], template)
    if _PLACEHOLDER.search(rendered):
        raise ValueError("Unresolved SQL template placeholder")
    return rendered


def split_sql_statements(script: str) -> list[str]:
    """Split SQL at top-level semicolons, preserving quoted and dollar-quoted bodies.

    Supports single/double quoted strings, doubled quote escapes, line comments,
    nested block comments, and PostgreSQL dollar-quoted function bodies.
    """
    if not isinstance(script, str):
        raise TypeError("SQL script must be text")
    statements: list[str] = []
    start = 0
    i = 0
    n = len(script)
    state = "normal"
    block_depth = 0
    dollar_tag = ""

    while i < n:
        ch = script[i]
        nxt = script[i + 1] if i + 1 < n else ""
        if state == "normal":
            if ch == "'":
                state = "single"
                i += 1
                continue
            if ch == '"':
                state = "double"
                i += 1
                continue
            if ch == "-" and nxt == "-":
                state = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block_comment"
                block_depth = 1
                i += 2
                continue
            if ch == "$":
                match = re.match(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$", script[i:])
                if match:
                    dollar_tag = match.group(0)
                    state = "dollar"
                    i += len(dollar_tag)
                    continue
            if ch == ";":
                chunk = script[start:i].strip()
                if chunk and not all(line.lstrip().startswith("--") for line in chunk.splitlines() if line.strip()):
                    statements.append(chunk)
                start = i + 1
            i += 1
            continue

        if state == "single":
            if ch == "'":
                if nxt == "'":
                    i += 2
                    continue
                state = "normal"
            i += 1
            continue

        if state == "double":
            if ch == '"':
                if nxt == '"':
                    i += 2
                    continue
                state = "normal"
            i += 1
            continue

        if state == "line_comment":
            if ch in "\r\n":
                state = "normal"
            i += 1
            continue

        if state == "block_comment":
            if ch == "/" and nxt == "*":
                block_depth += 1
                i += 2
                continue
            if ch == "*" and nxt == "/":
                block_depth -= 1
                i += 2
                if block_depth == 0:
                    state = "normal"
                continue
            i += 1
            continue

        if state == "dollar":
            if script.startswith(dollar_tag, i):
                i += len(dollar_tag)
                state = "normal"
                dollar_tag = ""
                continue
            i += 1
            continue

    if state in {"single", "double", "block_comment", "dollar"}:
        raise ValueError(f"Unterminated SQL lexical construct: {state}")
    tail = script[start:].strip()
    if tail and not all(line.lstrip().startswith("--") for line in tail.splitlines() if line.strip()):
        statements.append(tail)
    return statements


def render_signed_tenant_context_migration(
    *,
    security_schema: str,
    crypto_schema: str,
    security_owner_role: str,
    app_role: str,
    control_schema: str = "public",
) -> str:
    """Render the reviewed Stage 23 SQL prototype with validated identifiers.

    The control schema must contain the authoritative ``companies`` and
    ``company_memberships`` tables. The application role must not be able to
    mutate these tables directly; use separately reviewed privileged routines.
    """
    path = Path(__file__).with_name("postgres_signed_tenant_context.sql")
    template = path.read_text(encoding="utf-8")
    values = {
        "SECURITY_SCHEMA": security_schema,
        "CRYPTO_SCHEMA": crypto_schema,
        "SECURITY_OWNER_ROLE": security_owner_role,
        "APP_ROLE": app_role,
        "CONTROL_SCHEMA": control_schema,
    }
    return render_sql_template(template, values)
