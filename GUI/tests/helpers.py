"""Offline GUI fixtures; no models or external tools are invoked."""

from time import monotonic, sleep


def result_for(config):
    return {
        "status": "completed",
        "test_type": config.test_type.lower(),
        "tool": config.tool_id,
        "model": config.model_name,
        "summary": {"test_count": 1},
        "results": [{"offline": True}],
        "metadata": {"run_id": "offline-run", "duration_seconds": 0.1},
        "artifacts": {},
    }


def wait_for_finish(manager, timeout=5):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        task = manager.snapshot()
        if task is not None and task.status != "running":
            return task
        sleep(0.01)
    raise AssertionError("Offline task did not finish in time")
