"""Process-local GUI tasks that survive Streamlit page/session reruns.

Workers never access Streamlit state. Only one GUI test may run at a time:
ToolFuzz's fuzzer temporarily changes process-wide JSON parsing behavior.
Task state lasts until the Streamlit service process exits.
"""

from dataclasses import dataclass, replace
from threading import Lock, Thread
from time import perf_counter
from typing import Callable, Optional

from main.core.run_result import start_run


_UNSPECIFIED = object()


@dataclass(frozen=True)
class RunConfig:
    test_type: str
    tool_id: str
    model_name: str
    iterations: int
    prompt_temperature: float
    agent_temperature: float
    judge_temperature: float = 0


@dataclass(frozen=True)
class TaskSnapshot:
    task_id: str
    config: RunConfig
    status: str
    started_at: str
    started_perf: float
    finished_perf: Optional[float] = None
    result: Optional[dict] = None
    error: Optional[str] = None

    @property
    def duration_seconds(self):
        end = self.finished_perf
        return round((perf_counter() if end is None else end) - self.started_perf, 1)


def execute_test(task_id, config):
    # Import lazily: the task manager and its offline tests need no model/API.
    from main.core.correctness_runner import run_correctness
    from main.core.runtime_runner import DEFAULT_RESULTS_DIR, run_runtime
    from main.core.tool_registry import create_tool

    kwargs = {
        "tool": create_tool(config.tool_id),
        "model_name": config.model_name,
        "prompt_temperature": config.prompt_temperature,
        "agent_temperature": config.agent_temperature,
        "results_dir": DEFAULT_RESULTS_DIR / task_id,
    }
    if config.test_type == "Runtime":
        return run_runtime(
            **kwargs,
            fuzzer_iters=config.iterations,
            output_name="runtime",
            title="ToolFuzz GUI - Runtime",
        )
    if config.test_type == "Correctness":
        return run_correctness(
            **kwargs,
            prompt_set_iters=config.iterations,
            judge_temperature=config.judge_temperature,
            output_name="correctness",
            title="ToolFuzz GUI - Correctness",
        )
    raise ValueError(f"Unknown test type: {config.test_type!r}")


class TaskManager:
    def __init__(self, execute: Callable = execute_test):
        self._execute = execute
        self._lock = Lock()
        self._task = None
        self._last_result = None

    def snapshot(self):
        with self._lock:
            return self._task

    def last_result(self):
        with self._lock:
            return self._last_result

    def start(self, config, *, expected_task_id=_UNSPECIFIED):
        """Atomically start a test, or reattach to the one already running."""
        with self._lock:
            current_id = self._task.task_id if self._task is not None else None
            if expected_task_id is not _UNSPECIFIED and expected_task_id != current_id:
                # Ignore an old tab's queued click even if that test has finished.
                return self._task
            if self._task is not None and self._task.status == "running":
                return self._task

            context = start_run()
            task = TaskSnapshot(
                task_id=context.run_id,
                config=config,
                status="running",
                started_at=context.started_at,
                started_perf=context.started_perf,
            )
            self._task = task
            worker = Thread(
                target=self._run,
                args=(task,),
                name=f"toolfuzz-{task.task_id}",
                daemon=True,
            )
            try:
                worker.start()
            except Exception as exc:
                self._task = replace(
                    task,
                    status="failed",
                    finished_perf=perf_counter(),
                    error=f"{type(exc).__name__}: {exc}",
                )
            return self._task

    def _run(self, task):
        result = None
        error = None
        try:
            result = self._execute(task.task_id, task.config)
        except BaseException as exc:
            # Worker exits must release the running state as well as errors do.
            error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._task = replace(
                    task,
                    status="completed" if error is None else "failed",
                    finished_perf=perf_counter(),
                    result=result,
                    error=error,
                )
                if error is None:
                    self._last_result = result


# Imported modules outlive individual Streamlit script runs and browser sessions.
# Do not create the manager inside app.py or store it only in session_state.
_manager = TaskManager()


def get_task_manager():
    return _manager
