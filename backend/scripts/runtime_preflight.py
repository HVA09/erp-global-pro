#!/usr/bin/env python3
"""Read-only runtime/deployment preflight for ERP Global Pro.

This command does not create directories, open the database, or modify application state.
It checks Python dependencies, security-sensitive environment variables, and path layout.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

REQUIRED_MODULES = {
    "flask": "Flask",
    "werkzeug": "Werkzeug",
    "jwt": "PyJWT",
    "openpyxl": "openpyxl",
    "reportlab": "reportlab",
}
OPTIONAL_MODULES = {"gunicorn": "gunicorn"}


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def _path_has_symlink(path: Path) -> bool:
    """Inspect existing path components without following a symlink component."""
    candidate = path.expanduser()
    parts = [candidate, *candidate.parents]
    return any(part.is_symlink() for part in parts if part.exists() or part.is_symlink())


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def run_preflight(env: dict[str, str] | None = None) -> dict:
    env = dict(os.environ if env is None else env)
    project_root = Path(__file__).resolve().parents[2]
    backend_root = project_root / "backend"
    environment = env.get("ERP_ENV", "development").strip().lower()
    production = environment == "production"

    checks: list[dict] = []
    blockers: list[str] = []
    warnings: list[str] = []

    for module, package in REQUIRED_MODULES.items():
        ok = _module_available(module)
        checks.append({"name": f"python:{package}", "ok": ok, "required": True})
        if not ok:
            blockers.append(f"Missing required Python dependency: {package}.")

    for module, package in OPTIONAL_MODULES.items():
        ok = _module_available(module)
        checks.append({"name": f"python:{package}", "ok": ok, "required": production})
        if production and not ok:
            blockers.append(f"Missing production WSGI dependency: {package}.")
        elif not ok:
            warnings.append(f"Optional package is not installed: {package}.")

    secret = env.get("ERP_SECRET_KEY", "")
    secret_ok = bool(secret) and len(secret) >= 32
    checks.append({"name": "production_secret", "ok": (secret_ok if production else True),
                   "required": production})
    if production and not secret_ok:
        blockers.append("ERP_SECRET_KEY must be configured with at least 32 characters in production.")

    db_raw = env.get("ERP_DB_PATH", str(backend_root / "instance" / "erp.sqlite3"))
    storage_raw = env.get("ERP_FILE_STORAGE_DIR", str(backend_root / "instance" / "files"))
    frontend_raw = env.get("ERP_FRONTEND_DIR", str(project_root / "frontend"))
    db_path = Path(db_raw).expanduser()
    storage_path = Path(storage_raw).expanduser()
    frontend_path = Path(frontend_raw).expanduser()
    if not db_path.is_absolute():
        db_path = (backend_root / db_path).resolve(strict=False)
    else:
        db_path = db_path.resolve(strict=False)
    if not storage_path.is_absolute():
        storage_path = (backend_root / storage_path).resolve(strict=False)
    else:
        storage_path = storage_path.resolve(strict=False)
    if not frontend_path.is_absolute():
        frontend_path = (project_root / frontend_path).resolve(strict=False)
    else:
        frontend_path = frontend_path.resolve(strict=False)

    storage_outside_frontend = not _is_inside(storage_path, frontend_path)
    checks.append({"name": "private_storage_outside_frontend", "ok": storage_outside_frontend,
                   "required": True})
    if not storage_outside_frontend:
        blockers.append("ERP_FILE_STORAGE_DIR must not be inside the frontend/static directory.")

    paths_do_not_overlap = not (_is_inside(db_path, storage_path) or _is_inside(storage_path, db_path)
                                or db_path == storage_path)
    checks.append({"name": "database_storage_paths_separate", "ok": paths_do_not_overlap,
                   "required": True})
    if not paths_do_not_overlap:
        blockers.append("ERP_DB_PATH and ERP_FILE_STORAGE_DIR must be separate non-overlapping paths.")

    for name, path in (("database_path_no_symlink", db_path), ("storage_path_no_symlink", storage_path),
                       ("frontend_path_no_symlink", frontend_path)):
        safe = not _path_has_symlink(path)
        checks.append({"name": name, "ok": safe, "required": True})
        if not safe:
            blockers.append(f"Unsafe symbolic-link path detected: {name}.")

    scanner = env.get("ERP_CLAMSCAN_BIN", "clamscan")
    scanner_path = shutil.which(scanner) if not Path(scanner).is_absolute() else scanner
    scanner_ok = bool(scanner_path and Path(scanner_path).is_file() and os.access(scanner_path, os.X_OK))
    checks.append({"name": "clamav_scanner", "ok": scanner_ok, "required": production})
    if production and not scanner_ok:
        blockers.append("ClamAV scanner executable is required for production file uploads.")
    elif not scanner_ok:
        warnings.append("ClamAV is unavailable; uploaded files must remain non-downloadable/pending.")

    maintenance = env.get("ERP_MAINTENANCE_MODE", "0").strip().lower() in {"1", "true", "yes"}
    checks.append({"name": "maintenance_mode_disabled", "ok": not maintenance, "required": True})
    if maintenance:
        blockers.append("ERP_MAINTENANCE_MODE is enabled; the service is intentionally unavailable for normal API traffic.")

    # Multi-company production is intentionally blocked until the PostgreSQL/RLS and full HTTP gates close.
    tenant_gate_closed = True  # Config.TENANT_DATA_SCOPING_ENABLED remains hard-coded False in this release.
    checks.append({"name": "multi_company_release_gate_closed", "ok": tenant_gate_closed, "required": False})
    warnings.append("Multi-company production is not approved: TENANT_DATA_SCOPING_ENABLED must remain False.")

    return {
        "project": "ERP Global Pro",
        "environment": environment,
        "status": "blocked" if blockers else "preflight_pass_with_warnings" if warnings else "pass",
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "paths": {"database": str(db_path), "storage": str(storage_path), "frontend": str(frontend_path)},
        "read_only": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()
    result = run_preflight()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"ERP Global Pro runtime preflight: {result['status']}")
        print(f"Environment: {result['environment']}")
        for check in result["checks"]:
            mark = "PASS" if check["ok"] else "BLOCK"
            print(f"[{mark}] {check['name']}")
        for item in result["blockers"]:
            print(f"BLOCKER: {item}")
        for item in result["warnings"]:
            print(f"WARNING: {item}")
    return 0 if not result["blockers"] else 2


if __name__ == "__main__":
    sys.exit(main())
