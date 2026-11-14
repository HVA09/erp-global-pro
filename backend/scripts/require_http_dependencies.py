"""Fail closed when the real Flask/Werkzeug HTTP runtime is not installed."""
import importlib.util
import sys


def main() -> int:
    missing = [name for name in ('flask', 'werkzeug') if importlib.util.find_spec(name) is None]
    if missing:
        print('BLOCKED: missing required HTTP dependencies: ' + ', '.join(missing), file=sys.stderr)
        print('Install with: python -m pip install -r requirements.txt', file=sys.stderr)
        return 2
    import flask
    import werkzeug
    print(f'Flask {flask.__version__ if hasattr(flask, "__version__") else "installed"}')
    print(f'Werkzeug {werkzeug.__version__ if hasattr(werkzeug, "__version__") else "installed"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
