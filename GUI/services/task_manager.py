"""Process-local GUI tasks that survive Streamlit page/session reruns.

Workers never access Streamlit state. A supervisor thread owns one isolated
process tree; it reaps the worker before publishing a terminal task state.
Task state lasts until the Streamlit service process exits.
"""

from dataclasses import asdict, dataclass, replace
import logging
from threading import Event, Lock, Thread
from time import perf_counter, sleep
from typing import Callable, Optional

from GUI.services.task_worker import TaskProcess

from main.core.run_result import RunContext, build_run_result, start_run
from main.core.run_store import persist_run, redact_credentials, task_directory


_UNSPECIFIED = object()
_LOGGER = logging.getLogger(__name__)


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
    persistence_status: str = "pending"
    persistence_error: Optional[str] = None
    result_path: Optional[str] = None
    core_run_id: Optional[str] = None
    cleanup_error: Optional[str] = None

    @property
    def duration_seconds(self):
        end = self.finished_perf
        return round((perf_counter() if end is None else end) - self.started_perf, 1)


def execute_test(task_id, config, *, store_root=None, results_dir=None, run_context=None):
    # Import lazily: the task manager and its offline tests need no model/API.
    from main.core.correctness_runner import run_correctness
    from main.core.runtime_runner import run_runtime
    from main.core.tool_registry import create_tool

    kwargs = {
        "tool": create_tool(config.tool_id),
        "model_name": config.model_name,
        "prompt_temperature": config.prompt_temperature,
        "agent_temperature": config.agent_temperature,
        "results_dir": results_dir if results_dir is not None else task_directory(task_id, root=store_root),
        "run_context": run_context,
        "task_id": task_id,
        "persist_result": False,  # TaskManager also records execution failures.
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
    def __init__(self, execute: Optional[Callable] = None, *, store_root=None, worker_factory=None):
        # Optional executors are also isolated and must be spawn-pickleable.
        self._execute = execute
        self._worker_factory = worker_factory
        self._store_root = store_root
        self._lock = Lock()
        self._task = None
        self._last_result = None
        self._worker = None
        self._cancel_event = None
        self._monitor = None

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
            if self._task is not None and self._task.status in ("running", "cancelling"):
                return self._task

            context = start_run()
            core_context = start_run()
            task = TaskSnapshot(
                task_id=context.run_id,
                config=config,
                status="running",
                started_at=context.started_at,
                started_perf=context.started_perf,
                core_run_id=core_context.run_id,
            )
            self._task = task
            cancelled = Event()
            self._cancel_event = cancelled
            worker = Thread(
                target=self._run,
                args=(task, cancelled, core_context),
                name=f"toolfuzz-{task.task_id}",
                daemon=True,
            )
            self._monitor = worker
            try:
                worker.start()
            except Exception as exc:
                self._task = self._finish(task, None, f"{type(exc).__name__}: {exc}", context=core_context)
                self._cancel_event = None
                self._monitor = None
            return self._task

    def cancel(self, task_id, *, expected_task_id=_UNSPECIFIED):
        """Accept cancellation atomically; only the supervisor sets the terminal state."""
        with self._lock:
            task = self._task
            if task is None or task.task_id != task_id:
                return task
            if expected_task_id is not _UNSPECIFIED and expected_task_id != task.task_id:
                return task
            if task.status == "running" and self._cancel_event is not None:
                self._task = replace(task, status="cancelling")
                self._cancel_event.set()
            return self._task

    def _finish(self, task, result, error, *, context=None, cancelled=False, cleanup_error=None, worker_info=None):
        finished_perf = perf_counter()
        error = redact_credentials(error)
        if result is None or cancelled:
            if cancelled and isinstance(result, dict):
                metadata = result.get("metadata") or {}
                if isinstance(metadata, dict) and metadata.get("run_id"):
                    context = RunContext(
                        metadata["run_id"], metadata.get("started_at") or context.started_at,
                        context.started_perf,
                    )
            result = build_run_result(
                context=context or RunContext(task.task_id, task.started_at, task.started_perf),
                status="cancelled" if cancelled else "failed", test_type=task.config.test_type.lower(),
                tool=task.config.tool_id, model=task.config.model_name,
                config=asdict(task.config), summary={}, results=[],
                json_path=None, html_path=None, task_id=task.task_id, error=error,
            )
            # Reuse the exact context supplied to the Runner, even before it returns.
            if context is None:
                result["metadata"]["run_id_source"] = "task_manager"
            else:
                result["metadata"]["run_id_source"] = "core_context"
            if cancelled:
                result["summary"]["incomplete"] = True
        result = dict(result)
        if worker_info:
            result["metadata"] = {**result["metadata"], **worker_info}
        if cleanup_error:
            result["cleanup_error"] = cleanup_error
        result["task_config"] = asdict(task.config)
        result = persist_run(result, task_id=task.task_id, root=self._store_root)
        persistence = result["persistence"]
        return replace(
            task, status="cancelled" if cancelled else "completed" if error is None else "failed",
            finished_perf=finished_perf, result=None if cancelled else result, error=error,
            persistence_status=persistence["status"],
            persistence_error=persistence.get("error"), result_path=persistence.get("path"),
            core_run_id=result["metadata"]["run_id"],
            cleanup_error=cleanup_error,
        )

    def _run(self, task, cancelled, context):
        worker = None
        result = None
        error = None
        cleanup_error = None
        worker_info = None
        try:
            if not cancelled.is_set():
                factory = self._worker_factory or TaskProcess
                worker = factory(
                    task.task_id, task.config, store_root=self._store_root,
                    run_context=context, execute=self._execute,
                )
                with self._lock:
                    self._worker = worker
                if not cancelled.is_set():
                    worker.start()
                    while not cancelled.is_set():
                        if worker.poll(cancelled):
                            payload = worker.response()
                            result, error = payload["result"], payload["error"]
                            if isinstance(result, dict) and result.get("status") == "failed":
                                error = error or result.get("error") or "Runner reported failure"
                            break
        except BaseException as exc:
            error = redact_credentials(f"{type(exc).__name__}: {exc}")
        finally:
            if worker is not None:
                # Never unlock until the owned process tree really has stopped.
                for attempt in range(3):
                    try:
                        if attempt == 0:
                            worker.finish(cancelled)
                        else:
                            worker.stop()
                        break
                    except Exception as exc:
                        diagnostic = redact_credentials(f"{type(exc).__name__}: {exc}")
                        _LOGGER.error("Task %s cleanup attempt %s/3 failed: %s", task.task_id, attempt + 1, diagnostic)
                        with self._lock:
                            self._task = replace(self._task, cleanup_error=diagnostic)
                        if attempt < 2:
                            sleep(0.1)
                else:
                    # Keep ownership and the single-task lock for operator recovery.
                    # No publication, file deletion, or false terminal state.
                    with self._lock:
                        cancelled.set()
                        self._task = replace(self._task, status="cancelling")
                        self._monitor = None
                    _LOGGER.error("Task %s remains locked: process-tree cleanup could not be confirmed", task.task_id)
                    return
                if getattr(worker, "pid", None) is not None:
                    worker_info = {
                        "worker_pid": worker.pid, "worker_reaped": worker.stopped,
                        "worker_exitcode": worker.exitcode, "worker_forced": worker.forced,
                        "worker_shutdown_reason": worker.shutdown_reason,
                    }
            # Serialise cancel acceptance vs final publication/persistence.
            with self._lock:
                is_cancelled = self._task.status == "cancelling"
                try:
                    if worker is not None and result is not None and not is_cancelled:
                        result = worker.publish(result)
                except Exception as exc:
                    error = redact_credentials(f"{type(exc).__name__}: {exc}")
                    result = None
                if worker is not None:
                    try:
                        worker.close()
                    except Exception as exc:
                        # Worker is already dead. Retain cleanup diagnostics.
                        cleanup_error = redact_credentials(f"{type(exc).__name__}: {exc}")
                if self._task.task_id == task.task_id:
                    self._task = self._finish(
                        task, result, None if is_cancelled else error,
                        context=context, cancelled=is_cancelled,
                        cleanup_error=cleanup_error,
                        worker_info=worker_info,
                    )
                    if not is_cancelled and error is None:
                        self._last_result = self._task.result
                self._worker = None
                self._cancel_event = None
                self._monitor = None


# Imported modules outlive individual Streamlit script runs and browser sessions.
# Do not create the manager inside app.py or store it only in session_state.
_manager = TaskManager()


def get_task_manager():
    return _manager
