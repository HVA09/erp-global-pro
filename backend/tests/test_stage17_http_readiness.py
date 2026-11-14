"""Dependency-free route registration and HTTP security readiness checks."""
import ast
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
ERP = ROOT / "erp"
sys.path.insert(0, str(ROOT / "scripts"))
from runtime_preflight import run_preflight  # noqa: E402


class Stage17HttpReadinessTests(unittest.TestCase):
    def _tree(self, path):
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_all_api_modules_are_registered(self):
        tree = self._tree(ERP / "api" / "__init__.py")
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is None and node.level == 1:
                imported.update(alias.name for alias in node.names)
        expected = {"accounting", "admin", "auth", "data", "file_objects", "hr", "inventory", "ops", "reports", "trade"}
        self.assertTrue(expected.issubset(imported))
        self.assertEqual(expected, imported)

    def test_all_api_modules_have_a_register_function(self):
        modules = ("accounting", "admin", "auth", "data", "file_objects", "hr", "inventory", "ops", "reports", "trade")
        for module in modules:
            with self.subTest(module=module):
                tree = self._tree(ERP / "api" / f"{module}.py")
                register = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and node.name == "register"]
                self.assertEqual(len(register), 1)
                self.assertTrue(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                                    and node.func.attr == "register_blueprint"
                                    for node in ast.walk(register[0])))

    def test_app_factory_has_global_api_authentication_guard(self):
        tree = self._tree(ERP / "__init__.py")
        functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_guard"]
        self.assertEqual(len(functions), 1)
        calls = [node for node in ast.walk(functions[0]) if isinstance(node, ast.Call)]
        self.assertTrue(any(isinstance(c.func, ast.Attribute) and c.func.attr == "load_user" for c in calls))
        self.assertTrue(any(isinstance(c.func, ast.Name) and c.func.id == "ApiError" for c in calls))
        self.assertTrue(any(isinstance(dec, ast.Attribute) and dec.attr == "before_request"
                            for dec in functions[0].decorator_list))

    def test_public_setup_establishes_verified_company_context_before_settings(self):
        tree = self._tree(ERP / "api" / "auth.py")
        setup = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "setup")
        source_lines = (ERP / "api" / "auth.py").read_text(encoding="utf-8").splitlines()
        text = "\n".join(source_lines[setup.lineno - 1:setup.end_lineno])
        self.assertIn("tenancy.add_membership(company_id, uid, 'admin', status='active')", text)
        self.assertIn("g.company = tenancy.membership_company_payload(membership)", text)
        self.assertLess(text.index("g.company ="), text.index("settings.save_settings("))
        self.assertIn("company_id=company_id", text)

    def test_public_api_allowlist_is_exact_and_limited(self):
        tree = self._tree(ERP / "__init__.py")
        assignments = {node.targets[0].id: node.value for node in tree.body
                       if isinstance(node, ast.Assign) and len(node.targets) == 1
                       and isinstance(node.targets[0], ast.Name)}
        public = ast.literal_eval(assignments["PUBLIC_PATHS"])
        self.assertEqual(public, {"/api/health", "/api/auth/setup-status", "/api/auth/setup", "/api/auth/login"})

    def test_multi_company_operations_gate_defaults_closed(self):
        tree = self._tree(ERP / "config.py")
        assignments = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Config"]
        self.assertEqual(len(assignments), 1)
        value = next(node.value for node in assignments[0].body if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == "TENANT_DATA_SCOPING_ENABLED"
                             for target in node.targets))
        self.assertIs(value.value, False)

    def test_restore_journal_guard_precedes_database_initialization(self):
        tree = self._tree(ERP / "__init__.py")
        factory = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "create_app")
        calls = [node for node in ast.walk(factory) if isinstance(node, ast.Call)]
        db_init_line = next(node.lineno for node in calls if isinstance(node.func, ast.Attribute)
                            and node.func.attr == "init_db")
        raise_lines = [node.lineno for node in ast.walk(factory) if isinstance(node, ast.Raise)]
        self.assertTrue(any(line < db_init_line for line in raise_lines),
                        "Startup must refuse to open a database while a restore journal exists.")

    def test_file_routes_require_permissions_and_company_context(self):
        tree = self._tree(ERP / "api" / "file_objects.py")
        routes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and any(isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                          and dec.func.attr in {"get", "post", "delete"} for dec in node.decorator_list)]
        self.assertEqual(len(routes), 6)
        for route in routes:
            with self.subTest(route=route.name):
                self.assertTrue(any(isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name)
                                    and dec.func.id == "require" for dec in route.decorator_list))
        source = (ERP / "api" / "file_objects.py").read_text(encoding="utf-8")
        for marker in ("_active_company()", "company_id=cid", "company_id=_active_company()"):
            self.assertIn(marker, source)

    def test_preflight_is_read_only_and_reports_missing_runtime_dependencies(self):
        result = run_preflight({"ERP_ENV": "development"})
        self.assertTrue(result["read_only"])
        self.assertIn(result["status"], {"blocked", "preflight_pass_with_warnings", "pass"})
        names = {item["name"] for item in result["checks"]}
        self.assertIn("python:Flask", names)
        self.assertIn("python:Werkzeug", names)
        self.assertIn("clamav_scanner", names)

    def test_preflight_blocks_production_without_secret_and_scanner(self):
        result = run_preflight({"ERP_ENV": "production", "ERP_SECRET_KEY": "short"})
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("ERP_SECRET_KEY" in item for item in result["blockers"]))
        self.assertTrue(any("ClamAV" in item for item in result["blockers"]))

    def test_preflight_rejects_storage_inside_frontend(self):
        result = run_preflight({
            "ERP_ENV": "development",
            "ERP_DB_PATH": "/tmp/erp-stage17/database.sqlite3",
            "ERP_FILE_STORAGE_DIR": "/tmp/erp-stage17/frontend/files",
            "ERP_FRONTEND_DIR": "/tmp/erp-stage17/frontend",
        })
        self.assertTrue(any("must not be inside" in item for item in result["blockers"]))


