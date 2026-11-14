"""Run the real Flask HTTP scenarios and fail if any are skipped."""
import importlib.util
import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def main() -> int:
    missing = [name for name in ('flask', 'werkzeug') if importlib.util.find_spec(name) is None]
    if missing:
        print('BLOCKED: install Flask and Werkzeug before running HTTP integration tests: ' + ', '.join(missing),
              file=sys.stderr)
        return 2

    from tests.test_stage17_http_readiness import Stage17RealHttpIntegrationTests

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Stage17RealHttpIntegrationTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful() and not result.skipped:
        return 0
    if result.skipped:
        print(f'BLOCKED: {len(result.skipped)} HTTP tests were skipped; no skips are permitted.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
