"""Offline HTTP adapters and lifespan checks. Every store is temporary."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import partial
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from threading import Barrier, Event
from time import monotonic, perf_counter, sleep
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.request import urlopen

from fastapi.testclient import TestClient

from GUI.services.task_manager import RunConfig, TaskManager, TaskSnapshot
from GUI.tests.test_run_store import process_fixture, result
from main.api.app import create_app
from main.core import run_store
from main.core.model_provider import ModelCallError, ModelRoles, PROVIDERS


class MemoryManager:
    """No executor: route tests control cancellation and completion explicitly."""
    def __init__(self):
        self.current = None
        self.started = []
        self.cancelled = []
        self.finish_cancel = False

    def snapshot(self):
        return self.current

    def start(self, config, *, expected_task_id=None):
        old = self.current.task_id if self.current else None
        if old != expected_task_id or (self.current and self.current.status in ("running", "cancelling")):
            return self.current
        self.started.append(config)
        self.current = TaskSnapshot(
            f"task-{len(self.started)}", config, "running", "2026-11-18T00:00:00+00:00",
            perf_counter(), core_run_id=f"core-{len(self.started)}",
        )
        return self.current

    def cancel(self, task_id, *, expected_task_id):
        self.cancelled.append((task_id, expected_task_id))
        if self.current and task_id == self.current.task_id and expected_task_id == task_id:
            self.current = replace(self.current, status="cancelled" if self.finish_cancel else "cancelling")
        return self.current


class ApiTests(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "experiment_runs"
        self.root.mkdir()
        self.manager = MemoryManager()
        self.factory = Mock(return_value=self.manager)
        self.app = create_app(manager_factory=self.factory, store_root=self.root, shutdown_timeout=.2)
        self.client = TestClient(self.app)
        self.client.__enter__()
        # Any running fake task becomes terminal during teardown's lifespan cancel.
        def close():
            self.manager.finish_cancel = True
            self.client.__exit__(None, None, None)
        self.addCleanup(close)

    def post(self, **kwargs):
        return self.client.post("/api/v1/tasks", json={"test_type": "Runtime", "tool_id": "duckduckgo", **kwargs})

    def saved(self, *, task_id="stored-task", run_id="core-different", kind="runtime"):
        value = result(kind)
        value["metadata"].update(task_id=task_id, run_id=run_id)
        directory = self.root / task_id
        directory.mkdir(exist_ok=True)
        stem = "runtime" if kind == "runtime" else "correctness"
        for extension in ("json", "html"):
            path = directory / f"{stem}.{extension}"
            content = '[{"trace":"offline"}]' if extension == "json" else (
                f'<html><img src="./toolfuzz.png"><a href="{stem}.json">JSON</a></html>'
            )
            path.write_text(content)
            value["artifacts"][f"{extension}_path"] = str(path)
        (directory / "toolfuzz.png").write_bytes(b"\x89PNG\r\n\x1a\noffline")
        run_store.save_run(value, task_id=task_id, root=self.root)
        return value

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["error"]["code"], code)

    def test_health_and_openapi(self):
        self.assertEqual(self.client.get("/api/v1/health").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/docs").status_code, 200)
        self.assertIn("/api/v1/tasks", self.client.get("/openapi.json").json()["paths"])
        self.assertEqual(self.manager.started, [])

    def test_swagger_uses_local_assets_with_valid_release_checksums(self):
        import hashlib
        from html.parser import HTMLParser

        class Links(HTMLParser):
            def __init__(self):
                super().__init__()
                self.urls = []
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "script" and attrs.get("src"):
                    self.urls.append(attrs["src"])
                if tag == "link" and attrs.get("href"):
                    self.urls.append(attrs["href"])

        response = self.client.get("/docs")
        self.assertEqual(response.status_code, 200)
        links = Links()
        links.feed(response.text)
        prefix = "/docs-assets/swagger-ui/"
        self.assertEqual(set(links.urls), {
            prefix + "swagger-ui-bundle.js", prefix + "swagger-ui.css", prefix + "favicon-32x32.png",
        })
        self.assertNotIn("cdn.jsdelivr.net", response.text)
        self.assertIn('"validatorUrl": null', response.text)
        manifest = self.client.get(prefix + "manifest.json").json()
        self.assertEqual(manifest["package"], "swagger-ui-dist")
        self.assertEqual(manifest["version"], "5.35.0")
        self.assertTrue(manifest["source"].startswith("https://registry.npmjs.org/swagger-ui-dist/"))
        for name, expected in manifest["files_sha256"].items():
            asset = self.client.get(prefix + name)
            self.assertEqual(asset.status_code, 200, name)
            self.assertEqual(hashlib.sha256(asset.content).hexdigest(), expected)
        script = self.client.get(prefix + "swagger-ui-bundle.js")
        self.assertIn("javascript", script.headers["content-type"])
        self.assertIn(b"SwaggerUIBundle", script.content)
        css = self.client.get(prefix + "swagger-ui.css")
        self.assertIn("text/css", css.headers["content-type"])
        self.assertIn(b".swagger-ui", css.content)
        self.assertIn("Apache License", self.client.get(prefix + "LICENSE").text)
        self.assertIn("SmartBear", self.client.get(prefix + "NOTICE").text)
        self.assertEqual(self.client.get("/docs/oauth2-redirect").status_code, 200)
        schema = self.client.get("/openapi.json")
        self.assertEqual(schema.status_code, 200)
        self.assertIn("/api/v1/tasks", schema.json()["paths"])
        self.assertNotIn("/docs", schema.json()["paths"])
        self.assertEqual(self.manager.started, [])
        self.assertEqual(self.client.get(prefix + "..%2F..%2Fapp.py").status_code, 404)

    def test_swagger_local_urls_respect_proxy_root_path(self):
        app = create_app(manager_factory=MemoryManager, store_root=self.root)
        with TestClient(app, root_path="/toolfuzz") as client:
            response = client.get("/docs")
        self.assertEqual(response.status_code, 200)
        self.assertIn('/toolfuzz/docs-assets/swagger-ui/swagger-ui-bundle.js', response.text)
        self.assertIn('/toolfuzz/openapi.json', response.text)
        self.assertIn('/toolfuzz/docs/oauth2-redirect', response.text)

    def test_providers_reuse_registry_without_returning_credentials(self):
        keys = {key: "offline-private" for _, key in PROVIDERS.values() if key}
        with patch.dict(os.environ, keys):
            response = self.client.get("/api/v1/providers")
        self.assertEqual(response.status_code, 200)
        items = {item["id"]: item for item in response.json()["items"]}
        self.assertEqual(set(items), set(PROVIDERS))
        for provider, (url, key) in PROVIDERS.items():
            self.assertEqual(items[provider]["default_base_url"], url)
            self.assertEqual(items[provider]["credentials_configured"], bool(key))
        self.assertTrue(items["custom"]["requires_base_url"])
        self.assertNotIn("offline-private", response.text)

    def test_tools_use_existing_metadata_without_constructing_tools(self):
        with patch("main.core.tool_registry.create_tool", side_effect=AssertionError("Do not instantiate")):
            response = self.client.get("/api/v1/tools")
        self.assertEqual(response.json()["items"], [
            {"id": "duckduckgo", "name": "DuckDuckGo Search"}, {"id": "pubmed", "name": "PubMed"},
        ])

    def test_runtime_legacy_request_builds_original_run_config(self):
        response = self.post(model_name="deepseek-flash", iterations=2, agent_temperature=.2)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["task_id"], "task-1")
        config = self.manager.started[0]
        self.assertIsInstance(config, RunConfig)
        self.assertEqual(config.iterations, 2)
        self.assertEqual(config.prompt_temperature, 0)
        self.assertEqual(config.agent_temperature, .2)
        self.assertIsNone(config.model_roles)

    def test_correctness_legacy_default_temperature(self):
        response = self.post(test_type="Correctness", tool_id="pubmed")
        self.assertEqual(response.status_code, 202)
        config = self.manager.started[0]
        self.assertEqual((config.prompt_temperature, config.agent_temperature, config.judge_temperature), (.4, 0, 0))

    def roles_payload(self):
        return {"prompt": {"provider": "deepseek", "model_id": "deepseek-flash"},
                "agent": {"provider": "openrouter", "model_id": "mistralai/mistral-nemo", "timeout": 15},
                "judge": {"provider": "qwen", "model_id": "qwen-plus"}}

    def test_explicit_three_roles_and_custom_endpoint(self):
        roles = self.roles_payload()
        roles["judge"] = {"provider": "custom", "model_id": "judge", "base_url": "https://example.org/v1",
                          "api_key_env": "CUSTOM_AUTH", "temperature": .1, "timeout": 12, "max_retries": 0}
        response = self.post(test_type="Correctness", model_roles=roles)
        self.assertEqual(response.status_code, 202, response.text)
        config = self.manager.started[0]
        self.assertIsInstance(config.model_roles, ModelRoles)
        self.assertEqual(config.model_roles.agent.provider, "openrouter")
        self.assertEqual(config.model_roles.judge.api_key_env, "CUSTOM_AUTH")
        self.assertEqual(config.model_name, "mistralai/mistral-nemo")
        self.assertEqual(config.judge_temperature, .1)

    def test_runtime_explicit_roles_needs_no_judge(self):
        roles = self.roles_payload()
        del roles["judge"]
        self.assertEqual(self.post(model_roles=roles).status_code, 202)
        self.assertIsNone(self.manager.started[0].model_roles.judge)

    def test_invalid_parameters_rejected_without_echoing_inputs(self):
        requests = [
            {}, {"test_type": "Other", "tool_id": "pubmed"},
            {"test_type": "Runtime", "tool_id": "unknown"},
            {"test_type": "Runtime", "tool_id": "pubmed", "iterations": 0},
            {"test_type": "Runtime", "tool_id": "pubmed", "iterations": True},
            {"test_type": "Runtime", "tool_id": "pubmed", "iterations": "1"},
            {"test_type": "Runtime", "tool_id": "pubmed", "api_key": "private-input"},
            {"test_type": "Runtime", "tool_id": "pubmed", "fuzzer_iters": 1},
            {"test_type": "Runtime", "tool_id": "pubmed", "model_name": ""},
            {"test_type": "Runtime", "tool_id": "pubmed", "judge_temperature": .2},
            {"test_type": "Runtime", "tool_id": "pubmed", "agent_temperature": 3},
            {"test_type": "Runtime", "tool_id": "pubmed", "model_roles": self.roles_payload()},
            {"test_type": "Correctness", "tool_id": "pubmed", "model_roles": {"prompt": self.roles_payload()["prompt"]}},
        ]
        for payload in requests:
            with self.subTest(payload=payload):
                response = self.client.post("/api/v1/tasks", json=payload)
                self.assert_error(response, 422, "validation_error")
                self.assertNotIn("private-input", response.text)
        self.assertEqual(self.manager.started, [])

    def test_bad_model_parameters_and_legacy_role_mix_are_422(self):
        for field, val in (("provider", "unknown"), ("base_url", "https://user:private-input@example.org"),
                           ("base_url", "https://example.org?api_key=private-input"), ("api_key", "private-input"),
                           ("timeout", -1), ("max_retries", True), ("model_id", "")):
            roles = self.roles_payload()
            roles["prompt"][field] = val
            response = self.post(test_type="Correctness", model_roles=roles)
            self.assert_error(response, 422, "validation_error")
            self.assertNotIn("private-input", response.text)
        self.assert_error(self.post(test_type="Correctness", model_roles=self.roles_payload(), model_name="ignored"),
                          422, "validation_error")

    def test_conflict_while_running_or_cancelling(self):
        self.post()
        self.assert_error(self.post(), 409, "task_conflict")
        self.manager.current = replace(self.manager.current, status="cancelling")
        self.assert_error(self.post(), 409, "task_conflict")
        self.assertEqual(len(self.manager.started), 1)

    def test_simultaneous_creates_only_start_one_task(self):
        barrier = Barrier(2)
        def send():
            barrier.wait(2)
            return self.post().status_code
        with ThreadPoolExecutor(2) as pool:
            codes = list(pool.map(lambda _: send(), range(2)))
        self.assertEqual(sorted(codes), [202, 409])
        self.assertEqual(len(self.manager.started), 1)

    def test_start_detects_task_replacement_race(self):
        with patch.object(self.manager, "start", return_value=None):
            self.assert_error(self.post(), 409, "task_conflict")

    def test_task_query_distinguishes_core_identity_and_no_fake_progress(self):
        self.post()
        response = self.client.get("/api/v1/tasks/task-1")
        value = response.json()
        self.assertEqual((value["task_id"], value["run_id"]), ("task-1", "core-1"))
        self.assertIsNone(value["progress"])
        self.assertEqual(value["status"], "running")
        self.assertEqual(value["persistence"]["status"], "pending")

    def test_cancel_is_idempotent_and_only_backend_confirms_cancelled(self):
        self.post()
        response = self.client.post("/api/v1/tasks/task-1/cancel")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "cancelling")
        self.assertEqual(self.client.post("/api/v1/tasks/task-1/cancel").json()["status"], "cancelling")
        self.assertEqual(self.manager.cancelled, [("task-1", "task-1")] * 2)
        self.manager.current = replace(self.manager.current, status="cancelled")
        self.assertEqual(self.client.get("/api/v1/tasks/task-1").json()["status"], "cancelled")
        self.assert_error(self.client.post("/api/v1/tasks/task-1/cancel"), 409, "task_not_cancellable")
        self.assertEqual(self.post().status_code, 202)

    def test_cancel_responds_cancelled_only_if_manager_already_reaped(self):
        self.post()
        self.manager.finish_cancel = True
        self.assertEqual(self.client.post("/api/v1/tasks/task-1/cancel").json()["status"], "cancelled")

    def test_completion_and_replacement_race_cannot_claim_cancellation(self):
        self.post()
        def completed(*args, **kwargs):
            self.manager.current = replace(self.manager.current, status="completed")
            return self.manager.current
        with patch.object(self.manager, "cancel", side_effect=completed):
            self.assert_error(self.client.post("/api/v1/tasks/task-1/cancel"), 409, "task_not_cancellable")
        self.assertEqual(self.manager.current.status, "completed")
        self.manager.current = replace(self.manager.current, status="running", task_id="new-task")
        self.assert_error(self.client.post("/api/v1/tasks/task-1/cancel"), 404, "task_not_found")
        self.assertEqual(self.manager.current.task_id, "new-task")

    def test_missing_tasks_and_terminal_history_cannot_cancel(self):
        self.assert_error(self.client.get("/api/v1/tasks/missing"), 404, "task_not_found")
        self.assert_error(self.client.post("/api/v1/tasks/missing/cancel"), 404, "task_not_found")
        self.saved()
        self.assert_error(self.client.post("/api/v1/tasks/stored-task/cancel"), 409, "task_not_cancellable")

    def test_history_uses_runstore_pagination_filters_and_recovers_after_restart(self):
        first = self.saved()
        first["metadata"]["started_at"] = "2026-11-18T00:00:00Z"
        run_store.save_run(first, root=self.root)
        second = self.saved(task_id="second", run_id="core-second", kind="correctness")
        second["metadata"]["started_at"] = "2026-11-19T00:00:00Z"
        run_store.save_run(second, root=self.root)
        with patch("main.api.routes.run_store.list_runs", wraps=run_store.list_runs) as listing:
            response = self.client.get("/api/v1/runs?limit=1&offset=0")
            listing.assert_called_once_with(root=self.root)
        self.assertEqual(response.json()["total"], 2)
        self.assertEqual(response.json()["items"][0]["run_id"], "core-second")
        self.assertEqual(len(self.client.get("/api/v1/runs?test_type=runtime").json()["items"]), 1)
        fresh = create_app(manager_factory=MemoryManager, store_root=self.root)
        with TestClient(fresh) as client:
            self.assertEqual(client.get("/api/v1/tasks/stored-task").json()["run_id"], "core-different")
            self.assertEqual(client.get("/api/v1/runs/core-different").status_code, 200)
        self.assertEqual(self.manager.started, [])

    def test_history_detail_maps_run_id_to_task_id_and_hides_server_paths(self):
        value = self.saved()
        with patch("main.api.routes.run_store.load_run", wraps=run_store.load_run) as loading:
            response = self.client.get("/api/v1/runs/core-different")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(call.args == ("stored-task",) for call in loading.call_args_list))
        record = response.json()
        self.assertEqual(record["metadata"]["task_id"], "stored-task")
        self.assertEqual(record["metadata"]["run_id"], "core-different")
        self.assertEqual(record["results"], value["results"])
        self.assertEqual(record["artifacts"]["json_path"], "/api/v1/runs/core-different/reports/runtime.json")
        self.assertNotIn(str(self.root), response.text)
        self.assert_error(self.client.get("/api/v1/runs/stored-task"), 404, "run_not_found")

    def test_json_html_and_relative_png_are_accessible(self):
        self.saved()
        prefix = "/api/v1/runs/core-different/reports/"
        self.assertEqual(self.client.get(prefix + "runtime.json").json(), [{"trace": "offline"}])
        html = self.client.get(prefix + "runtime.html")
        self.assertIn('src="./toolfuzz.png"', html.text)
        self.assertIn('href="runtime.json"', html.text)
        self.assertIn("sandbox", html.headers["content-security-policy"])
        image = self.client.get(prefix + "toolfuzz.png")
        self.assertEqual(image.headers["content-type"], "image/png")
        self.assertTrue(image.content.startswith(b"\x89PNG"))

    def test_report_paths_reject_escape_cross_task_symlink_and_unlisted_files(self):
        record = self.saved()
        other = self.saved(task_id="other", run_id="other-core")
        outside = Path(self.root.parent, "outside.json")
        outside.write_text('{"private":"outside"}')
        directory = self.root / "stored-task"
        (directory / "unlisted.json").write_text("[]")
        prefix = "/api/v1/runs/core-different/reports/"
        for name in ("unlisted.json", "run_result.json", "response.json", "..%2F..%2Foutside.json", "%5Coutside.json"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(prefix + name).status_code, 404)
        for unsafe in (str(outside), other["artifacts"]["json_path"]):
            record["artifacts"]["json_path"] = unsafe
            run_store.save_run(record, root=self.root)
            self.assertEqual(self.client.get(prefix + Path(unsafe).name).status_code, 404)
        linked = directory / "linked.json"
        linked.symlink_to(outside)
        record["artifacts"]["json_path"] = str(linked)
        run_store.save_run(record, root=self.root)
        self.assertEqual(self.client.get(prefix + "linked.json").status_code, 404)

    def test_report_relative_paths_and_missing_files(self):
        record = self.saved()
        for raw in ("stored-task/runtime.json", "experiment_runs/stored-task/runtime.json", "runtime.json"):
            record["artifacts"]["json_path"] = raw
            run_store.save_run(record, root=self.root)
            self.assertEqual(self.client.get("/api/v1/runs/core-different/reports/runtime.json").status_code, 200)
        (self.root / "stored-task/runtime.json").unlink()
        self.assert_error(self.client.get("/api/v1/runs/core-different/reports/runtime.json"), 404, "report_not_found")
        self.assertEqual(self.client.get("/api/v1/runs/core-different").status_code, 200)

    def test_corrupt_deleted_and_external_record_symlinks_are_unavailable(self):
        self.saved()
        bad = self.root / "broken"
        bad.mkdir()
        (bad / "run_result.json").write_text("{")
        linked = self.root / "linked"
        linked.mkdir()
        outside = self.root.parent / "external.json"
        outside.write_text(json.dumps(result()))
        (linked / "run_result.json").symlink_to(outside)
        self.assertIsNone(run_store.load_run("linked", root=self.root))
        self.assertEqual(self.client.get("/api/v1/runs").json()["total"], 1)
        (self.root / "stored-task/run_result.json").unlink()
        self.assertEqual(self.client.get("/api/v1/runs/core-different").status_code, 404)

    def test_empty_missing_store_and_old_minimal_record(self):
        self.root.rmdir()
        self.assertEqual(self.client.get("/api/v1/runs").json()["total"], 0)
        run_store.save_run({"metadata": {"run_id": "legacy"}}, root=self.root)
        self.assertEqual(self.client.get("/api/v1/runs/legacy").status_code, 200)
        self.assert_error(self.client.get("/api/v1/runs?limit=101"), 422, "validation_error")
        self.assert_error(self.client.get("/api/v1/runs?offset=-1"), 422, "validation_error")

    def test_duplicate_run_ids_return_conflict(self):
        self.saved(task_id="one")
        self.saved(task_id="two")
        self.assert_error(self.client.get("/api/v1/runs/core-different"), 409, "ambiguous_run_id")

    def test_history_and_report_operations_do_not_modify_records_or_start_tasks(self):
        self.saved()
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.client.get("/api/v1/runs")
        self.client.get("/api/v1/runs/core-different")
        self.client.get("/api/v1/runs/core-different/reports/runtime.html")
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertEqual(self.manager.started, [])

    def test_secret_values_headers_and_local_paths_never_appear_in_responses(self):
        record = self.saved()
        record["config"]["model_roles"] = {"agent": {"key_env_name": "VENDOR_AUTH"}}
        run_store.save_run(record, root=self.root)
        # Simulate an old/manual record or report containing unsafe diagnostics.
        path = self.root / "stored-task/run_result.json"
        data = json.loads(path.read_text())
        data.update(error="opaque shortkey /home/user/private.log", authorization="Bearer private-value")
        path.write_text(json.dumps(data))
        report = self.root / "stored-task/runtime.json"
        report.write_text('[{"error":"opaque shortkey /tmp/private.log", "api_key":"private-value"}]')
        with patch.dict(os.environ, {"VENDOR_AUTH": "shortkey"}):
            for url in ("/api/v1/tasks/stored-task", "/api/v1/runs/core-different", "/api/v1/runs/core-different/reports/runtime.json"):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, response.text)
                for secret in ("shortkey", "private-value", "/home/user", "/tmp/private", str(self.root)):
                    self.assertNotIn(secret, response.text)

    def test_internal_and_model_errors_have_safe_stable_response(self):
        for error in (ModelCallError("Authorization Bearer private-key /tmp/secret"), OSError("/home/user/private-key")):
            with patch.object(self.manager, "snapshot", side_effect=error):
                response = self.client.get("/api/v1/tasks/x")
            self.assert_error(response, 500, "internal_error")
            self.assertNotIn("private-key", response.text)
        self.assert_error(self.client.get("/api/v1/unknown"), 404, "http_error")
        self.assert_error(self.client.post("/api/v1/tasks", content="{"), 422, "validation_error")

    def test_cors_explicit_local_origins_and_no_wildcard(self):
        for origin in ("http://localhost:5173", "http://127.0.0.1:5173"):
            response = self.client.options("/api/v1/tasks", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
            self.assertEqual(response.headers["access-control-allow-origin"], origin)
        blocked = self.client.options("/api/v1/tasks", headers={"Origin": "https://outside.example", "Access-Control-Request-Method": "POST"})
        self.assertEqual(blocked.status_code, 400)
        self.assertNotIn("access-control-allow-origin", blocked.headers)
        with patch.dict(os.environ, {"TOOLFUZZ_CORS_ORIGINS": "*"}):
            with self.assertRaises(ValueError):
                create_app()
        with patch.dict(os.environ, {"TOOLFUZZ_CORS_ORIGINS": "https://dev.example"}):
            app = create_app(manager_factory=MemoryManager, store_root=self.root)
            with TestClient(app) as client:
                response = client.get("/api/v1/health", headers={"Origin": "https://dev.example"})
                self.assertEqual(response.headers["access-control-allow-origin"], "https://dev.example")

    def test_lifespan_owns_one_shared_manager_and_cancels_on_shutdown(self):
        self.factory.assert_called_once()
        for _ in range(3):
            self.client.get("/api/v1/health")
        self.assertEqual(self.factory.call_count, 1)
        self.assertIs(self.app.state.task_manager, self.manager)
        local = MemoryManager()
        local.finish_cancel = True
        factory = Mock(return_value=local)
        app = create_app(manager_factory=factory, store_root=self.root)
        self.assertFalse(hasattr(app.state, "task_manager"))
        with TestClient(app) as client:
            self.assertEqual(client.post("/api/v1/tasks", json={"test_type": "Runtime", "tool_id": "pubmed"}).status_code, 202)
        self.assertEqual(local.current.status, "cancelled")
        self.assertFalse(app.state.accepting_tasks)
        self.assertEqual(local.cancelled, [("task-1", "task-1")])

    def test_shutdown_unconfirmed_cleanup_reports_failure_keeps_ownership(self):
        local = MemoryManager()
        app = create_app(manager_factory=lambda: local, store_root=self.root, shutdown_timeout=.05)
        with self.assertRaisesRegex(RuntimeError, "cleanup was not confirmed"):
            with TestClient(app) as client:
                client.post("/api/v1/tasks", json={"test_type": "Runtime", "tool_id": "pubmed"})
        self.assertEqual(local.current.status, "cancelling")
        self.assertIs(app.state.task_manager, local)
        self.assertFalse(app.state.accepting_tasks)

    def test_real_worker_lifespan_stops_blocked_process_before_shutdown_returns(self):
        manager = TaskManager(partial(process_fixture, root=str(self.root), socket_block=True), store_root=self.root)
        app = create_app(manager_factory=lambda: manager, store_root=self.root)
        with TestClient(app) as client:
            response = client.post("/api/v1/tasks", json={"test_type": "Runtime", "tool_id": "pubmed"})
            self.assertEqual(response.status_code, 202)
            deadline = monotonic() + 15
            while not (self.root / "entered").exists() and monotonic() < deadline:
                sleep(.02)
            self.assertTrue((self.root / "entered").exists())
            worker = manager._worker
            cancelling = client.post(f"/api/v1/tasks/{response.json()['task_id']}/cancel")
            self.assertIn(cancelling.json()["status"], ("cancelling", "cancelled"))
        self.assertEqual(manager.snapshot().status, "cancelled")
        self.assertTrue(worker.stopped)
        self.assertFalse((self.root / "unexpected-next-call").exists())

    def test_app_import_does_not_construct_manager_or_models(self):
        script = """
