#!/usr/bin/env bash
# Reproducible local API entry point; no installation or environment mutation.
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON="$PROJECT_ROOT/ToolFuzz/.venv/bin/python"
API_DEPS="$PROJECT_ROOT/.venv/api-deps"

fail() { printf 'ToolFuzz API: %s\n' "$1" >&2; exit 1; }

[[ $# -eq 0 ]] || fail 'This script takes no arguments. Set API_PORT to change the port.'
[[ -x "$PYTHON" ]] || fail 'Project Python is missing: ToolFuzz/.venv/bin/python.'
[[ -d "$API_DEPS" ]] || fail 'API dependencies are missing: .venv/api-deps. Restore the existing dependencies before starting.'
for module in fastapi starlette uvicorn; do
    [[ -f "$API_DEPS/$module/__init__.py" ]] || fail "Missing API dependency: $module in .venv/api-deps. No packages were installed."
done
[[ -f "$PROJECT_ROOT/main/api/app.py" ]] || fail 'API application is missing: main/api/app.py.'
[[ "${WEB_CONCURRENCY-1}" == 1 ]] || fail 'Only one worker is supported. Set WEB_CONCURRENCY=1 or unset it.'
[[ "${UVICORN_WORKERS:-1}" == 1 ]] || fail 'Only one worker is supported. Set UVICORN_WORKERS=1 or unset it.'
case "${UVICORN_RELOAD:-false}" in
    0|false|False|FALSE|no|No|NO|off|Off|OFF|'') ;;
    *) fail 'Reload is not supported. Unset UVICORN_RELOAD.' ;;
esac
for setting in UVICORN_FD UVICORN_UDS UVICORN_ENV_FILE; do
    [[ -z "${!setting:-}" ]] || fail "Unset $setting to use this local single-worker entry point."
done

export PYTHONPATH="$API_DEPS:$PROJECT_ROOT:$PROJECT_ROOT/ToolFuzz"
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONHOME
export API_PORT="${API_PORT:-8000}"

"$PYTHON" -B - <<'PY'
import os
from pathlib import Path
import sys

port = os.environ['API_PORT']
if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
    sys.exit('ToolFuzz API: API_PORT must be an integer between 1 and 65535.')
try:
    import fastapi
    import starlette
    import uvicorn
    from main.api.app import app
    deps = Path(os.environ['PYTHONPATH'].split(os.pathsep)[0]).resolve()
    for module in (fastapi, starlette, uvicorn):
        if deps not in Path(module.__file__).resolve().parents:
            raise ImportError('Dependency priority mismatch')
    if fastapi.__version__ != '0.115.14' or starlette.__version__ != '0.46.2':
        raise ImportError('Unexpected API dependency version')
except Exception:
    sys.exit('ToolFuzz API: Cannot load the expected API dependencies (FastAPI 0.115.14 / Starlette 0.46.2). Check .venv/api-deps; no packages were changed.')
print(f'ToolFuzz API: FastAPI {fastapi.__version__}, Starlette {starlette.__version__}; single worker.', flush=True)
PY

exec "$PYTHON" -B -m uvicorn main.api.app:app --app-dir "$PROJECT_ROOT" --host 127.0.0.1 --port "$API_PORT" --workers 1
