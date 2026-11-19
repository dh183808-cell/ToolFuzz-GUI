#!/usr/bin/env bash
# Local development only. Python environment setup stays in run_api.sh.
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON="$PROJECT_ROOT/ToolFuzz/.venv/bin/python"
[[ -x "$PYTHON" ]] || { echo 'ToolFuzz: 缺少 ToolFuzz/.venv/bin/python。' >&2; exit 1; }
exec "$PYTHON" -B - "$PROJECT_ROOT" "$@" <<'PY'
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
from threading import Event
from time import monotonic
from urllib.request import ProxyHandler, build_opener

root = Path(sys.argv[1])
stopping = Event()
children = []
opener = build_opener(ProxyHandler({}))

def fail(message):
    raise RuntimeError(message)

def interrupted(signum, frame):
    stopping.set()

def ready(url, child, *, health=False):
    deadline = monotonic() + 25
    while not stopping.is_set():
        if child.poll() is not None:
            fail('服务提前退出，请查看上方错误信息。')
        try:
            with opener.open(url, timeout=.5) as response:
                if response.status == 200 and (not health or json.load(response).get('status') == 'ok'):
                    return
        except (OSError, ValueError):
            pass
        if monotonic() >= deadline:
            fail('等待本地服务就绪超时。')
        stopping.wait(.1)

def launch(command, cwd):
    child = subprocess.Popen(command, cwd=cwd, start_new_session=True)
    children.append(child)
    return child

def cleanup():
    # Only these exact owned process groups, never search/kill by program name.
    for child in reversed(children):
        if child.poll() is not None:
            continue
        try:
            os.killpg(child.pid, signal.SIGINT)
        except ProcessLookupError:
            continue
    success = True
    for child in reversed(children):
        try:
            child.wait(timeout=35)
        except subprocess.TimeoutExpired:
            # Backend owns isolated task workers; don't kill it before it can
            # reap them. Report failure and preserve that ownership reference.
            print(f'ToolFuzz: 服务 PID {child.pid} 尚未退出，请检查其终端信息；未强行终止。', file=sys.stderr)
            success = False
    return success

code = 0
try:
    if len(sys.argv) != 2:
        fail('无需额外参数，直接运行 ./run_dev.sh。')
    for command in ('node', 'npm'):
        if not shutil.which(command):
            fail(f'缺少 {command}，请先准备项目要求的 Node.js 环境。')
    version = subprocess.check_output(['node', '--version'], text=True).strip().lstrip('v')
    major, minor, *_ = map(int, version.split('.'))
    if not ((major == 20 and minor >= 19) or (major == 22 and minor >= 12) or major > 22):
        fail('Node.js 版本不兼容，需要 20.19+、22.12+ 或更新版本。')
    subprocess.run(['npm', '--version'], check=True, stdout=subprocess.DEVNULL)
    for path in ('run_api.sh', 'frontend/node_modules/vite/bin/vite.js',
                 'frontend/node_modules/vue/package.json', '.venv/api-deps/fastapi/__init__.py'):
        if not (root / path).is_file():
            fail(f'缺少依赖或文件：{path}。本脚本不会自动安装依赖。')
    if os.environ.get('API_PORT', '8000') != '8000':
        fail('一键开发入口使用 API_PORT=8000，与前端代理一致；自定义后端端口请单独使用 run_api.sh。')
    for port in (8000, 5173):
        with socket.socket() as probe:
            # Match servers' bind behaviour: recently closed connections in
            # TIME_WAIT must not prevent an immediate development restart.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                fail(f'端口 {port} 已被占用，未启动服务，也不会停止占用进程。请先自行关闭原服务。')
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupted)
    backend = launch([str(root / 'run_api.sh')], root)
    ready('http://127.0.0.1:8000/api/v1/health', backend, health=True)
    if not stopping.is_set():
        # Run the installed Vite directly, avoiding an extra npm process layer.
        frontend = launch(['node', str(root / 'frontend/node_modules/vite/bin/vite.js'),
                           '--host', '127.0.0.1', '--port', '5173', '--strictPort'], root / 'frontend')
        ready('http://127.0.0.1:5173', frontend)
        if not stopping.is_set():
            print('\nToolFuzz 已就绪\n前端：http://127.0.0.1:5173\nAPI 文档：http://127.0.0.1:8000/docs\n按 Ctrl+C 关闭本次启动的前后端。\n', flush=True)
    while not stopping.wait(.25):
        if any(child.poll() is not None for child in children):
            fail('前端或后端已退出，正在关闭本次启动的其他服务。')
except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
    print('ToolFuzz: ' + str(exc), file=sys.stderr)
    code = 1
finally:
    if not cleanup():
        code = 1
sys.exit(code)
PY
