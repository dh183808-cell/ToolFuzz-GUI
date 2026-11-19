"""Offline model-backend checks. No service credentials or network are used."""

import asyncio
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import pickle
import tempfile
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from openai import _base_client
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from GUI.services.task_manager import RunConfig, TaskManager, execute_test
from GUI.tests.test_run_store import InlineWorker, Record, offline_dependencies, result
from main.core.model_provider import (
    DEFAULT_MODEL, PROVIDERS, MissingCredentialsError, ModelCallError,
    ModelConfig, ModelConfigurationError, ModelRoles, check_model_calls,
    create_deepseek_llm, create_llm, create_role_models, resolve_roles,
)
from main.core.model_validation import (
    main as validate_main, probe_text, probe_tool_call, validate_config,
)
from main.core.run_store import list_runs, load_run, persist_run


# SDK 3 uses httpx2; older supported environments use httpx.
httpx = getattr(_base_client, "httpx2", None) or getattr(_base_client, "httpx")


@tool
def local_echo(value: int) -> str:
    """Echo a local integer without contacting external services."""
    return str(value)


def config_executor(task_id, config):
    # Runs in a real spawn process. Clients are deliberately absent from config.
    with patch("socket.socket.connect", side_effect=AssertionError("Offline")):
        value = result(config.test_type.lower())
        value["model"] = config.model_roles.agent.model_id
        value["config"]["model_roles"] = config.model_roles.summary()
        return value


class ModelBackendTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.addCleanup(patch.stopall)
        # Every request must go through the explicitly mocked SDK transport.
        patch("socket.socket.connect", side_effect=AssertionError("Offline test attempted network")).start()
        patch("socket.create_connection", side_effect=AssertionError("Offline test attempted network")).start()
        patch("socket.getaddrinfo", side_effect=AssertionError("Offline test attempted DNS")).start()
        self.keys = {name: f"offline-{provider}-private" for provider, (_, name) in PROVIDERS.items() if name}
        self.keys["VENDOR_AUTH"] = "tiny"
        patch.dict(os.environ, self.keys).start()

    def roles(self):
        return ModelRoles(
            ModelConfig("openrouter", "vendor/prompt", temperature=.4, timeout=12, max_retries=0),
            ModelConfig("siliconflow", "vendor/agent", timeout=13, max_retries=0),
            ModelConfig("qwen", "judge", timeout=14, max_retries=0),
        )

    def transport(self, messages=None, *, status=200):
        messages = iter(messages or [{"role": "assistant", "content": "OK"}])
        self.requests = []
        def send(request, **kwargs):
            self.requests.append(request)
            if status != 200:
                return httpx.Response(status, request=request, json={"error": {
                    "message": "Bad credential " + self.keys["VENDOR_AUTH"], "type": "invalid_request_error",
                }})
            return httpx.Response(200, request=request, json={
                "id": "offline-chat", "object": "chat.completion", "created": 1,
                "model": "offline-model", "choices": [{"index": 0, "message": next(messages), "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
        return patch.object(httpx.Client, "send", side_effect=send)

    def test_five_providers_defaults_and_overrides(self):
        for provider, (url, key) in PROVIDERS.items():
            with self.subTest(provider=provider):
                config = (ModelConfig(provider, "supplied-model") if provider != "custom" else
                          ModelConfig(provider, "supplied-model", "http://localhost:1234/v1", "VENDOR_AUTH"))
                self.assertEqual(config.base_url, url or "http://localhost:1234/v1")
                self.assertEqual(config.api_key_env, key or "VENDOR_AUTH")
                override = replace(config, base_url="https://regional.example/workspace/v1/", api_key_env="VENDOR_AUTH", temperature=.2, timeout=8, max_retries=0)
                self.assertEqual(override.base_url, "https://regional.example/workspace/v1")
                self.assertEqual(override.api_key_env, "VENDOR_AUTH")
                with self.transport():
                    llm = create_llm(override)
                    self.assertIsInstance(llm, ChatOpenAI)
                    self.assertEqual(llm.request_timeout, 8)
                    self.assertEqual(llm.max_retries, 0)
                    self.assertEqual(llm.invoke("hello").content, "OK")
                sent = json.loads(self.requests[0].content)
                self.assertEqual(sent["model"], "supplied-model")
                self.assertEqual(sent["temperature"], .2)
                self.assertIn("/chat/completions", str(self.requests[0].url))

    def test_invalid_configurations_and_secret_urls_rejected(self):
        invalid = [
            {"provider": "unknown"}, {"model_id": ""}, {"model_id": "x\ny"},
            {"base_url": "ftp://example.org"}, {"base_url": "https://u:secret@example.org/v1"},
            {"base_url": "https://example.org?api_key=secret"}, {"base_url": "https://example.org#secret"},
            {"base_url": "https://example.org:bad"}, {"base_url": "https://example.org:0"},
            {"api_key_env": "secret key value"}, {"timeout": 0}, {"timeout": float("inf")},
            {"temperature": True}, {"temperature": -1}, {"max_retries": -1}, {"max_retries": 1.2},
        ]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ModelConfigurationError) as raised:
                ModelConfig(**({"provider": "deepseek", "model_id": "test"} | kwargs))
            self.assertNotIn("secret", str(raised.exception))
        with self.assertRaises(ModelConfigurationError):
            ModelConfig("custom", "test")
        with self.assertRaises(ModelConfigurationError):
            ModelConfig("custom", "test", "https://example.org")

    def test_missing_credentials_and_roles_fail_before_client_creation(self):
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}):
            with patch("main.core.model_provider.create_llm") as factory:
                with self.assertRaisesRegex(MissingCredentialsError, "judge: DASHSCOPE_API_KEY"):
                    create_role_models(self.roles(), correctness=True)
                factory.assert_not_called()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            with self.assertRaisesRegex(MissingCredentialsError, "DEEPSEEK_API_KEY"):
                create_llm(ModelConfig("deepseek", "test"))
        for roles, correctness in ((ModelRoles(None, self.roles().agent), False),
                                   (replace(self.roles(), judge=None), True)):
            with self.assertRaises(ModelConfigurationError):
                create_role_models(roles, correctness=correctness)

    def test_same_provider_roles_and_runtime_never_initializes_judge(self):
        roles = ModelRoles(*(ModelConfig("deepseek", f"role-{n}") for n in range(3)))
        with patch("main.core.model_provider.create_llm", return_value=object()) as factory:
            models = create_role_models(roles)
            self.assertEqual(list(models), ["prompt", "agent"])
            self.assertEqual(factory.call_count, 2)
            factory.reset_mock()
            models = create_role_models(roles, correctness=True)
            self.assertEqual(list(models), ["prompt", "agent", "judge"])
            self.assertEqual(factory.call_count, 3)

    def test_legacy_factory_and_temperature_defaults(self):
        with self.transport():
            llm = create_deepseek_llm("legacy-model", .3)
            self.assertEqual(llm.invoke("test").content, "OK")
        self.assertEqual(llm.model_name, "legacy-model")
        self.assertEqual(llm.temperature, .3)
        self.assertEqual(llm.request_timeout, 60)
        self.assertEqual(llm.max_retries, 1)
        self.assertEqual(resolve_roles().agent.model_id, DEFAULT_MODEL)
        self.assertEqual(resolve_roles(correctness=True, prompt_temperature=.4).prompt.temperature, .4)
        with patch("main.core.model_provider.create_llm") as factory:
            create_deepseek_llm()
        self.assertEqual(factory.call_args.args[0].model_id, DEFAULT_MODEL)

    def test_sdk_parameter_adjustment_is_reported_not_silent(self):
        with patch("main.core.model_client.ServiceChatOpenAI", return_value=SimpleNamespace(temperature=1)):
            with self.assertRaisesRegex(ModelConfigurationError, "adjusted temperature"):
                create_llm(ModelConfig("deepseek", "test", temperature=0))
        with patch("main.core.model_client.ServiceChatOpenAI", side_effect=ValueError("tiny")):
            with self.assertRaises(ModelConfigurationError) as raised:
                create_llm(ModelConfig("deepseek", "test"))
        self.assertNotIn("tiny", str(raised.exception))

    def test_langgraph_agent_uses_real_chat_model_and_tool_binding(self):
        from main.compat.react_agent_new import CompatibleReactAgentNew
        messages = [
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call-local", "type": "function",
                "function": {"name": "local_echo", "arguments": '{"value":7}'},
            }]},
            {"role": "assistant", "content": "7"},
        ]
        with self.transport(messages):
            llm = create_llm(ModelConfig("deepseek", "test", max_retries=0))
            agent = CompatibleReactAgentNew(local_echo, llm)
            self.assertIsNotNone(agent.agent)
            response = agent("Echo 7")
        self.assertEqual(response.tool_args, '{"value": 7}')
        self.assertIn("7", response.tool_output)
        self.assertEqual(len(self.requests), 2)
        self.assertTrue(json.loads(self.requests[0].content)["tools"])

    def test_service_failures_are_safe_and_stop_other_roles(self):
        for status in (401, 404, 400, 500):
            with self.subTest(status=status), self.transport(status=status):
                models = create_role_models(self.roles(), correctness=True)
                with self.assertRaises(ModelCallError) as raised:
                    models["agent"].invoke("test")
                self.assertIn(f"HTTP {status}", str(raised.exception))
                self.assertNotIn("tiny", str(raised.exception))
                with self.assertRaises(ModelCallError):
                    models["prompt"].invoke("next must not execute")
                self.assertEqual(len(self.requests), 1)
                with self.assertRaises(ModelCallError):
                    check_model_calls(models)

    def test_timeout_async_and_stream_failures_are_attributed(self):
        llm = create_llm(ModelConfig("deepseek", "test", max_retries=0))
        with patch.object(ChatOpenAI, "_generate", side_effect=TimeoutError("tiny")):
            with self.assertRaises(ModelCallError) as raised:
                llm.invoke("test")
        self.assertIn("TimeoutError", str(raised.exception))
        self.assertNotIn("tiny", str(raised.exception))
        llm = create_llm(ModelConfig("deepseek", "test"))
        with patch.object(ChatOpenAI, "_agenerate", new=AsyncMock(side_effect=TimeoutError("tiny"))):
            with self.assertRaises(ModelCallError):
                asyncio.run(llm.ainvoke("test"))
        llm = create_llm(ModelConfig("deepseek", "test"))
        with patch.object(ChatOpenAI, "_stream", side_effect=TimeoutError("tiny")):
            with self.assertRaises(ModelCallError):
                list(llm.stream("test"))

    def test_explicit_runner_role_wiring_and_agent_model_summary(self):
        roles = self.roles()
        for kind in ("runtime", "correctness"):
            with self.subTest(kind=kind), offline_dependencies(kind, []) as (run, _):
                models = {name: object() for name in ("prompt", "agent", "judge")}
                agent = Mock(return_value=SimpleNamespace(agent=object()))
                tester = Mock(return_value=SimpleNamespace(test=Mock(), test_results=[], results=[]))
                tester_name = "CompatibleRuntimeErrorTester" if kind == "runtime" else "CompatibleCorrectnessTester"
                with patch("main.core.model_provider.create_llm", side_effect=lambda config: models[
                    "prompt" if config is roles.prompt else "agent" if config is roles.agent else "judge"
                ]) as factory, patch.dict(run.__globals__, {"CompatibleReactAgentNew": agent, tester_name: tester}), redirect_stdout(io.StringIO()):
                    value = run(local_echo, model_roles=roles, results_dir=self.root / kind)
                self.assertEqual(factory.call_count, 2 if kind == "runtime" else 3)
                self.assertIs(agent.call_args.args[1], models["agent"])
                self.assertEqual(value["model"], roles.agent.model_id)
                self.assertEqual(value["config"]["model_roles"], roles.summary())
                self.assertEqual(value["config"]["prompt_temperature"], .4)
                if kind == "runtime":
                    self.assertIs(tester.call_args.kwargs["llm"], models["prompt"])
                else:
                    self.assertIs(tester.call_args.kwargs["prompt_model"], models["prompt"])
                    self.assertIs(tester.call_args.kwargs["judge_model"], models["judge"])
                    self.assertIs(tester.call_args.kwargs["llm"], models["judge"])

    def test_correctness_real_pipeline_routes_requests_and_writes_reports(self):
        from main.core.correctness_runner import run_correctness
        def answer(data):
            return {"role": "assistant", "content": json.dumps(data)}
        prompt_tail = [
            answer({"template_arguments": [{"argument_values": ["7"]}]}),
            answer({"expected_answers": ["7"]}),
            answer({"expected_answer": "7"}),
        ]
        messages = [
            answer({"template_prompts": ["Echo [value]", "Echo [value]"]}),
            *prompt_tail,
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call-local", "type": "function",
                "function": {"name": "local_echo", "arguments": '{"value":7}'},
            }]},
            {"role": "assistant", "content": "7"},
            answer({"correctness_degree": 10, "reason": "Echo matched"}),
            # Existing tester generates the next prompt set before checking its iteration limit.
            *prompt_tail,
        ]
        with self.transport(messages), redirect_stdout(io.StringIO()):
            value = run_correctness(local_echo, model_roles=self.roles(),
                                    prompt_set_iters=1, results_dir=self.root)
        self.assertEqual(value["status"], "completed")
        self.assertEqual(value["summary"], {"prompt_set_count": 1, "prompt_count": 1, "tool_failure_count": 0})
        self.assertEqual([json.loads(req.content)["model"] for req in self.requests],
                         ["vendor/prompt"] * 4 + ["vendor/agent"] * 2 + ["judge"] + ["vendor/prompt"] * 3)
        for name in ("run_result.json", "correctness.json", "correctness.html"):
            self.assertTrue((self.root / name).is_file(), name)
        trace = value["results"][0]["individual_run_test_results"][0]["trace"]
        self.assertIn("local_echo", trace)
        self.assertIn('"value"', trace.replace('\\"', '"'))

    def test_swallowed_model_error_never_publishes_tool_verdicts(self):
        from main.compat.runtime_tester import RuntimeResultMixin
        with offline_dependencies("runtime", []) as (run, _), self.transport(status=401):
            models = create_role_models(self.roles())
            tester = SimpleNamespace(test_results=[])
            def test():
                # Deliberately broad expected RuntimeError would otherwise count a service failure as a trigger.
                _, case = RuntimeResultMixin().run_test(
                    lambda prompt: models["agent"].invoke(prompt), "test", RuntimeError,
                    "local_echo", {}, "offline",
                )
                self.assertTrue(case.successful_trigger)
                tester.test_results.append(case)
            tester.test = test
            report = Mock()
            with patch.dict(run.__globals__, {
                "create_role_models": Mock(return_value=models),
                "CompatibleRuntimeErrorTester": Mock(return_value=tester), "save_reports": report,
            }), redirect_stdout(io.StringIO()):
                with self.assertRaises(ModelCallError):
                    run(local_echo, model_roles=self.roles(), results_dir=self.root)
            report.assert_not_called()
            self.assertFalse((self.root / "run_result.json").exists())

    def test_configuration_and_probes_distinguish_text_from_tools(self):
        config = ModelConfig("deepseek", "test", max_retries=0)
        with patch("main.core.model_validation.create_llm") as factory:
            self.assertEqual(validate_config(config).status, "configured")
            factory.assert_not_called()
        with self.transport():
            self.assertEqual(probe_text(config).status, "passed")
        with self.transport():
            self.assertEqual(probe_tool_call(config).status, "no_tool_call")
        valid = {"role": "assistant", "content": None, "tool_calls": [{
            "id": "probe", "type": "function", "function": {
                "name": "toolfuzz_probe", "arguments": '{"value":7}',
            },
        }]}
        with self.transport([valid]):
            self.assertEqual(probe_tool_call(config).status, "passed")
        valid["tool_calls"][0]["function"]["arguments"] = "not JSON"
        with self.transport([valid]):
            self.assertEqual(probe_tool_call(config).status, "invalid_tool_call")
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            self.assertEqual(probe_text(config).status, "missing_credentials")
            self.assertEqual(probe_tool_call(config).status, "missing_credentials")
        with self.transport(status=401):
            failure = probe_text(config)
        self.assertEqual(failure.status, "request_failed")
        self.assertNotIn("tiny", failure.detail)

    def test_cli_defaults_to_offline_configuration_check(self):
        output = io.StringIO()
        with patch("main.core.model_validation.create_llm") as factory, redirect_stdout(output):
            code = validate_main(["--provider", "qwen", "--model", "user-model"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "configured")
        factory.assert_not_called()

    def test_spawn_carries_configs_without_live_clients(self):
        config = RunConfig("Runtime", "local", "legacy-ignored", 1, 0, 0, model_roles=self.roles())
        self.assertEqual(pickle.loads(pickle.dumps(config)), config)
        manager = TaskManager(config_executor, store_root=self.root)
        task = manager.start(config)
        deadline = monotonic() + 20
        while manager.snapshot().status in ("running", "cancelling") and monotonic() < deadline:
            sleep(.02)
        finished = manager.snapshot()
        self.assertEqual(finished.status, "completed", finished.error)
        self.assertEqual(finished.result["model"], config.model_roles.agent.model_id)
        stored = load_run(task.task_id, root=self.root)
        self.assertEqual(stored["config"]["model_roles"], self.roles().summary())
        self.assertEqual(stored["metadata"]["worker_exitcode"], 0)
        self.assertFalse(stored["metadata"]["worker_forced"])

    def test_spawned_missing_role_credential_saves_execution_failure(self):
        config = RunConfig("Correctness", "pubmed", "legacy", 1, 0, 0,
                           model_roles=self.roles())
        manager = TaskManager(store_root=self.root)
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}):
            task = manager.start(config)
            deadline = monotonic() + 20
            while manager.snapshot().status in ("running", "cancelling") and monotonic() < deadline:
                sleep(.02)
        finished = manager.snapshot()
        self.assertEqual(finished.status, "failed", finished.error)
        self.assertIn("judge: DASHSCOPE_API_KEY", finished.error)
        stored = load_run(task.task_id, root=self.root)
        self.assertEqual(stored["config"]["model_roles"], self.roles().summary())
        self.assertEqual(stored["model"], self.roles().agent.model_id)
        self.assertEqual(stored["results"], [])
        self.assertEqual(stored["summary"], {})
        self.assertEqual(stored["metadata"]["run_id"], task.core_run_id)
        self.assertEqual(stored["metadata"]["worker_exitcode"], 0)
        self.assertFalse(stored["metadata"]["worker_forced"])
        self.assertEqual(stored["artifacts"], {"json_path": None, "html_path": None})

    def test_task_manager_failed_and_cancelled_summaries_are_safe(self):
        roles = ModelRoles(*(ModelConfig("custom", f"role-{n}", "https://local.example/v1", "VENDOR_AUTH") for n in range(3)))
        manager = TaskManager(store_root=self.root, worker_factory=InlineWorker)
        config = RunConfig("Correctness", "local", "legacy", 1, 0, 0, model_roles=roles)
        # Use _finish directly to isolate existing lifecycle from config persistence.
        from GUI.services.task_manager import TaskSnapshot
        from main.core.run_result import start_run
        context = start_run()
        task = TaskSnapshot("safe-task", config, "running", context.started_at, context.started_perf)
        for cancelled in (False, True):
            finished = manager._finish(task, None, "opaque tiny failure", context=context, cancelled=cancelled)
            stored = load_run("safe-task", root=self.root)
            self.assertEqual(stored["status"], "cancelled" if cancelled else "failed")
            self.assertEqual(stored["model"], roles.agent.model_id)
            self.assertEqual(stored["config"]["model_roles"], roles.summary())
            self.assertEqual(stored["metadata"]["run_id"], context.run_id)
            self.assertNotIn("tiny", json.dumps(stored))
            self.assertNotIn("tiny", finished.error)

    def test_custom_and_known_keys_redacted_in_result_json_html_and_errors(self):
        roles = ModelRoles(*(ModelConfig("custom", f"role-{n}", "https://example.org/v1", "VENDOR_AUTH") for n in range(3)))
        data = {"tool": "local", "trace": "opaque tiny content " + self.keys["OPENROUTER_API_KEY"],
                "successful_trigger": False}
        with offline_dependencies("runtime", [Record(data)]) as (run, _), patch(
            "main.core.model_provider.create_llm", return_value=object()
        ), redirect_stdout(io.StringIO()):
            value = run(local_echo, model_roles=roles, results_dir=self.root)
        for path in self.root.iterdir():
            if path.suffix in (".json", ".html"):
                text = path.read_text()
                self.assertNotIn("tiny", text)
                self.assertNotIn(self.keys["OPENROUTER_API_KEY"], text)
        self.assertNotIn("tiny", json.dumps(value))
        self.assertIn("VENDOR_AUTH", json.dumps(value))

    def test_old_and_new_records_remain_readable_without_rewriting(self):
        legacy = result()
        modern = result("correctness")
        modern["config"]["model_roles"] = self.roles().summary()
        persist_run(legacy, task_id="old", root=self.root)
        persist_run(modern, task_id="new", root=self.root)
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(len(list_runs(root=self.root)), 2)
        self.assertNotIn("model_roles", load_run("old", root=self.root)["config"])
        self.assertEqual(load_run("new", root=self.root)["config"]["model_roles"], self.roles().summary())
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_task_execution_forwards_roles_to_runner_only_inside_worker(self):
        roles = self.roles()
        from types import ModuleType
        import sys
        registry = ModuleType("main.core.tool_registry")
        registry.create_tool = Mock(return_value=local_echo)
        runtime_module = ModuleType("main.core.runtime_runner")
        correctness_module = ModuleType("main.core.correctness_runner")
        runtime_module.run_runtime = Mock()
        correctness_module.run_correctness = Mock()
        for kind in ("Runtime", "Correctness"):
            with self.subTest(kind=kind), patch.dict(sys.modules, {
                registry.__name__: registry, runtime_module.__name__: runtime_module,
                correctness_module.__name__: correctness_module,
            }):
                config = RunConfig(kind, "local", "legacy", 1, 0, 0, model_roles=roles)
                execute_test("forward", config, results_dir=self.root)
                selected = runtime_module.run_runtime if kind == "Runtime" else correctness_module.run_correctness
                self.assertIs(selected.call_args.kwargs["model_roles"], roles)
                self.assertFalse(selected.call_args.kwargs["persist_result"])


if __name__ == "__main__":
    unittest.main()
