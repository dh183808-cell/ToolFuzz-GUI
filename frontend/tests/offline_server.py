"""HTTP lifecycle fixture: real TaskManager/Worker, temporary store, no models."""
from functools import partial
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import uvicorn

from GUI.services.task_manager import TaskManager
from GUI.tests.test_run_store import process_fixture
from main.api.app import create_app


def execute(task_id, config, *, root):
    # One iteration completes; two blocks until the real worker is cancelled.
    if config.iterations > 1:
        print('offline-blocking-entered:' + task_id, flush=True)
    return process_fixture(task_id, config, root=root, block=config.iterations > 1)


if __name__ == '__main__':
    with TemporaryDirectory(prefix='toolfuzz-p2-3-http-') as temporary:
        manager = TaskManager(execute=partial(execute, root=temporary), store_root=Path(temporary) / 'experiment_runs')
        app = create_app(manager_factory=lambda: manager, store_root=Path(temporary) / 'experiment_runs')
        uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]), workers=1, log_level='warning')