from unittest.mock import patch
from GUI.services import task_manager
with patch.object(task_manager, 'TaskManager', side_effect=AssertionError('import created manager')):
    with patch('main.core.model_provider.create_llm', side_effect=AssertionError('import created model')):
        import main.api.app
print('import safe')
"""
        completed = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True, text=True,
                                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("import safe", completed.stdout)

    def test_multiworker_environment_is_rejected_at_startup(self):
        with patch.dict(os.environ, {"WEB_CONCURRENCY": "4"}):
            with self.assertRaisesRegex(RuntimeError, "single Uvicorn worker"):
                with TestClient(create_app(manager_factory=MemoryManager, store_root=self.root)):
                    pass

    def test_moved_legacy_and_relative_records_download_through_real_routes(self):
        from GUI.services.result_view import read_report
        for kind in ("runtime", "correctness"):
            original = self.saved(task_id=kind, run_id=f"core-{kind}", kind=kind)
            # Keep Runtime in old absolute form, Correctness in new relative form.
            if kind == "runtime":
                (self.root / kind / "run_result.json").write_text(json.dumps(original))
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        moved = self.root.parent / "new_workspace" / "experiment_runs"
        moved.parent.mkdir()
        self.root.rename(moved)
        with TestClient(create_app(manager_factory=MemoryManager, store_root=moved)) as client:
            self.assertEqual(client.get('/api/v1/runs').json()['total'], 2)
            for kind in ("runtime", "correctness"):
                record = run_store.load_run(kind, root=moved)
                response = client.get(f'/api/v1/runs/core-{kind}')
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(str(self.root.parent), response.text)
                value = response.json()
                self.assertEqual(value['metadata']['task_id'], kind)
                for extension in ('json', 'html'):
                    download = client.get(value['artifacts'][f'{extension}_path'])
                    self.assertEqual(download.status_code, 200)
                    self.assertIsNone(read_report(record['artifacts'], extension, allowed_root=moved, task_id=kind)[2])
                html = client.get(value['artifacts']['html_path']).text
                self.assertIn(f'href="{kind}.json"', html)
                self.assertIn('src="./toolfuzz.png"', html)
                self.assertEqual(client.get(value['reports']['toolfuzz.png']).status_code, 200)
            self.assertEqual(client.get('/api/v1/runs/core-runtime/reports/run_result.json').status_code, 404)
        self.assertEqual({str(p.relative_to(moved)): p.read_bytes() for p in moved.rglob('*') if p.is_file()}, before)


class ApiLauncherTests(TestCase):
    """Start only local read-only services; never create model tasks."""

    def environment(self):
        return {key: value for key, value in os.environ.items()
                if key not in ('PYTHONPATH', 'PYTHONHOME', 'WEB_CONCURRENCY', 'API_PORT')
                and not key.startswith('UVICORN_')}

    def test_entrypoint_from_root_and_unrelated_cwd_without_manual_pythonpath(self):
        root = run_store.PROJECT_ROOT
        with tempfile.TemporaryDirectory() as unrelated:
            for cwd in (root, Path(unrelated)):
                with self.subTest(cwd=str(cwd)), socket.socket() as listener:
                    listener.bind(('127.0.0.1', 0))
                    port = listener.getsockname()[1]
                    listener.close()
                    env = self.environment()
                    env['API_PORT'] = str(port)
                    # A conflicting inherited module path must not influence startup.
                    if cwd == root:
                        env['PYTHONPATH'] = '/nonexistent/inherited/path'
                    with tempfile.TemporaryFile(mode='w+') as log:
                        process = subprocess.Popen([str(root / 'run_api.sh')], cwd=cwd, env=env,
                                                   stdout=log, stderr=log, start_new_session=True)
                        try:
                            deadline = monotonic() + 15
                            while True:
                                try:
                                    with urlopen(f'http://127.0.0.1:{port}/api/v1/health', timeout=.5) as response:
                                        self.assertEqual(response.status, 200)
                                        self.assertEqual(json.load(response), {'status': 'ok'})
                                    break
                                except OSError:
                                    if process.poll() is not None or monotonic() >= deadline:
                                        log.seek(0)
                                        self.fail('Launcher did not become healthy: ' + log.read())
                                    sleep(.05)
                            for path in ('/docs', '/openapi.json', '/docs-assets/swagger-ui/swagger-ui-bundle.js',
                                         '/docs-assets/swagger-ui/swagger-ui.css'):
                                with urlopen(f'http://127.0.0.1:{port}{path}', timeout=3) as response:
                                    self.assertEqual(response.status, 200)
                                    if path == '/docs':
                                        self.assertIn('/docs-assets/swagger-ui/swagger-ui-bundle.js', response.read().decode())
                            if Path('/proc').is_dir():
                                args = Path(f'/proc/{process.pid}/cmdline').read_bytes().split(b'\x00')
                                self.assertEqual(args[0].decode(), str(root / 'ToolFuzz/.venv/bin/python'))
                                self.assertIn(b'--workers', args)
                                self.assertNotIn(b'--reload', args)
                        finally:
                            if process.poll() is None:
                                process.send_signal(signal.SIGINT)
                            try:
                                process.wait(timeout=8)
                            except subprocess.TimeoutExpired:
                                os.killpg(process.pid, signal.SIGKILL)
                                process.wait(timeout=5)
                        log.seek(0)
                        output = log.read()
                        self.assertEqual(process.returncode, 0, output)
                        self.assertIn('FastAPI 0.115.14, Starlette 0.46.2; single worker.', output)
                        self.assertEqual(output.count('Started server process'), 1)

    def test_launcher_rejects_conflicting_environment_and_invalid_port(self):
        for settings in ({'WEB_CONCURRENCY': '2'}, {'WEB_CONCURRENCY': ''}, {'UVICORN_WORKERS': '2'}, {'UVICORN_RELOAD': 'true'},
                         {'UVICORN_FD': '3'}, {'API_PORT': '0'}, {'API_PORT': 'invalid'}):
            with self.subTest(settings=settings):
                completed = subprocess.run([str(run_store.PROJECT_ROOT / 'run_api.sh')],
                                           env={**self.environment(), **settings}, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn('ToolFuzz API:', completed.stderr)
                self.assertNotIn('Started server process', completed.stderr)

    def test_launcher_reports_missing_dependencies_without_installing(self):
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            shutil.copy2(run_store.PROJECT_ROOT / 'run_api.sh', root / 'run_api.sh')
            (root / 'ToolFuzz/.venv/bin').mkdir(parents=True)
            (root / 'ToolFuzz/.venv/bin/python').symlink_to(sys.executable)
            completed = subprocess.run([str(root / 'run_api.sh')], env=self.environment(),
                                       capture_output=True, text=True, timeout=5)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn('API dependencies are missing', completed.stderr)
            self.assertFalse((root / '.venv').exists())
