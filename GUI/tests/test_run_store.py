"""Offline P0/P1 checks, with all output in TemporaryDirectory."""

from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from functools import partial
import os
import subprocess
import tempfile
from time import monotonic, sleep
import io
from importlib.util import find_spec
import json
from pathlib import Path
import runpy
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from GUI.components.history import history_label, history_options
from GUI.services.result_view import dashboard, read_report
from GUI.services.task_manager import RunConfig, TaskManager, TaskSnapshot
from GUI.services.task_worker import TaskProcess, _group_running
from main.core.run_result import build_run_result, start_run
from main.core.run_store import (
    DEFAULT_RESULTS_DIR, HISTORICAL_RESULTS_DIR, PROJECT_ROOT, list_runs,
    load_run, persist_run, report_path, save_run, task_directory,
)


class Record:
    """Small stand-in for an upstream Pydantic record, never calls a model."""
    def __init__(self, data):
        self.data = deepcopy(data)
        self.tool = data["tool"]

    def model_dump(self):
        return deepcopy(self.data)

    @classmethod
    def model_validate(cls, data):
        return cls(data)


def result(kind="runtime"):
    cases = [{"successful_trigger": True, "prompt": "中文测试"}]
    summary = {"test_count": 1, "successful_triggers": 1}
    if kind == "correctness":
        cases = [{"individual_run_test_results": [{"tool_failure": True}]}]
        summary = {"prompt_set_count": 1, "prompt_count": 1, "tool_failure_count": 1}
    return build_run_result(
        context=start_run(), status="completed", test_type=kind,
        tool="offline", model="offline-model", config={"iterations": 1},
        summary=summary, results=cases, json_path="test.json", html_path="test.html",
    )


@contextmanager
def offline_dependencies(kind, records):
    """Only external boundaries are replaced; actual Runner/store/report code runs."""
    agent = ModuleType("main.compat.react_agent_new")
    agent.CompatibleReactAgentNew = lambda *args: SimpleNamespace(agent=object())
    provider = ModuleType("main.core.model_provider")
    provider.DEFAULT_MODEL = "offline-model"
    provider.create_deepseek_llm = Mock(return_value=object())
    runtime = ModuleType("main.compat.runtime_tester")
    correctness = ModuleType("main.compat.correctness")
    tester = Mock(return_value=SimpleNamespace(
        test=Mock(), test_results=records, results=records,
    ))
    runtime.CompatibleRuntimeErrorTester = tester
    correctness.CompatibleCorrectnessTester = tester
    utils = ModuleType("src.toolfuzz.utils")
    utils.env = Mock()
    utils.env.get_template.return_value.render.return_value = "<html>Offline report</html>"
    utils.transform_test_results = lambda values: values
    modules = {
        agent.__name__: agent, provider.__name__: provider,
        runtime.__name__: runtime, correctness.__name__: correctness,
        utils.__name__: utils,
    }
    with patch.dict(sys.modules, modules), patch("main.core.run_reports.resources.files") as files:
        files.return_value.joinpath.return_value.read_bytes.return_value = b"offline logo"
        namespace = runpy.run_path(str(PROJECT_ROOT / "main/core" / f"{kind}_runner.py"))
        yield namespace[f"run_{kind}"], provider


def process_fixture(task_id, config, *, root, block=False, descendant=False, socket_block=False):
    """Real spawned executor fixture; no model or external network access."""
    root = Path(root)
    staging = Path(tempfile.gettempdir())
    (staging / "owned-cache.tmp").write_text("private temporary data")
    (root / "worker-pid").write_text(str(os.getpid()))
    if descendant:
        child = subprocess.Popen([
            sys.executable, "-B", "-c",
            "import time; from pathlib import Path; "
            "p=Path(__import__('sys').argv[1]); "
            "p.write_text('started'); "
            "exec(\"while True:\\n p.write_text(str(time.monotonic())); time.sleep(.02)\")",
            str(root / "descendant-heartbeat"),
        ])
        (root / "descendant-pid").write_text(str(child.pid))
    (root / "entered").write_text("started")
    if socket_block:
        import socket
        first, second = socket.socketpair()
        try:
            first.recv(1)  # Simulate an in-flight synchronous call, no network.
            (root / "unexpected-next-call").write_text("must not happen")
        finally:
            first.close()
            second.close()
    while block:
        (root / "heartbeat").write_text(str(monotonic()))
        sleep(0.02)
    value = result(config.test_type.lower())
    for kind in ("json", "html"):
        path = staging / f"runtime.{kind}"
        path.write_text("[]" if kind == "json" else "<html>offline</html>")
        value["artifacts"][f"{kind}_path"] = str(path)
    return value


_fixture_resources = []


def lifecycle_fixture(task_id, config, *, root, semaphores=False, dependencies=False,
                      failure=False, failed_result=False, hang_exit=False, descendant=False):
    """Keep named resources alive until the Worker's Python exit finalizers."""
    if semaphores:
        import multiprocessing
        for _ in range(2):
            _fixture_resources.append(multiprocessing.get_context("spawn").Semaphore())
        Path(root, "semaphore-names.json").write_text(json.dumps([
            item._semlock.name for item in _fixture_resources
        ]))
    if dependencies:
        # Import the real Runtime dependency tree; do not construct a model/tool.
        with patch("socket.socket.connect", side_effect=AssertionError("Offline dependencies only")):
            import main.core.runtime_runner
            from tqdm import tqdm
            list(tqdm([], disable=True))
            lock = tqdm.get_lock()
        Path(root, "dependency-lock.json").write_text(json.dumps({
            "tqdm_semaphore": getattr(getattr(getattr(lock, "mp_lock", None), "_semlock", None), "name", None),
        }))
    if hang_exit:
        from multiprocessing.util import Finalize
        Finalize(None, sleep, args=(30,), exitpriority=10)
    if failure:
        raise RuntimeError("offline Runner failure after resources were created")
    value = process_fixture(task_id, config, root=root, descendant=descendant)
    if failed_result:
        value.update(status="failed", error="offline returned failure")
    return value


