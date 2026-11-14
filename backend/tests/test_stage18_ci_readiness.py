"""Dependency-free checks for the real HTTP CI gate introduced in Stage 18."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class Stage18CiReadinessTests(unittest.TestCase):
    def test_ci_workflow_installs_requirements_and_runs_full_and_http_suites(self):
        workflow = (ROOT / '.github' / 'workflows' / 'erp-backend-ci.yml').read_text(encoding='utf-8')
        self.assertIn('python -m pip install -r requirements.txt', workflow)
        self.assertIn('python -m unittest discover -s tests -v', workflow)
        self.assertIn('python scripts/run_http_tests.py', workflow)
        self.assertIn("python-version: ['3.11', '3.12']", workflow)

    def test_http_runner_has_a_no_skips_gate(self):
        runner = (ROOT / 'backend' / 'scripts' / 'run_http_tests.py').read_text(encoding='utf-8')
        self.assertIn("result.skipped", runner)
        self.assertIn('no skips are permitted', runner)
        self.assertIn('Stage17RealHttpIntegrationTests', runner)

    def test_werkzeug_is_explicitly_declared_for_http_runtime(self):
        requirements = (ROOT / 'backend' / 'requirements.txt').read_text(encoding='utf-8')
        self.assertRegex(requirements, r'(?m)^Werkzeug>=3\.0,<4$')

    def test_multi_company_feature_flag_remains_closed(self):
        config = (ROOT / 'backend' / 'erp' / 'config.py').read_text(encoding='utf-8')
        self.assertRegex(config, r'TENANT_DATA_SCOPING_ENABLED\s*=\s*False')