try:
    FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None and importlib.util.find_spec("werkzeug") is not None
except (ImportError, ValueError, ModuleNotFoundError):
    FLASK_AVAILABLE = False


@unittest.skipUnless(FLASK_AVAILABLE, "Real Flask HTTP tests require Flask and Werkzeug; install backend/requirements.txt.")
class Stage17RealHttpIntegrationTests(unittest.TestCase):
    """Real Flask test-client scenarios; skipped rather than replaced by a fake Flask implementation."""

    def setUp(self):
        import os
        import tempfile
        from erp import create_app

        self.tmp = tempfile.TemporaryDirectory(prefix="erp-stage17-http-")
        root = Path(self.tmp.name)
        self.app = create_app({
            "DB_PATH": str(root / "db" / "erp.sqlite3"),
            "FILE_STORAGE_DIR": str(root / "private-files"),
            "FRONTEND_DIR": str(ROOT.parent / "frontend"),
            "SECRET_KEY": "stage17-http-test-secret-key-material-0001",
            "TESTING": True,
            "CORS_ORIGINS": ["https://erp.example.test"],
            "TENANT_DATA_SCOPING_ENABLED": True,  # Explicitly test-only; production config stays False.
            "FILE_QUOTA_BYTES": 1024 * 1024,
            "FILE_UPLOADS_PER_HOUR": 30,
        })
        self.client = self.app.test_client()
        response = self.client.post("/api/auth/setup", json={
            "company": "HTTP Tenant One", "name": "Owner", "email": "owner@stage17.test",
            "password": "Stage17Pass123",
        })
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.token_one = response.json["token"]
        from erp import db, security
        from erp.services import tenancy
        with self.app.app_context():
            self.user = db.one("SELECT id,email,name,role FROM users WHERE email=?", ("owner@stage17.test",))
            self.company_one = db.scalar("SELECT id FROM companies ORDER BY id LIMIT 1")
            self.company_two = tenancy.create_company("HTTP Tenant Two")
            tenancy.add_membership(self.company_two, self.user["id"], "admin", status="active")
            self.token_two = security.make_token(self.user, company_id=self.company_two)
        self.storage_root = root / "private-files"

    def tearDown(self):
        self.tmp.cleanup()

    def test_authentication_headers_and_active_company_switch(self):
        self.assertEqual(self.client.get("/api/file-objects").status_code, 401)
        response = self.client.get("/api/auth/companies", headers={"Authorization": f"Bearer {self.token_one}"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        switched = self.client.post("/api/auth/switch-company", json={"company_id": self.company_two},
                                    headers={"Authorization": f"Bearer {self.token_one}"})
        self.assertEqual(switched.status_code, 200, switched.get_data(as_text=True))
        self.assertEqual(switched.json["active_company"]["id"], self.company_two)

    def test_attachment_metadata_and_download_are_isolated_between_companies(self):
        import io
        headers_one = {"Authorization": f"Bearer {self.token_one}"}
        upload = self.client.post("/api/file-objects/upload", headers=headers_one,
                                  data={"file": (io.BytesIO(b"stage17 private file"), "private.txt")},
                                  content_type="multipart/form-data")
        self.assertEqual(upload.status_code, 201, upload.get_data(as_text=True))
        file_id = upload.json["file"]["id"]
        self.assertEqual(upload.json["file"]["scan_status"], "pending")

        headers_two = {"Authorization": f"Bearer {self.token_two}"}
        other_list = self.client.get("/api/file-objects", headers=headers_two)
        self.assertEqual(other_list.status_code, 200, other_list.get_data(as_text=True))
        self.assertEqual(other_list.json["items"], [])
        self.assertEqual(self.client.get(f"/api/file-objects/{file_id}", headers=headers_two).status_code, 404)
        self.assertEqual(self.client.get(f"/api/file-objects/{file_id}/download", headers=headers_two).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/file-objects/{file_id}", headers=headers_two).status_code, 404)
        self.assertEqual(self.client.get(f"/api/file-objects/{file_id}", headers=headers_one).status_code, 200)

    def test_client_cannot_supply_tenant_id_during_file_upload(self):
        import io
        response = self.client.post(
            "/api/file-objects/upload",
            headers={"Authorization": f"Bearer {self.token_one}"},
            data={"company_id": str(self.company_two), "file": (io.BytesIO(b"x"), "x.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 422)

    def test_maintenance_mode_blocks_all_api_routes(self):
        self.app.config["MAINTENANCE_MODE"] = True
        response = self.client.get("/api/file-objects", headers={"Authorization": f"Bearer {self.token_one}"})
        self.assertEqual(response.status_code, 503)

    def test_startup_refuses_interrupted_restore_marker(self):
        from erp import create_app
        db_path = Path(self.app.config["DB_PATH"])
        marker = Path(str(db_path) + ".restore-in-progress.json")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('{"stage":"test"}', encoding="utf-8")
        try:
            with self.assertRaisesRegex(RuntimeError, "restore was interrupted"):
                create_app({
                    "DB_PATH": str(db_path), "FILE_STORAGE_DIR": self.app.config["FILE_STORAGE_DIR"],
                    "FRONTEND_DIR": self.app.config["FRONTEND_DIR"],
                    "SECRET_KEY": "stage17-http-test-secret-key-material-0002", "TESTING": True,
                })
        finally:
            marker.unlink(missing_ok=True)

    def test_non_member_cannot_switch_to_another_company(self):
        from erp.services import tenancy
        with self.app.app_context():
            outsider_company = tenancy.create_company('No Membership Company')
        response = self.client.post(
            '/api/auth/switch-company',
            json={'company_id': outsider_company},
            headers={'Authorization': f'Bearer {self.token_one}'},
        )
        self.assertEqual(response.status_code, 403, response.get_data(as_text=True))
        self.assertEqual(response.json['error']['code'], 'company_access_denied')

    def test_revoked_membership_invalidates_existing_company_token(self):
        from erp import db
        with self.app.app_context():
            db.execute(
                "UPDATE company_memberships SET status='suspended' WHERE user_id=? AND company_id=?",
                (self.user['id'], self.company_two),
            )
        response = self.client.get(
            '/api/auth/me', headers={'Authorization': f'Bearer {self.token_two}'}
        )
        self.assertEqual(response.status_code, 401, response.get_data(as_text=True))

    def test_operational_api_remains_blocked_until_tenant_gate_is_enabled(self):
        self.app.config['TENANT_DATA_SCOPING_ENABLED'] = False
        headers = {'Authorization': f'Bearer {self.token_one}'}
        blocked = self.client.get('/api/file-objects', headers=headers)
        self.assertEqual(blocked.status_code, 503, blocked.get_data(as_text=True))
        self.assertEqual(blocked.json['error']['code'], 'tenant_isolation_pending')
        companies = self.client.get('/api/auth/companies', headers=headers)
        self.assertEqual(companies.status_code, 200, companies.get_data(as_text=True))
        self.assertFalse(companies.json['can_use_operations'])

    def test_pending_attachment_cannot_be_downloaded_and_clean_file_is_served_safely(self):
        import io
        from erp import db
        headers = {'Authorization': f'Bearer {self.token_one}'}
        content = b'http integration attachment content'
        upload = self.client.post(
            '/api/file-objects/upload', headers=headers,
            data={'file': (io.BytesIO(content), 'integration.txt')},
            content_type='multipart/form-data',
        )
        self.assertEqual(upload.status_code, 201, upload.get_data(as_text=True))
        file_id = upload.json['file']['id']
        pending = self.client.get(f'/api/file-objects/{file_id}/download', headers=headers)
        self.assertEqual(pending.status_code, 423, pending.get_data(as_text=True))
        self.assertEqual(pending.json['error']['code'], 'file_scan_pending')

        with self.app.app_context():
            db.execute(
                "UPDATE file_objects SET scan_status='clean',scan_engine='test-fixture' WHERE id=? AND company_id=?",
                (file_id, self.company_one),
            )
        downloaded = self.client.get(f'/api/file-objects/{file_id}/download', headers=headers)
        self.assertEqual(downloaded.status_code, 200, downloaded.get_data(as_text=True))
        self.assertEqual(downloaded.data, content)
        self.assertIn('attachment;', downloaded.headers.get('Content-Disposition', ''))
        self.assertEqual(downloaded.headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(downloaded.headers.get('Cache-Control'), 'no-store')

    def test_download_hash_mismatch_fails_closed_and_marks_file_error(self):
        import io
        from erp import db
        headers = {'Authorization': f'Bearer {self.token_one}'}
        upload = self.client.post(
            '/api/file-objects/upload', headers=headers,
            data={'file': (io.BytesIO(b'original-bytes'), 'tamper.txt')},
            content_type='multipart/form-data',
        )
        self.assertEqual(upload.status_code, 201, upload.get_data(as_text=True))
        file_id = upload.json['file']['id']
        with self.app.app_context():
            row = db.one('SELECT storage_key FROM file_objects WHERE id=? AND company_id=?',
                         (file_id, self.company_one))
            db.execute("UPDATE file_objects SET scan_status='clean' WHERE id=? AND company_id=?",
                       (file_id, self.company_one))
        (self.storage_root / str(self.company_one) / row['storage_key']).write_bytes(b'tampered-bytes')
        response = self.client.get(f'/api/file-objects/{file_id}/download', headers=headers)
        self.assertEqual(response.status_code, 410, response.get_data(as_text=True))
        self.assertEqual(response.json['error']['code'], 'file_integrity_failed')
        with self.app.app_context():
            state = db.scalar('SELECT scan_status FROM file_objects WHERE id=? AND company_id=?',
                              (file_id, self.company_one))
        self.assertEqual(state, 'error')

    def test_cors_preflight_only_allows_configured_origins(self):
        allowed = self.client.options('/api/file-objects', headers={'Origin': 'https://erp.example.test'})
        self.assertEqual(allowed.status_code, 204)
        self.assertEqual(allowed.headers.get('Access-Control-Allow-Origin'), 'https://erp.example.test')
        denied = self.client.options('/api/file-objects', headers={'Origin': 'https://attacker.example'})
        self.assertNotEqual(denied.headers.get('Access-Control-Allow-Origin'), 'https://attacker.example')