class InlineWorker:
    """In-process transport double for the existing mock-only Runner checks."""
    def __init__(self, task_id, config, *, store_root=None, run_context=None, execute=None):
        self.execute = execute
        self.task_id, self.config = task_id, config
        self.root, self.context = store_root, run_context

    def start(self):
        pass

    def poll(self, cancelled=None):
        from GUI.services.task_manager import execute_test
        self.value = self.execute(self.task_id, self.config) if self.execute is not None else execute_test(
            self.task_id, self.config, store_root=self.root, run_context=self.context,
        )
        return True

    def response(self):
        return {"result": self.value, "error": None}

    def stop(self):
        pass

    def finish(self, cancelled=None):
        self.stop()

    def publish(self, value):
        return value

    def close(self):
        pass


class RunStoreTests(unittest.TestCase):
    def manager(self, execute=None, **kwargs):
        return TaskManager(execute, worker_factory=InlineWorker, **kwargs)

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def finish(self, manager, config):
        threads = []

        def worker(**kwargs):
            thread = Thread(**kwargs)
            threads.append(thread)
            return thread

        with patch("GUI.services.task_manager.Thread", side_effect=worker):
            task = manager.start(config)
        threads[0].join(5)
        self.assertFalse(threads[0].is_alive())
        return manager.snapshot()

    def config(self, kind="Runtime"):
        return RunConfig(kind, "duckduckgo", "offline-model", 1, 0, 0)

    def test_save_load_utf8_and_identity(self):
        original = result()
        before = deepcopy(original)
        path = save_run(original, task_id="gui-task", root=self.root)
        loaded = load_run("gui-task", root=self.root)
        self.assertEqual(loaded["metadata"]["run_id"], original["metadata"]["run_id"])
        self.assertEqual(loaded["metadata"]["task_id"], "gui-task")
        for field in ("config", "summary", "results", "artifacts"):
            self.assertEqual(loaded[field], original[field])
        self.assertIn("中文测试", path.read_text(encoding="utf-8"))
        self.assertEqual(original, before)

    def test_runtime_correctness_and_gui_read_adapters(self):
        for kind in ("runtime", "correctness"):
            with self.subTest(kind=kind):
                original = result(kind)
                save_run(original, task_id=kind, root=self.root)
                loaded = load_run(kind, root=self.root)
                self.assertEqual(loaded["results"], original["results"])
                self.assertEqual(dashboard(loaded)["yes"], 1)
        self.assertEqual(len(list_runs(root=self.root)), 2)

    def test_corrupt_files_do_not_hide_other_runs(self):
        good = result()
        save_run(good, task_id="good", root=self.root)
        for name, content in [("broken", "{bad"), ("invalid", "[]"), ("missing-id", '{}'), ("bad-utf8", b'\xff')]:
            directory = self.root / name
            directory.mkdir()
            (directory / "run_result.json").write_bytes(content if isinstance(content, bytes) else content.encode())
            self.assertIsNone(load_run(name, root=self.root))
        self.assertIsNone(load_run("absent", root=self.root))
        self.assertEqual(len(list_runs(root=self.root)), 1)

    def test_atomic_failure_preserves_previous_file_and_cleans_temporary(self):
        original = result()
        path = save_run(original, task_id="task", root=self.root)
        previous = path.read_bytes()
        with patch("main.core.run_store.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                save_run(result("correctness"), task_id="task", root=self.root)
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual(list(path.parent.glob("*.tmp")), [])
        with self.assertRaises(TypeError):
            save_run({**original, "results": [object()]}, task_id="task", root=self.root)
        self.assertEqual(path.read_bytes(), previous)

    def test_credentials_in_config_trace_errors_and_reports_are_redacted(self):
        original = result()
        original["config"]["api_key"] = "private-config-value"
        original["summary"]["total_tokens"] = 42
        original["results"][0]["trace"] = json.dumps({"token": "trace-private-value"})
        original["error"] = "Authorization: Bearer bearer-value; api_key=another-private-value; opaque-value"
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "opaque-value"}):
            path = save_run(original, task_id="task", root=self.root)
        content = path.read_text()
        for secret in ("private-config-value", "trace-private-value", "bearer-value", "another-private-value", "opaque-value"):
            self.assertNotIn(secret, content)
        self.assertIn("[REDACTED]", content)
        self.assertEqual(load_run("task", root=self.root)["summary"]["total_tokens"], 42)

    def test_default_location_and_historical_path_protection(self):
        self.assertEqual(DEFAULT_RESULTS_DIR, PROJECT_ROOT / "experiment_runs")
        self.assertEqual(task_directory("task"), DEFAULT_RESULTS_DIR / "task")
        with self.assertRaises(ValueError):
            save_run(result(), root=HISTORICAL_RESULTS_DIR)
        for identity in ("../escape", "/tmp/escape", "..", "x/y", "x\\y"):
            with self.assertRaises(ValueError):
                task_directory(identity, root=self.root)
        with self.assertRaises(ValueError):
            report_path(self.root, "../escape")
        (self.root / "linked").symlink_to(HISTORICAL_RESULTS_DIR, target_is_directory=True)
        with self.assertRaises(ValueError):
            task_directory("linked", root=self.root)
        with patch("main.core.run_store.DEFAULT_RESULTS_DIR", self.root):
            saved = persist_run(result(), task_id="task")
        self.assertEqual(Path(saved["persistence"]["path"]).parent, self.root / "task")

    def test_both_core_runners_save_all_artifacts_in_one_directory(self):
        for kind in ("runtime", "correctness"):
            with self.subTest(kind=kind):
                data = result(kind)["results"][0]
                data.update(tool="offline", trace='{"api_key": "hidden-report-key"}')
                with offline_dependencies(kind, [Record(data)]) as (run, provider), patch("main.core.run_store.DEFAULT_RESULTS_DIR", self.root), redirect_stdout(io.StringIO()):
                    context = start_run()
                    actual = run(SimpleNamespace(name="offline"), task_id=kind, run_context=context)
                loaded = load_run(kind, root=self.root)
                self.assertEqual(actual["metadata"]["run_id"], context.run_id)
                self.assertEqual(actual["persistence"]["status"], "saved")
                self.assertEqual(actual["metadata"]["run_id"], loaded["metadata"]["run_id"])
                for report_kind in ("json", "html"):
                    path = Path(loaded["artifacts"][f"{report_kind}_path"])
                    self.assertEqual(path.parent, self.root / kind)
                    self.assertTrue(path.is_file())
                    self.assertIsNone(read_report(loaded["artifacts"], report_kind)[2])
                    self.assertNotIn("hidden-report-key", path.read_text())
                self.assertEqual(provider.create_deepseek_llm.call_count, 2 if kind == "runtime" else 3)

    def test_empty_run_also_has_json_and_html(self):
        with offline_dependencies("runtime", []) as (run, _), redirect_stdout(io.StringIO()):
            actual = run(SimpleNamespace(name="offline"), results_dir=self.root)
        self.assertEqual(json.loads(Path(actual["artifacts"]["json_path"]).read_text()), [])
        self.assertTrue(Path(actual["artifacts"]["html_path"]).is_file())
        self.assertTrue((self.root / "run_result.json").is_file())

    def test_default_gui_execution_routes_reports_and_result_to_same_task(self):
        for kind in ("runtime", "correctness"):
            with self.subTest(kind=kind), offline_dependencies(kind, []) as (run, _):
                runtime = ModuleType("main.core.runtime_runner")
                correctness = ModuleType("main.core.correctness_runner")
                runtime.run_runtime = run if kind == "runtime" else Mock()
                correctness.run_correctness = run if kind == "correctness" else Mock()
                registry = ModuleType("main.core.tool_registry")
                registry.create_tool = lambda tool_id: SimpleNamespace(name=tool_id)
                with patch.dict(sys.modules, {
                    runtime.__name__: runtime, correctness.__name__: correctness,
                    registry.__name__: registry,
                }), redirect_stdout(io.StringIO()):
                    task = self.finish(self.manager(store_root=self.root), self.config(kind.title()))
                loaded = load_run(task.task_id, root=self.root)
                self.assertEqual((task.status, task.persistence_status), ("completed", "saved"))
                for report_kind in ("json", "html"):
                    path = Path(loaded["artifacts"][f"{report_kind}_path"])
                    self.assertEqual(path.parent, Path(task.result_path).parent)
                    self.assertTrue(path.is_file())

    def test_report_failure_is_separate_from_test_execution(self):
        with offline_dependencies("runtime", []) as (run, _), redirect_stdout(io.StringIO()):
            with patch.dict(run.__globals__, {"save_reports": Mock(side_effect=OSError("report disk full"))}):
                actual = run(SimpleNamespace(name="offline"), results_dir=self.root)
        self.assertEqual(actual["status"], "completed")
        self.assertEqual(actual["persistence"]["status"], "saved")
        self.assertIn("report disk full", actual["artifacts"]["save_error"])

    def test_task_manager_saves_both_types_and_preserves_core_id(self):
        for kind in ("Runtime", "Correctness"):
            original = result(kind.lower())
            manager = self.manager(lambda *args: original, store_root=self.root)
            task = self.finish(manager, self.config(kind))
            loaded = load_run(task.task_id, root=self.root)
            self.assertEqual((task.status, task.persistence_status), ("completed", "saved"))
            self.assertEqual(loaded["metadata"]["task_id"], task.task_id)
            self.assertEqual(loaded["metadata"]["run_id"], original["metadata"]["run_id"])
            self.assertEqual(loaded["task_config"]["test_type"], kind)
            self.assertEqual(manager.last_result(), task.result)

    def test_task_manager_execution_and_thread_start_failures_are_saved(self):
        manager = self.manager(Mock(side_effect=RuntimeError("offline failure")), store_root=self.root)
        task = self.finish(manager, self.config())
        self.assertEqual((task.status, task.persistence_status), ("failed", "saved"))
        self.assertIsNone(manager.last_result())
        self.assertIn("offline failure", load_run(task.task_id, root=self.root)["error"])
        with patch("GUI.services.task_manager.Thread.start", side_effect=RuntimeError("thread failure")):
            task = manager.start(self.config())
        self.assertEqual((task.status, task.persistence_status), ("failed", "saved"))
        self.assertIsNone(load_run(task.task_id, root=self.root)["artifacts"]["json_path"])

    def test_storage_failure_does_not_change_execution_outcome_or_block_next_task(self):
        manager = self.manager(lambda *args: result(), store_root=self.root)
        with patch("main.core.run_store.save_run", side_effect=OSError("disk full")):
            task = self.finish(manager, self.config())
        self.assertEqual((task.status, task.persistence_status), ("completed", "failed"))
        self.assertIn("disk full", task.persistence_error)
        self.assertIsNone(task.error)
        self.assertEqual(self.finish(manager, self.config()).persistence_status, "saved")

    def test_polling_configuration_lock_and_stale_tab_protection(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)

        def execute(*args):
            entered.set()
            release.wait(5)
            return result()

        manager = self.manager(execute, store_root=self.root)
        task = manager.start(self.config())
        self.assertTrue(entered.wait(2))
        self.assertEqual(manager.snapshot().status, "running")
        self.assertEqual(manager.start(self.config("Correctness")).task_id, task.task_id)
        self.assertEqual(manager.snapshot().config.test_type, "Runtime")
        self.assertEqual(manager.start(self.config(), expected_task_id="stale").task_id, task.task_id)
        release.set()
        # Locking is tested above; wait for this worker before cleaning the temp folder.
        from time import monotonic, sleep
        deadline = monotonic() + 5
        while manager.snapshot().status == "running" and monotonic() < deadline:
            sleep(0.005)
        self.assertEqual(manager.snapshot().status, "completed")

    def history_fixture(self, kind="runtime", task_id=None):
        task_id = task_id or kind + "-history"
        directory = self.root / task_id
        directory.mkdir(exist_ok=True)
        original = result(kind)
        original["metadata"]["started_at"] = "2026-11-17T12:00:00+00:00" if kind == "runtime" else "2026-11-17T13:00:00+00:00"
        case = original["results"][0]
        if kind == "correctness":
            case = case["individual_run_test_results"][0]
        case["trace"] = json.dumps([{"role": "assistant", "content": "offline history answer"}])
        for report_kind in ("json", "html"):
            path = directory / f"{kind}.{report_kind}"
            path.write_text("[]" if report_kind == "json" else "<html>offline</html>")
            original["artifacts"][f"{report_kind}_path"] = str(path)
        save_run(original, task_id=task_id, root=self.root)
        return load_run(task_id, root=self.root)

    def history_snapshot(self):
        return {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in self.root.rglob("*") if path.is_file()}

    def test_history_discovers_orders_loads_and_preserves_files_without_tasks(self):
        runtime = self.history_fixture()
        correctness = self.history_fixture("correctness")
        save_run({"metadata": {"run_id": "minimal"}}, root=self.root)
        (self.root / "broken").mkdir()
        (self.root / "broken/run_result.json").write_text("{invalid")
        before = self.history_snapshot()
        with patch("GUI.services.task_manager.TaskManager.start") as start, patch("main.core.run_store.save_run") as save:
            options = history_options(list_runs(root=self.root))
            self.assertEqual(list(options), ["correctness-history", "runtime-history", "minimal"])
            for name, expected in [("runtime-history", runtime), ("correctness-history", correctness)]:
                self.assertEqual(load_run(name, root=self.root), expected)
                self.assertEqual(dashboard(options[name])["yes"], 1)
            self.assertIn("未记录", history_label(options["minimal"], "zh"))
            # A new manager has no state to supply, yet disk records remain usable.
            self.assertIsNone(self.manager(store_root=self.root).snapshot())
            self.assertEqual(self.history_snapshot(), before)
            start.assert_not_called()
            save.assert_not_called()
        self.assertEqual(list_runs(root=self.root / "absent"), [])
        self.assertFalse((self.root / "absent").exists())

    def test_history_orders_timezones_and_handles_missing_or_invalid_times(self):
        records = [
            {"metadata": {"task_id": "newer", "started_at": "2026-11-17T12:00:00Z"}},
            {"metadata": {"task_id": "older", "started_at": "2026-11-17T19:00:00+08:00"}},
            {"metadata": {"task_id": "unknown", "started_at": ["bad"], "finished_at": "bad"}},
            {"metadata": None},
        ]
        self.assertEqual(list(history_options(records)), ["newer", "older", "unknown"])

    def test_history_report_paths_allow_only_files_inside_experiment_root(self):
        original = self.history_fixture()
        path = Path(original["artifacts"]["json_path"])
        for allowed in (str(path), "runtime-history/runtime.json", self.root.name + "/runtime-history/runtime.json"):
            self.assertEqual(read_report({"json_path": allowed}, "json", allowed_root=self.root)[1], path.read_bytes())
        with TemporaryDirectory() as outside:
            secret = Path(outside) / "private.json"
            secret.write_text("private content")
            (self.root / "escape.json").symlink_to(secret)
            (self.root / "escape-dir").symlink_to(outside, target_is_directory=True)
            for denied in (str(secret), "../" + secret.parent.name + "/private.json", "escape.json", "escape-dir/private.json"):
                with patch.object(Path, "read_bytes", side_effect=AssertionError("Outside content must never be read")):
                    report = read_report({"json_path": denied}, "json", allowed_root=self.root)
                self.assertEqual(report[2], "report_outside_store")
        for missing in (None, "missing.json", "."):
            self.assertEqual(read_report({"json_path": missing}, "json", allowed_root=self.root)[2], "report_missing")

    @contextmanager
    def history_app(self, manager=None):
        from streamlit.testing.v1 import AppTest
        manager = manager or self.manager(store_root=self.root)
        with patch("main.core.run_store.DEFAULT_RESULTS_DIR", self.root), patch(
            "GUI.services.task_manager.get_task_manager", return_value=manager
        ), patch.object(TaskManager, "start", side_effect=AssertionError("History must not start tests")) as start, patch(
            "main.core.run_store.save_run", side_effect=AssertionError("History must not save records")
        ), patch("socket.socket.connect", side_effect=AssertionError("Offline test forbids network")):
            yield AppTest, start

    @unittest.skipUnless(find_spec("streamlit"), "Use the existing project venv for Streamlit AppTest")
    def test_history_page_survives_fresh_session_switching_and_language_changes(self):
        self.history_fixture()
        self.history_fixture("correctness")
        before = self.history_snapshot()
        with self.history_app() as (AppTest, start):
            app = AppTest.from_file(str(PROJECT_ROOT / "GUI/app.py"), default_timeout=30).run()
            app.radio(key="result_view").set_value("history").run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key="history_task").value, "correctness-history")
            app.selectbox(key="history_task").set_value("runtime-history").run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get("download_button")), 2)
            self.assertEqual(json.loads(app.json[-1].value)["metadata"]["task_id"], "runtime-history")
            self.assertTrue(any(code.value == "offline history answer" for code in app.code))
            self.assertEqual(app.selectbox(key="test_type").value, "Runtime")
            app.radio(key="language").set_value("en").run()
            self.assertFalse(app.exception)
            self.assertIn("History", app.radio(key="result_view").options)
            # Fresh AppTest session + empty manager exercises disk recovery.
            fresh = AppTest.from_file(str(PROJECT_ROOT / "GUI/app.py"), default_timeout=30)
            fresh.query_params.update(app.query_params)
            fresh.run()
            self.assertFalse(fresh.exception)
            self.assertEqual(fresh.radio(key="result_view").value, "history")
            self.assertEqual(fresh.selectbox(key="history_task").value, "runtime-history")
            self.assertEqual(len(fresh.metric), 4)
            fresh.button(key="history_refresh").click().run()
            fresh.radio(key="result_view").set_value("current").run()
            self.assertFalse(fresh.exception)
            self.assertFalse(fresh.button(key="start_test").disabled)
            start.assert_not_called()
        self.assertEqual(self.history_snapshot(), before)

    @unittest.skipUnless(find_spec("streamlit"), "Use the existing project venv for Streamlit AppTest")
    def test_history_page_handles_failure_missing_reports_deletion_and_empty_store(self):
        failed = self.history_fixture(task_id="failed-history")
        failed.update(status="failed", error="saved offline error", results=[], artifacts={})
        save_run(failed, task_id="failed-history", root=self.root)
        save_run({"metadata": {"run_id": "minimal"}}, root=self.root)
        (self.root / "broken").mkdir()
        (self.root / "broken/run_result.json").write_text("bad JSON")
        before = self.history_snapshot()
        with self.history_app() as (AppTest, start):
            app = AppTest.from_file(str(PROJECT_ROOT / "GUI/app.py"), default_timeout=30)
            app.query_params.update(view="history", history_task="failed-history")
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(any("saved offline error" in error.value for error in app.error))
            self.assertEqual(len(app.get("download_button")), 0)
            for kind in ("JSON", "HTML"):
                self.assertTrue(any(kind + " 报告未生成" in message.value for message in app.info))
            app.selectbox(key="history_task").set_value("minimal").run()
            self.assertFalse(app.exception)
            self.assertEqual(self.history_snapshot(), before)
            # Simulate deletion after listing, before the selected record reloads.
            listed = list_runs(root=self.root)
            (self.root / "minimal/run_result.json").unlink()
            with patch("main.core.run_store.list_runs", return_value=listed):
                app.button(key="history_refresh").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("已删除" in warning.value for warning in app.warning))
            app.button(key="history_refresh").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key="history_task").value, "failed-history")
            start.assert_not_called()
        self.root = self.root / "does-not-exist"
        with self.history_app() as (AppTest, _):
            empty = AppTest.from_file(str(PROJECT_ROOT / "GUI/app.py"), default_timeout=30)
            empty.query_params["view"] = "history"
            empty.run()
            self.assertFalse(empty.exception)
            self.assertTrue(any("暂无可读取" in message.value for message in empty.info))
            self.assertFalse(self.root.exists())

    @unittest.skipUnless(find_spec("streamlit"), "Use the existing project venv for Streamlit AppTest")
    def test_history_keeps_running_task_locked_and_blocks_external_reports(self):
        saved = self.history_fixture()
        saved["artifacts"]["html_path"] = "/etc/passwd"
        save_run(saved, task_id="runtime-history", root=self.root)
        context = start_run()
        manager = self.manager(store_root=self.root)
        manager._task = TaskSnapshot(context.run_id, self.config(), "running", context.started_at, context.started_perf)
        before = self.history_snapshot()
        with self.history_app(manager) as (AppTest, start):
            app = AppTest.from_file(str(PROJECT_ROOT / "GUI/app.py"), default_timeout=30)
            app.query_params["view"] = "history"
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(app.button(key="start_test").disabled)
            self.assertTrue(app.selectbox(key="test_type").disabled)
            self.assertTrue(app.selectbox(key="tool_id").disabled)
            self.assertTrue(app.text_input(key="model_name").disabled)
            self.assertTrue(app.number_input(key="iterations").disabled)
            self.assertEqual(len(app.get("download_button")), 1)
            self.assertTrue(any("已拒绝读取" in message.value for message in app.info))
            app.radio(key="language").set_value("en").run()
            self.assertFalse(app.exception)
            self.assertTrue(app.button(key="start_test").disabled)
            self.assertEqual(app.query_params["task"], [context.run_id])
            start.assert_not_called()
        self.assertEqual(self.history_snapshot(), before)


    def wait_for(self, condition, timeout=10):
        deadline = monotonic() + timeout
        while not condition():
            if monotonic() >= deadline:
                self.fail("Timed out waiting for worker state")
            sleep(0.01)

    def real_manager(self, **fixture):
        manager = TaskManager(partial(process_fixture, root=str(self.root), **fixture), store_root=self.root)
        def cleanup():
            task = manager.snapshot()
            if task and task.status in ("running", "cancelling"):
                manager.cancel(task.task_id)
                self.wait_for(lambda: manager.snapshot().status not in ("running", "cancelling"))
        self.addCleanup(cleanup)
        return manager

    def test_real_process_normal_result_and_owned_temporary_cleanup(self):
        previous = self.history_fixture()
        before = self.history_snapshot()
        manager = self.real_manager()
        task = manager.start(self.config())
        self.wait_for(lambda: manager.snapshot().status != "running")
        finished = manager.snapshot()
        self.assertEqual((finished.status, finished.persistence_status), ("completed", "saved"))
        self.assertEqual(manager.last_result(), finished.result)
        self.assertEqual(finished.result["metadata"]["worker_exitcode"], 0)
        self.assertFalse(finished.result["metadata"]["worker_forced"])
        self.assertIsNone(manager._worker)
        self.assertIsNone(manager._cancel_event)
        directory = self.root / task.task_id
        self.assertEqual({path.name for path in directory.iterdir()}, {"run_result.json", "runtime.json", "runtime.html"})
        self.assertEqual(Path(finished.result["artifacts"]["json_path"]).parent, directory)
        self.assertEqual(load_run(task.task_id, root=self.root)["metadata"]["run_id"], finished.result["metadata"]["run_id"])
        self.assertEqual(load_run("runtime-history", root=self.root), previous)
        for path, values in before.items():
            self.assertEqual((Path(path).read_bytes(), Path(path).stat().st_mtime_ns), values)
        self.assertEqual(manager.cancel(task.task_id).status, "completed")

    def test_real_blocking_process_cancel_stops_work_reaps_and_allows_new_task(self):
        manager = self.real_manager(block=True)
        task = manager.start(self.config())
        self.wait_for(lambda: (self.root / "entered").exists())
        worker = manager._worker
        pid = worker.pid
        self.assertEqual(manager.cancel(task.task_id, expected_task_id="wrong").status, "running")
        self.assertEqual(manager.cancel("old-task").task_id, task.task_id)
        accepted = manager.cancel(task.task_id)
        self.assertEqual(accepted.status, "cancelling")
        self.assertIn(manager.cancel(task.task_id).status, ("cancelling", "cancelled"))
        if manager.snapshot().status == "cancelling":
            self.assertEqual(manager.start(self.config()).task_id, task.task_id)
        self.wait_for(lambda: manager.snapshot().status == "cancelled")
        finished = manager.snapshot()
        self.assertIsNone(finished.result)
        self.assertIsNone(manager._worker)
        self.assertIsNone(manager._cancel_event)
        self.assertTrue(worker.stopped)
        if os.name == "posix":
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        heartbeat = (self.root / "heartbeat").read_bytes()
        sleep(0.1)
        self.assertEqual((self.root / "heartbeat").read_bytes(), heartbeat)
        stored = load_run(task.task_id, root=self.root)
        self.assertEqual(stored["status"], "cancelled")
        self.assertTrue(stored["summary"]["incomplete"])
        self.assertEqual(stored["metadata"]["task_id"], task.task_id)
        self.assertEqual(stored["metadata"]["run_id"], task.core_run_id)
        self.assertEqual(stored["metadata"]["worker_pid"], pid)
        self.assertTrue(stored["metadata"]["worker_reaped"])
        self.assertEqual(stored["artifacts"], {"json_path": None, "html_path": None})
        self.assertEqual([p.name for p in (self.root / task.task_id).iterdir()], ["run_result.json"])
        manager._execute = partial(process_fixture, root=str(self.root))
        next_task = manager.start(self.config(), expected_task_id=task.task_id)
        self.assertNotEqual(next_task.task_id, task.task_id)
        self.assertEqual(manager.cancel(task.task_id).task_id, next_task.task_id)
        self.wait_for(lambda: manager.snapshot().status == "completed")
        self.assertEqual(manager.snapshot().task_id, next_task.task_id)
        self.assertEqual(load_run(task.task_id, root=self.root)["status"], "cancelled")

    @unittest.skipUnless(os.name == "posix", "POSIX process group check")
    def test_cancel_also_stops_spawned_descendant_and_preserves_unrelated_file(self):
        manager = self.real_manager(block=True, descendant=True)
        task = manager.start(self.config())
        self.wait_for(lambda: (self.root / "descendant-heartbeat").exists())
        (self.root / "unrelated.tmp").write_text("keep")
        pid = int((self.root / "descendant-pid").read_text())
        manager.cancel(task.task_id)
        self.wait_for(lambda: manager.snapshot().status == "cancelled")
        heartbeat = (self.root / "descendant-heartbeat").read_bytes()
        sleep(0.1)
        self.assertEqual((self.root / "descendant-heartbeat").read_bytes(), heartbeat)
        # Orphan zombies may await init reaping; they cannot run or hold sockets.
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists():
            self.assertEqual(stat.read_text().split()[2], "Z")
        self.assertEqual((self.root / "unrelated.tmp").read_text(), "keep")

    def test_cancel_before_worker_launch_does_not_execute(self):
        manager = TaskManager(store_root=self.root)
        with patch("GUI.services.task_manager.Thread") as thread, patch("GUI.services.task_manager.TaskProcess") as factory:
            task = manager.start(self.config())
            args = thread.call_args.kwargs["args"]
            manager.cancel(task.task_id)
            manager._run(*args)
            factory.assert_not_called()
        self.assertEqual(manager.snapshot().status, "cancelled")
        self.assertEqual(load_run(task.task_id, root=self.root)["status"], "cancelled")

    def test_cancel_interrupts_blocked_socket_without_starting_next_call(self):
        manager = self.real_manager(socket_block=True)
        task = manager.start(self.config())
        self.wait_for(lambda: (self.root / "entered").exists())
        worker = manager._worker
        manager.cancel(task.task_id)
        self.wait_for(lambda: manager.snapshot().status == "cancelled")
        self.assertTrue(worker.stopped)
        self.assertFalse((self.root / "unexpected-next-call").exists())
        self.assertEqual([path.name for path in (self.root / task.task_id).iterdir()], ["run_result.json"])

    def test_model_provider_has_bounded_timeout_and_retry_configuration(self):
        module = ModuleType("langchain_openai")
        module.ChatOpenAI = Mock()
        with patch.dict(sys.modules, {"langchain_openai": module}), patch.dict(
            "os.environ", {"DEEPSEEK_API_KEY": "offline-private-value"}
        ):
            provider = runpy.run_path(str(PROJECT_ROOT / "main/core/model_provider.py"))
            provider["create_deepseek_llm"]()
        self.assertEqual(module.ChatOpenAI.call_args.kwargs["timeout"], 60)
        self.assertEqual(module.ChatOpenAI.call_args.kwargs["max_retries"], 1)

    @unittest.skipUnless(find_spec("langchain_openai"), "Use the project venv for the default Runner import check")
    def test_default_spawned_runner_without_key_saves_failure_with_core_identity(self):
        # PubMed construction is local; the missing-key check runs before tester.test().
        manager = TaskManager(store_root=self.root)
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}):
            task = manager.start(RunConfig("Correctness", "pubmed", "offline-model", 1, 0, 0))
            self.wait_for(lambda: manager.snapshot().status != "running", timeout=20)
        finished = manager.snapshot()
        self.assertEqual((finished.status, finished.persistence_status), ("failed", "saved"))
        self.assertIn("DEEPSEEK_API_KEY is not set", finished.error)
        stored = load_run(task.task_id, root=self.root)
        self.assertEqual(stored["metadata"]["run_id"], task.core_run_id)
        self.assertTrue(stored["metadata"]["worker_reaped"])
        self.assertEqual(stored["metadata"]["worker_exitcode"], 0)
        self.assertFalse(stored["metadata"]["worker_forced"])
        self.assertEqual([p.name for p in (self.root / task.task_id).iterdir()], ["run_result.json"])

    def test_accepted_cancel_wins_late_result_or_failure_until_reaping_finishes(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                stopping, release = Event(), Event()
                self.addCleanup(release.set)
                class LateWorker(InlineWorker):
                    def poll(self, cancelled=None):
                        if fail:
                            raise RuntimeError("late failure")
                        self.value = result()
                        return True
                    def stop(self):
                        stopping.set()
                        release.wait(5)
                manager = TaskManager(store_root=self.root, worker_factory=LateWorker)
                task = manager.start(self.config())
                self.assertTrue(stopping.wait(2))
                manager.cancel(task.task_id)
                self.assertEqual(manager.snapshot().status, "cancelling")
                self.assertEqual(manager.start(self.config()).task_id, task.task_id)
                self.assertIsNone(load_run(task.task_id, root=self.root))
                release.set()
                self.wait_for(lambda: manager.snapshot().status == "cancelled")
                self.assertEqual(load_run(task.task_id, root=self.root)["status"], "cancelled")
                self.assertIsNone(manager.snapshot().result)
                self.assertIsNone(manager.last_result())

    @unittest.skipUnless(find_spec("streamlit"), "Use the project venv for Streamlit AppTest")
    def test_cancelled_gui_hides_previous_result_cleans_only_owned_widgets_and_unlocks(self):
        from dataclasses import replace
        manager = self.manager(store_root=self.root)
        context = start_run()
        task = TaskSnapshot(context.run_id, self.config(), "running", context.started_at, context.started_perf, core_run_id="cancelled-core")
        manager._task = task
        manager._cancel_event = Event()
        manager._last_result = result()
        with patch("main.core.run_store.DEFAULT_RESULTS_DIR", self.root), patch(
            "GUI.services.task_manager.get_task_manager", return_value=manager
        ), patch.object(TaskManager, "start", side_effect=AssertionError("No new task")):
            from streamlit.testing.v1 import AppTest
            app = AppTest.from_file(str(PROJECT_ROOT / "GUI/app.py"), default_timeout=30).run()
            self.assertFalse(app.exception)
            self.assertFalse(app.button(key="cancel_test").disabled)
            app.button(key="cancel_test").click().run()
            self.assertEqual(manager.snapshot().status, "cancelling")
            self.assertTrue(app.button(key="cancel_test").disabled)
            self.assertTrue(app.button(key="start_test").disabled)
            self.assertEqual(len(app.metric), 0)
            manager._task = replace(manager.snapshot(), cleanup_error="offline reaping error")
            app.run()
            self.assertTrue(any("任务尚未确认停止" in error.value for error in app.error))
            app.radio(key="language").set_value("en").run()
            self.assertTrue(any(info.value == "Cancelling task…" for info in app.info))
            manager._task = replace(task, status="cancelled")
            app.session_state["cancelled-core_filter"] = "all"
            app.session_state["other-run_filter"] = "keep"
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(any(info.value == "Task cancelled." for info in app.info))
            self.assertEqual(len(app.metric), 0)
            self.assertEqual(len(app.get("download_button")), 0)
            self.assertNotIn("cancelled-core_filter", app.session_state)
            self.assertEqual(app.session_state["other-run_filter"], "keep")
            self.assertEqual(app.text_input(key="model_name").value, "offline-model")
            self.assertFalse(app.button(key="start_test").disabled)
            self.assertTrue(app.button(key="cancel_test").disabled)


    def finished_worker(self, **fixture):
        worker = TaskProcess(
            "lifecycle", self.config(), store_root=self.root, run_context=start_run(),
            execute=partial(lifecycle_fixture, root=str(self.root), **fixture),
        )
        self.addCleanup(worker.close)
        worker.start()
        self.wait_for(worker.poll, timeout=20)
        payload = worker.response()
        return worker, payload

    def test_done_success_and_runner_failure_exit_zero_without_forced_stop(self):
        for fixture in ({}, {"failure": True}, {"failed_result": True}):
            with self.subTest(fixture=fixture):
                worker, payload = self.finished_worker(**fixture)
                self.assertTrue(worker.done)
                with patch.object(worker, "stop", side_effect=AssertionError("Normal exit must not force stop")), patch(
                    "GUI.services.task_worker.os.killpg", side_effect=AssertionError("Normal exit must not SIGKILL"), create=True
                ):
                    worker.finish()
                self.assertEqual(worker.exitcode, 0)
                self.assertEqual(worker.process.exitcode, 0)
                self.assertFalse(worker.forced)
                self.assertTrue(worker.stopped)
                self.assertEqual(worker.shutdown_reason, "natural")
                if fixture.get("failure"):
                    self.assertIn("offline Runner failure", payload["error"])
                if os.name == "posix":
                    self.assertFalse(_group_running(worker.pid))
                worker.close()

    def test_returned_runner_failure_keeps_failed_status_with_natural_exit(self):
        manager = TaskManager(
            partial(lifecycle_fixture, root=str(self.root), failed_result=True),
            store_root=self.root,
        )
        task = manager.start(self.config())
        self.wait_for(lambda: manager.snapshot().status != "running")
        finished = manager.snapshot()
        self.assertEqual(finished.status, "failed")
        stored = load_run(task.task_id, root=self.root)
        self.assertEqual(stored["status"], "failed")
        self.assertEqual(stored["metadata"]["worker_exitcode"], 0)
        self.assertFalse(stored["metadata"]["worker_forced"])
        self.assertIsNone(manager.last_result())

    def test_exit_finalizer_timeout_forces_reap_and_cancel_interrupts_exit_wait(self):
        for cancel_wait in (False, True):
            with self.subTest(cancel_wait=cancel_wait):
                worker, _ = self.finished_worker(hang_exit=True)
                event = Event()
                if cancel_wait:
                    from threading import Timer
                    timer = Timer(0.05, event.set)
                    timer.start()
                    self.addCleanup(timer.join)
                started = monotonic()
                worker.finish(event, timeout=0.1 if not cancel_wait else 10)
                self.assertLess(monotonic() - started, 3)
                self.assertTrue(worker.stopped)
                self.assertTrue(worker.forced)
                self.assertNotEqual(worker.exitcode, 0)
                self.assertEqual(worker.shutdown_reason, "cancelled" if cancel_wait else "exit_timeout")
                worker.close()

    @unittest.skipUnless(os.name == "posix", "POSIX descendant group check")
    def test_normal_done_with_live_descendant_requires_forced_tree_cleanup(self):
        worker, _ = self.finished_worker(descendant=True)
        self.wait_for(lambda: (self.root / "descendant-heartbeat").exists())
        original_stop = worker.stop
        def verify_before_stop():
            self.assertFalse(worker.stopped)
            self.assertTrue(_group_running(worker.pid))
            original_stop()
        with patch.object(worker, "stop", side_effect=verify_before_stop) as stop:
            worker.finish()
        stop.assert_called_once()
        self.assertTrue(worker.stopped)
        self.assertTrue(worker.forced)
        self.assertEqual(worker.exitcode, 0)  # Root exited naturally, descendant did not.
        self.assertEqual(worker.shutdown_reason, "remaining_processes")
        self.assertFalse(_group_running(worker.pid))

    def test_persistent_reap_failure_is_bounded_reported_and_keeps_task_locked(self):
        class BrokenReaper(InlineWorker):
            def finish(self, cancelled=None):
                raise OSError("cannot confirm worker exit")
            def stop(self):
                raise OSError("still cannot confirm worker exit")
        manager = TaskManager(lambda *args: result(), store_root=self.root, worker_factory=BrokenReaper)
        with self.assertLogs("GUI.services.task_manager", level="ERROR") as logs:
            task = manager.start(self.config())
            self.wait_for(lambda: manager.snapshot().cleanup_error is not None and manager._monitor is None)
        self.assertEqual(len(logs.output), 4)
        self.assertEqual(manager.snapshot().status, "cancelling")
        self.assertIn("cannot confirm", manager.snapshot().cleanup_error)
        self.assertEqual(manager.start(self.config()).task_id, task.task_id)
        self.assertIsNotNone(manager._worker)
        self.assertIsNone(load_run(task.task_id, root=self.root))
        self.assertIsNone(manager.last_result())

    def isolated_shutdown(self, *, force=False, **fixture):
        # A separate interpreter must reach shutdown to observe tracker stderr.
        script = r"""
import json, os, sys, tempfile, time
from pathlib import Path
from functools import partial
from GUI.services.task_worker import TaskProcess
from GUI.services.task_manager import RunConfig
from GUI.tests.test_run_store import lifecycle_fixture
from main.core.run_result import start_run
options = json.loads(sys.argv[1])
force = options.pop('force')
with tempfile.TemporaryDirectory() as root:
    worker = TaskProcess('isolated', RunConfig('Runtime','duckduckgo','offline',1,0,0),
        store_root=root, run_context=start_run(), execute=partial(lifecycle_fixture, root=root, **options))
    try:
        worker.start()
        deadline = time.monotonic() + 20
        while not worker.poll():
            if time.monotonic() > deadline:
                raise RuntimeError('Worker timeout')
        payload = worker.response()
        names_path = Path(root, 'semaphore-names.json')
        names = json.loads(names_path.read_text()) if names_path.exists() else []
        lock_path = Path(root, 'dependency-lock.json')
        dependency_lock = json.loads(lock_path.read_text()) if lock_path.exists() else None
        if dependency_lock and dependency_lock.get('tqdm_semaphore'):
            names.append(dependency_lock['tqdm_semaphore'])
        if force:
            worker.stop()
        else:
            worker.finish()
        remaining = [name for name in names if name and Path('/dev/shm', 'sem.' + name.lstrip('/')).exists()]
        print(json.dumps({'exitcode':worker.exitcode, 'forced':worker.forced,
            'reason':worker.shutdown_reason, 'remaining_semaphores':remaining,
            'dependency_lock':dependency_lock, 'error':payload['error']}))
    finally:
        worker.close()
"""
        completed = subprocess.run(
            [sys.executable, "-B", "-c", script, json.dumps({"force": force, **fixture})],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=35,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout.strip().splitlines()[-1]), completed.stderr

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux named-semaphore /dev/shm check")
    def test_independent_interpreter_named_semaphores_unregister_on_natural_exit(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                info, stderr = self.isolated_shutdown(semaphores=True, failure=failure)
                self.assertEqual(info["exitcode"], 0)
                self.assertFalse(info["forced"])
                self.assertEqual(info["remaining_semaphores"], [])
                self.assertNotIn("resource_tracker", stderr)
        # Positive control demonstrates the detector sees deferred warnings.
        info, stderr = self.isolated_shutdown(force=True, semaphores=True)
        self.assertNotEqual(info["exitcode"], 0)
        self.assertEqual(len(info["remaining_semaphores"]), 2)
        self.assertIn("2 leaked semaphore", stderr)

    @unittest.skipUnless(find_spec("langchain_openai") and os.name == "posix", "Use the project venv for Runtime dependencies")
    def test_real_runtime_dependencies_natural_exit_has_no_tracker_warning(self):
        plain, plain_err = self.isolated_shutdown()
        loaded, loaded_err = self.isolated_shutdown(dependencies=True)
        self.assertEqual((plain["exitcode"], loaded["exitcode"]), (0, 0))
        self.assertFalse(loaded["forced"])
        self.assertNotIn("resource_tracker", plain_err)
        self.assertNotIn("resource_tracker", loaded_err)
        self.assertTrue(loaded["dependency_lock"]["tqdm_semaphore"])
        self.assertEqual(loaded["remaining_semaphores"], [])


if __name__ == "__main__":
    unittest.main()
