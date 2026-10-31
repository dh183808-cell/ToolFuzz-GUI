"""Offline regression tests for search failures during Correctness runs."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException
from langchain_core.tools import ToolException
from langchain_community.tools import DuckDuckGoSearchRun

from main.compat.correctness import CompatibleCorrectnessTester
from main.core.correctness_runner import run_correctness
from src.toolfuzz.agent_executors.agent_executor import AgentResponse
from src.toolfuzz.correctness.correctness_oracle import CorrectnessOracle


TIMEOUT_MESSAGE = (
    "error sending request for url (https://search.yahoo.com/search) > operation timed out"
)


def successful_response():
    return AgentResponse(
        agent_response="offline answer",
        trace=[{"role": "tool", "content": "offline result"}],
        is_tool_invoked=True,
        is_raised_exception=False,
        exception="",
        tool_output="offline result",
        tool_args='{"query": "offline"}',
    )


class CorrectnessSearchFailureTests(unittest.TestCase):
    def _tester_with_error(self, error):
        tester = CompatibleCorrectnessTester.__new__(CompatibleCorrectnessTester)
        tester.agent = Mock(side_effect=error)
        tester.context_resetter = Mock()
        return tester

    def test_search_errors_become_individual_failures_and_reset_context_once(self):
        for error in (
            TimeoutException(TIMEOUT_MESSAGE),
            RatelimitException("offline rate limit"),
            DDGSException("offline connection error"),
        ):
            with self.subTest(error=type(error).__name__):
                tester = self._tester_with_error(error)
                agent_result, result = tester.run_prompt("offline query")
                self.assertIsNone(agent_result)
                self.assertTrue(result.tool_failure)
                self.assertEqual(result.prompt, "offline query")
                self.assertIn(type(error).__name__, result.llm_agent_out_reason)
                self.assertIn("not evaluated", result.llm_agent_out_reason)
                trace = json.loads(result.trace)
                self.assertTrue(trace["evaluation_skipped"])
                self.assertEqual(trace["tool_error"], str(error))
                tester.context_resetter.reset_context.assert_called_once_with()

    def test_unexpected_agent_bug_still_propagates(self):
        tester = self._tester_with_error(RuntimeError("offline framework bug"))
        with self.assertRaisesRegex(RuntimeError, "offline framework bug"):
            tester.run_prompt("offline query")
        tester.context_resetter.reset_context.assert_called_once_with()

    def test_upstream_tool_exception_handling_is_preserved(self):
        tester = self._tester_with_error(ToolException("offline tool error"))
        agent_result, result = tester.run_prompt("offline query")
        self.assertIsNone(agent_result)
        self.assertTrue(result.tool_failure)
        tester.context_resetter.reset_context.assert_called_once_with()

    def run_offline(self, outcomes, results_dir):
        agent = Mock(side_effect=outcomes)
        oracle = Mock()
        oracle.evaluate_tool_output.side_effect = CorrectnessOracle.evaluate_tool_output
        oracle.evaluate_tool_arguments.side_effect = CorrectnessOracle.evaluate_tool_arguments
        oracle.agent_out_correctness.return_value = (9, "offline judge")

        # The second template lets the existing prompt-set limit stop generation.
        prompt_sets = [
            ("offline template", [f"query {i}" for i in range(len(outcomes))], "expected"),
            ("unused template", [], "expected"),
        ]
        with (
            patch("main.core.correctness_runner.create_deepseek_llm", return_value=object()),
            patch("main.core.correctness_runner.CompatibleReactAgentNew", return_value=agent),
            patch("main.compat.correctness.CompatibleCorrectnessOracle", return_value=oracle),
            patch("main.compat.correctness.CompatibleCorrectnessPromptGenerator") as generator,
            patch("src.toolfuzz.utils.shutil.copy"),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            generator.return_value.generate_prompt.side_effect = lambda: iter(prompt_sets)
            result = run_correctness(
                DuckDuckGoSearchRun(),
                model_name="offline-model",
                results_dir=results_dir,
                output_name="correctness",
                prompt_set_iters=1,
            )
        return result, agent, oracle

    def test_timeout_does_not_abort_remaining_prompts_or_report_save(self):
        with tempfile.TemporaryDirectory() as folder:
            result, agent, oracle = self.run_offline(
                [TimeoutException(TIMEOUT_MESSAGE), successful_response()], Path(folder)
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["summary"]["tool_failure_count"], 1)
            self.assertEqual(result["summary"]["prompt_count"], 2)
            self.assertEqual(agent.call_count, 2)
            oracle.agent_out_correctness.assert_called_once_with(
                "offline answer", "offline result", "expected"
            )
            saved = json.loads(Path(result["artifacts"]["json_path"]).read_text())
            records = saved[0]["individual_run_test_results"]
            self.assertEqual([r["tool_failure"] for r in records], [True, False])
            self.assertIn("TimeoutException", records[0]["trace"])
            html = Path(result["artifacts"]["html_path"])
            self.assertTrue(html.is_file())
            self.assertIn("TimeoutException", html.read_text())

    def test_all_timeouts_save_report_without_claiming_an_inconsistency(self):
        with tempfile.TemporaryDirectory() as folder:
            result, agent, oracle = self.run_offline(
                [TimeoutException(TIMEOUT_MESSAGE), TimeoutException(TIMEOUT_MESSAGE)],
                Path(folder),
            )
            self.assertEqual(agent.call_count, 2)
            self.assertEqual(result["summary"]["tool_failure_count"], 2)
            oracle.agent_out_correctness.assert_not_called()
            oracle.evaluate_tool_output.assert_not_called()
            oracle.evaluate_tool_arguments.assert_not_called()
            prompt_set = result["results"][0]
            self.assertFalse(prompt_set["tool_output_inconsistency"])
            self.assertFalse(prompt_set["tool_arguments_inconsistency"])
            self.assertEqual(prompt_set["same_output_buckets"], [])
            self.assertTrue(Path(result["artifacts"]["html_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
