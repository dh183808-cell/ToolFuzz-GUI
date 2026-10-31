import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from GUI.services.task_manager import RunConfig, TaskManager
from GUI.tests.helpers import result_for, wait_for_finish


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


class AppInteractionTests(unittest.TestCase):
    def setUp(self):
        self.release = Event()
        self.calls = []
        self.failure = None

        def execute(task_id, config):
            self.calls.append((task_id, config))
            if not self.release.wait(15):
                raise TimeoutError("Offline fixture was not released")
            if self.failure:
                raise self.failure
            return result_for(config)

        self.manager = TaskManager(execute)
        patcher = patch(
            "GUI.services.task_manager.get_task_manager", return_value=self.manager
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.finish)

    def finish(self):
        self.release.set()
        if self.manager.snapshot() is not None:
            wait_for_finish(self.manager)

    def app(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
        self.assertEqual(list(app.exception), [])
        return app

    def assert_controls_disabled(self, app, disabled):
        for controls in (app.selectbox, app.number_input, app.text_input, app.button):
            for widget in controls:
                self.assertEqual(widget.disabled, disabled, widget.key)

    def test_page_rerun_and_fresh_session_reattach_to_same_running_test(self):
        app = self.app()
        app.selectbox(key="test_type").select("Correctness").run()
        app.selectbox(key="tool_id").select("pubmed").run()
        app.text_input(key="model_name").set_value("offline-custom-model").run()
        app.number_input(key="iterations").set_value(4).run()
        app.number_input(key="correctness_prompt_temperature").set_value(0.7).run()
        app.number_input(key="judge_temperature").set_value(0.2).run()
        app.button(key="start_test").click().run()
        self.assertEqual(list(app.exception), [])
        task = self.manager.snapshot()
        self.assertEqual(task.status, "running")
        self.assert_controls_disabled(app, True)
        self.assertEqual(app.query_params["task"], [task.task_id])

        # Simulate a rerun/queued accidental edit, despite the disabled widget.
        app.session_state["model_name"] = "accidental-edit"
        app.run()
        self.assertEqual(app.text_input(key="model_name").value, "offline-custom-model")
        self.assertEqual(self.manager.snapshot().config.model_name, "offline-custom-model")

        # A new AppTest has entirely new session_state, like a browser reload.
        refreshed = self.app()
        self.assert_controls_disabled(refreshed, True)
        self.assertEqual(refreshed.query_params["task"], [task.task_id])
        self.assertEqual(refreshed.selectbox(key="test_type").value, "Correctness")
        self.assertEqual(refreshed.selectbox(key="tool_id").value, "pubmed")
        self.assertEqual(refreshed.number_input(key="iterations").value, 4)
        self.assertEqual(refreshed.number_input(key="judge_temperature").value, 0.2)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(any("Running Correctness" in item.value for item in refreshed.info))

        self.finish()
        refreshed.run()
        self.assertEqual(list(refreshed.exception), [])
        self.assert_controls_disabled(refreshed, False)
        self.assertTrue(any("Test completed" in item.value for item in refreshed.success))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(refreshed.metric[0].value, "completed")

        completed_reload = self.app()
        self.assertEqual(completed_reload.metric[0].value, "completed")
        self.assertEqual(len(self.calls), 1)

    def test_second_session_cannot_launch_another_test(self):
        first = self.app()
        second = self.app()
        first.button(key="start_test").click().run()
        task_id = self.manager.snapshot().task_id
        # This tab still has the old enabled button; the server must reject it.
        second.button(key="start_test").click().run()
        self.assertEqual(list(second.exception), [])
        self.assertEqual(self.manager.snapshot().task_id, task_id)
        self.assert_controls_disabled(second, True)
        self.assertEqual(len(self.calls), 1)

    def test_failure_unlocks_controls_and_survives_reload(self):
        self.failure = RuntimeError("offline failure")
        app = self.app()
        app.button(key="start_test").click().run()
        self.finish()
        refreshed = self.app()
        self.assert_controls_disabled(refreshed, False)
        self.assertTrue(any("RuntimeError: offline failure" in e.value for e in refreshed.error))

    def test_queued_click_from_old_tab_does_not_restart_completed_test(self):
        first = self.app()
        old_tab = self.app()
        first.button(key="start_test").click().run()
        task_id = self.manager.snapshot().task_id
        self.finish()
        old_tab.button(key="start_test").click().run()
        self.assertEqual(list(old_tab.exception), [])
        self.assertEqual(self.manager.snapshot().task_id, task_id)
        self.assertEqual(len(self.calls), 1)
        self.assert_controls_disabled(old_tab, False)
        # Once that tab has rendered the completed task, a deliberate new run works.
        old_tab.button(key="start_test").click().run()
        wait_for_finish(self.manager)
        self.assertNotEqual(self.manager.snapshot().task_id, task_id)
        self.assertEqual(len(self.calls), 2)

    def test_completion_between_render_and_poll_unlocks_sidebar(self):
        self.manager.start(RunConfig("Runtime", "duckduckgo", "offline", 1, 0, 0))
        snapshot = self.manager.snapshot
        polls = 0

        def complete_during_poll():
            nonlocal polls
            polls += 1
            if polls == 2:
                self.finish()
            return snapshot()

        with patch.object(self.manager, "snapshot", side_effect=complete_during_poll):
            app = self.app()
        self.assert_controls_disabled(app, False)
        self.assertTrue(any("Test completed" in e.value for e in app.success))

    def test_new_task_between_render_and_poll_locks_sidebar(self):
        snapshot = self.manager.snapshot
        polls = 0

        def start_during_poll():
            nonlocal polls
            polls += 1
            if polls == 2:
                self.manager.start(RunConfig("Runtime", "pubmed", "offline", 2, 0, 0))
            return snapshot()

        with patch.object(self.manager, "snapshot", side_effect=start_during_poll):
            app = self.app()
        self.assert_controls_disabled(app, True)
        self.assertEqual(app.selectbox(key="tool_id").value, "pubmed")

    def test_stale_task_after_service_restart_is_not_shown_as_running(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=10)
        app.query_params["task"] = "previous-service-task"
        app.run()
        self.assertEqual(list(app.exception), [])
        self.assert_controls_disabled(app, False)
        self.assertTrue(any("no longer available" in e.value for e in app.warning))
        self.assertEqual(self.calls, [])

    def test_empty_model_does_not_launch_a_test(self):
        app = self.app()
        app.text_input(key="model_name").set_value("   ").run()
        app.button(key="start_test").click().run()
        self.assertEqual(list(app.exception), [])
        self.assertIsNone(self.manager.snapshot())
        self.assertTrue(any("must not be empty" in e.value for e in app.error))
        self.assert_controls_disabled(app, False)

    def test_completed_run_with_tool_failures_shows_warning(self):
        def completed_with_timeout(task_id, config):
            result = result_for(config)
            result["summary"]["tool_failure_count"] = 1
            return result

        self.manager = TaskManager(completed_with_timeout)
        self.manager.start(RunConfig("Correctness", "duckduckgo", "offline", 1, 0, 0))
        wait_for_finish(self.manager)
        # setUp's patch captured the old manager; replace its return value too.
        with patch("GUI.services.task_manager.get_task_manager", return_value=self.manager):
            app = self.app()
        self.assert_controls_disabled(app, False)
        self.assertEqual(list(app.success), [])
        self.assertTrue(any("1 tool failure(s)" in e.value for e in app.warning))


if __name__ == "__main__":
    unittest.main()
