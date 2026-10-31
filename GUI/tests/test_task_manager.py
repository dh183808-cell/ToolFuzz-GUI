import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch, sentinel

from GUI.services.task_manager import RunConfig, TaskManager, execute_test
from GUI.tests.helpers import result_for, wait_for_finish


CONFIG = RunConfig("Runtime", "duckduckgo", "offline-model", 3, 0.4, 0)


class TaskManagerTests(unittest.TestCase):
    def test_concurrent_submissions_only_start_one_test(self):
        entered = Event()
        release = Event()
        calls = []

        def execute(task_id, config):
            calls.append((task_id, config))
            entered.set()
            if not release.wait(10):
                raise TimeoutError("Offline fixture was not released")
            return result_for(config)

        manager = TaskManager(execute)
        self.addCleanup(release.set)
        with ThreadPoolExecutor(max_workers=8) as submitters:
            tasks = list(submitters.map(lambda _: manager.start(CONFIG), range(8)))
        self.assertTrue(entered.wait(2))
        self.assertEqual(len({task.task_id for task in tasks}), 1)
        self.assertEqual(len(calls), 1)
        # Even a different submission must reattach, not replace running config.
        duplicate = manager.start(replace(CONFIG, model_name="accidental-edit"))
        self.assertEqual(duplicate.config, CONFIG)
        release.set()
        completed = wait_for_finish(manager)
        self.assertEqual(completed.status, "completed")
        self.assertEqual(manager.last_result()["model"], CONFIG.model_name)

    def test_failed_worker_preserves_previous_result_and_allows_retry(self):
        calls = []

        def execute(task_id, config):
            calls.append(task_id)
            if len(calls) == 2:
                raise RuntimeError("offline failure")
            return result_for(config)

        manager = TaskManager(execute)
        first = manager.start(CONFIG)
        wait_for_finish(manager)
        previous = manager.last_result()
        manager.start(CONFIG)
        failed = wait_for_finish(manager)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error, "RuntimeError: offline failure")
        self.assertIs(manager.last_result(), previous)
        third = manager.start(CONFIG)
        self.assertEqual(wait_for_finish(manager).status, "completed")
        self.assertEqual(len({first.task_id, failed.task_id, third.task_id}), 3)

    def test_worker_exit_does_not_leave_running_lock(self):
        def execute(task_id, config):
            raise SystemExit("offline exit")

        manager = TaskManager(execute)
        manager.start(CONFIG)
        task = wait_for_finish(manager)
        self.assertEqual(task.status, "failed")
        self.assertEqual(task.error, "SystemExit: offline exit")

    def test_thread_start_failure_does_not_leave_running_lock(self):
        manager = TaskManager(lambda task_id, config: result_for(config))
        with patch("GUI.services.task_manager.Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("cannot start")
            task = manager.start(CONFIG)
        self.assertEqual(task.status, "failed")
        self.assertEqual(task.error, "RuntimeError: cannot start")
        manager.start(CONFIG)
        self.assertEqual(wait_for_finish(manager).status, "completed")

    def test_runner_dispatch_and_reports_are_isolated_by_task(self):
        with (
            patch("main.core.tool_registry.create_tool", return_value=sentinel.tool),
            patch("main.core.runtime_runner.run_runtime", return_value={}) as runtime,
            patch("main.core.correctness_runner.run_correctness", return_value={}) as correctness,
        ):
            execute_test("first-task", CONFIG)
            execute_test("second-task", CONFIG)
            execute_test(
                "third-task",
                replace(CONFIG, test_type="Correctness", judge_temperature=0.2),
            )
        first_kwargs = runtime.call_args_list[0].kwargs
        second_kwargs = runtime.call_args_list[1].kwargs
        self.assertEqual(first_kwargs["tool"], sentinel.tool)
        self.assertEqual(first_kwargs["fuzzer_iters"], 3)
        self.assertEqual(first_kwargs["output_name"], "runtime")
        self.assertEqual(Path(first_kwargs["results_dir"]).name, "first-task")
        self.assertNotEqual(first_kwargs["results_dir"], second_kwargs["results_dir"])
        self.assertEqual(correctness.call_args.kwargs["prompt_set_iters"], 3)
        self.assertEqual(correctness.call_args.kwargs["judge_temperature"], 0.2)


if __name__ == "__main__":
    unittest.main()
