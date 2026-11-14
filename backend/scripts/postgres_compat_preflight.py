#!/usr/bin/env python3
"""Read-only PostgreSQL compatibility inventory for ERP Global Pro.

This is a source scanner, not a PostgreSQL validator. It deliberately reports
raw question-mark characters separately because a lexical count is not a
reliable count of DB-API bind parameters.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent

PATTERNS = {
    "pragma": re.compile(r"\bPRAGMA\b", re.IGNORECASE),
    "autoincrement": re.compile(r"\bAUTOINCREMENT\b", re.IGNORECASE),
    "sqlite_master": re.compile(r"\bsqlite_master\b", re.IGNORECASE),
    "sqlite3_reference": re.compile(r"\bsqlite3\b", re.IGNORECASE),
    "create_trigger": re.compile(r"\bCREATE\s+TRIGGER\b", re.IGNORECASE),
    "lastrowid": re.compile(r"\blastrowid\b", re.IGNORECASE),
    "begin_immediate": re.compile(r"\bBEGIN\s+IMMEDIATE\b", re.IGNORECASE),
    "insert_or_ignore_replace": re.compile(
        r"\bINSERT\s+OR\s+(?:IGNORE|REPLACE)\b", re.IGNORECASE
    ),
    "row_factory": re.compile(r"\brow_factory\b", re.IGNORECASE),
    "executescript": re.compile(r"\bexecutescript\b", re.IGNORECASE),
    "sqlite_specific_errors": re.compile(
        r"sqlite3\.(?:IntegrityError|OperationalError|DatabaseError)", re.IGNORECASE
    ),
}


def operational_files() -> list[Path]:
    """Return tracked-style operational source files, excluding tests/docs."""
    files: set[Path] = set()
    for directory in (BACKEND / "erp", BACKEND / "scripts"):
        if directory.exists():
            for pattern in ("*.py", "*.sql"):
                files.update(
                    p for p in directory.rglob(pattern)
                    if "__pycache__" not in p.parts and p.name != "postgres_compat_preflight.py"
                )
    for name in ("manage.py", "run.py"):
        candidate = BACKEND / name
        if candidate.exists():
            files.add(candidate)
    return sorted(files)


def _line_hits(path: Path, regex: re.Pattern[str]) -> list[dict[str, int]]:
    hits: list[dict[str, int]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        count = len(regex.findall(line))
        if count:
            hits.append({"line": line_number, "count": count})
    return hits


def build_report(root: Path | None = None) -> dict:
    global BACKEND
    original_backend = BACKEND
    if root is not None:
        BACKEND = root.resolve()
    try:
        files = operational_files()
        categories: dict[str, dict] = {}
        for category, regex in PATTERNS.items():
            matches = []
            total = 0
            for path in files:
                hits = _line_hits(path, regex)
                if hits:
                    count = sum(hit["count"] for hit in hits)
                    total += count
                    matches.append({
                        "file": path.relative_to(BACKEND).as_posix(),
                        "count": count,
                        "lines": hits,
                    })
            categories[category] = {"count": total, "files": matches}

        python_files = [p for p in files if p.suffix == ".py"]
        raw_qmarks = sum(
            path.read_text(encoding="utf-8", errors="replace").count("?")
            for path in python_files
        )
        migration_source = (BACKEND / "erp" / "migrations" / "__init__.py")
        migration_text = migration_source.read_text(encoding="utf-8", errors="replace") if migration_source.exists() else ""
        match = re.search(r"^LATEST_VERSION\s*=\s*(\d+)", migration_text, re.MULTILINE)
        schema_version = int(match.group(1)) if match else None
        return {
            "project": "ERP Global Pro",
            "classification": "static source inventory; not a PostgreSQL runtime test",
            "database_schema_version": schema_version,
            "operational_source_files_scanned": len(files),
            "python_source_files_scanned": len(python_files),
            "sqlite_specific_indicators": categories,
            "raw_question_mark_characters_in_python": raw_qmarks,
            "raw_question_mark_note": (
                "Lexical character count only. It may include question marks in comments, "
                "strings, SQL operators, or non-bind text. Do not mass-replace it."
            ),
            "postgresql_runtime_tests": 0,
            "decision": "PostgreSQL is not enabled; implement and test a complete adapter and migrations first.",
        }
    finally:
        BACKEND = original_backend


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write JSON report to this path")
    args = parser.parse_args(argv)
    report = build_report()
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
