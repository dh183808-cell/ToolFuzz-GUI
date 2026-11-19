"""P2.3.1 local launcher and memory-only credential tests; no paid requests."""
from dataclasses import asdict
from functools import partial
import io
import json
import logging
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from fastapi.testclient import TestClient

from GUI.services.task_manager import RunConfig, TaskManager
from main.api.app import create_app
from main.api.schemas import TaskInput
from main.core.credentials import TemporaryCredentials, credential_scope, current_credentials, SafeOutput
from main.core.model_provider import ModelConfig, ModelRoles, create_role_models, MissingCredentialsError
from main.core import run_store

MOCK_KEYS = {'prompt': 'offline-prompt-private-value', 'agent': 'offline-agent-private-value', 'judge': 'offline-judge-private-value'}
# A dependency can create a handler before Worker stream protection begins.
EARLY_LOGGER = logging.getLogger('toolfuzz.tests.early-handler')
if not EARLY_LOGGER.handlers:
    EARLY_LOGGER.addHandler(logging.StreamHandler())
EARLY_LOGGER.propagate = False


def credential_executor(task_id, config, *, root, failure=False, block=False):
    """Spawned process reaches real model initialization, HTTP replaced in memory."""
    from GUI.tests.test_model_backend import httpx
    from GUI.tests.test_run_store import result
    from main.core.run_reports import save_reports
    from GUI.tests.test_run_store import Record
    import tempfile
    import sys

    credentials = current_credentials()
    requests = []
    def send(request, **kwargs):
        role = json.loads(request.content)['model']
        expected = MOCK_KEYS[role]
        if request.headers['authorization'] != 'Bearer ' + expected:
            raise AssertionError('Wrong role credential')
        requests.append(role)
        return httpx.Response(200, request=request, json={
            'id': 'offline-chat', 'object': 'chat.completion', 'created': 1,
            'model': role, 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'opaque ' + expected}, 'finish_reason': 'stop'}],
        })
    with patch('socket.socket.connect', side_effect=AssertionError('No network')), patch.object(httpx.Client, 'send', side_effect=send):
        models = create_role_models(config.model_roles, correctness=config.test_type == 'Correctness')
        for model in models.values():
            model.invoke('offline check')
    if requests != list(MOCK_KEYS)[:len(models)]:
        raise AssertionError('Roles did not initialize and call correctly')
    if credentials is None:
        raise AssertionError('Worker did not receive credentials')
    for value in credentials.values():
        print('credential echo:', value)
        sys.stderr.write('split echo:' + value[:7]); sys.stderr.flush(); sys.stderr.write(value[7:] + '\n')
        logging.warning('logger credential echo: %s', value)
        def thread_output():
            print('thread echo:', value)
            try:
                raise RuntimeError(value)
            except RuntimeError:
                EARLY_LOGGER.exception('early handler exception')
        thread = Thread(target=thread_output)
        thread.start(); thread.join()
    Path(root, 'entered').write_text('model clients initialized')
    while block:
        sleep(.02)
    if failure:
        raise RuntimeError('simulated failure ' + ' '.join(credentials.values()))
    value = result(config.test_type.lower())
    value['config']['model_roles'] = config.model_roles.summary()
    value['results'] = [{'tool': 'offline', 'successful_trigger': True, 'trace': ' '.join(credentials.values())}]
    staging = Path(tempfile.gettempdir())
    # Run the actual report serialization/redaction code and real template.
    save_reports([Record(value['results'][0])], staging / 'runtime')
    value['artifacts'] = {f'{kind}_path': str(staging / f'runtime.{kind}') for kind in ('json', 'html')}
    return value


class CredentialTests(unittest.TestCase):
    def roles(self):
        # Same provider and same env NAME: explicit role keys must not overwrite.
        return ModelRoles(*(ModelConfig('deepseek', role, max_retries=0) for role in MOCK_KEYS))

    def config(self):
        return RunConfig('Correctness', 'pubmed', 'ignored', 1, 0, 0, model_roles=self.roles())

    def wait(self, manager, status=None):
        deadline = monotonic() + 20
        while manager.snapshot().status in ('running', 'cancelling') and monotonic() < deadline:
            sleep(.02)
        task = manager.snapshot()
        if status:
            self.assertEqual(task.status, status, task.error)
        self.assertNotIn(task.status, ('running', 'cancelling'))
        return task

    def test_role_credentials_prefer_temporary_and_environment_fallback_remains(self):
        credentials = TemporaryCredentials({'prompt': 'p-temp', 'agent': 'a-temp'})
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'env-fallback'}), credential_scope(credentials), patch(
                'main.core.model_provider.create_llm', return_value=object()) as factory:
            create_role_models(self.roles(), correctness=True)
        self.assertEqual(factory.call_args_list[0].kwargs, {'api_key': 'p-temp'})
        self.assertEqual(factory.call_args_list[1].kwargs, {'api_key': 'a-temp'})
        self.assertEqual(factory.call_args_list[2].kwargs, {})
        self.assertNotIn('p-temp', repr(credentials))
        self.assertIsNone(current_credentials())

    def test_same_provider_reuses_key_and_missing_key_reports_role_without_secret(self):
        with credential_scope(TemporaryCredentials({'prompt': 'shared', 'agent': 'shared'})), patch(
                'main.core.model_provider.create_llm', return_value=object()) as factory:
            create_role_models(self.roles())
        self.assertEqual([call.kwargs['api_key'] for call in factory.call_args_list], ['shared', 'shared'])
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': ''}), credential_scope(TemporaryCredentials({'prompt': 'only-prompt'})):
            with self.assertRaisesRegex(MissingCredentialsError, 'agent: DEEPSEEK_API_KEY'):
                create_role_models(self.roles())

    def test_short_opaque_keys_and_split_output_are_redacted_in_scope(self):
        output = io.StringIO()
        with credential_scope(TemporaryCredentials({'prompt': 'xy!'})):
            stream = SafeOutput(output)
            stream.write('opaque x'); stream.flush(); stream.write('y! end\n')
            stream.finish()
            self.assertNotIn('xy!', json.dumps(run_store.redact_credentials({'trace': 'xy!', 'error': 'xy!'})))
        self.assertEqual(output.getvalue(), 'opaque [REDACTED] end\n')

    def test_all_providers_use_explicit_keys_without_setting_environment(self):
        from GUI.tests.test_model_backend import httpx
        original_env = dict(os.environ)
        def send(request, **kwargs):
            self.assertEqual(request.headers['authorization'], 'Bearer temporary-value')
            return httpx.Response(200, request=request, json={
                'id': 'offline', 'object': 'chat.completion', 'created': 1, 'model': 'test-model',
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}],
            })
        for provider in ('deepseek', 'openrouter', 'siliconflow', 'qwen', 'custom'):
            with self.subTest(provider=provider), credential_scope(TemporaryCredentials({'prompt': 'temporary-value', 'agent': 'temporary-value'})):
                extra = {'base_url': 'https://custom.example/v1', 'api_key_env': 'UNSET_CUSTOM_KEY'} if provider == 'custom' else {}
                model = ModelConfig(provider, 'test-model', **extra)
                with patch('socket.socket.connect', side_effect=AssertionError('No network')), patch.object(httpx.Client, 'send', side_effect=send):
                    models = create_role_models(ModelRoles(model, model))
                    self.assertEqual(models['agent'].invoke('test').content, 'ok')
        self.assertEqual(dict(os.environ), original_env)

    def test_spawn_logs_are_redacted_including_threads_and_preexisting_handlers(self):
        probe = subprocess.run([sys.executable, '-B', '-c',
                                'import unittest; unittest.main(module="GUI.tests.test_usability", '
                                'defaultTest="CredentialTests.test_spawn_success_failure_cancel_clear_owned_refs_and_never_persist_keys")'],
                               capture_output=True, text=True, timeout=40)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        output = probe.stdout + probe.stderr
        self.assertIn('[REDACTED]', output)
        self.assertIn('early handler exception', output)
        for key in MOCK_KEYS.values():
            self.assertNotIn(key, output)

    def test_spawn_success_failure_cancel_clear_owned_refs_and_never_persist_keys(self):
        original_env = dict(os.environ)
        for outcome in ('completed', 'failed', 'cancelled'):
            with self.subTest(outcome=outcome), TemporaryDirectory() as temporary:
                root = Path(temporary)
                execute = partial(credential_executor, root=temporary, failure=outcome == 'failed', block=outcome == 'cancelled')
                manager = TaskManager(execute, store_root=root / 'experiment_runs')
                supplied = TemporaryCredentials(MOCK_KEYS)
                owned = []
                from GUI.services.task_worker import TaskProcess
                def factory(*args, **kwargs):
                    owned.append(kwargs['temporary_credentials'])
                    return TaskProcess(*args, **kwargs)
                manager._worker_factory = factory
                task = manager.start(self.config(), temporary_credentials=supplied)
                supplied.clear()  # Caller no longer holds data; worker must still work.
                if outcome == 'cancelled':
                    deadline = monotonic() + 15
                    while not (root / 'entered').exists() and monotonic() < deadline:
                        sleep(.02)
                    self.assertTrue((root / 'entered').is_file(), manager.snapshot().error)
                    manager.cancel(task.task_id, expected_task_id=task.task_id)
                finished = self.wait(manager, outcome)
                self.assertTrue(owned)
                self.assertEqual(owned[0].values(), ())
                self.assertIsNone(manager._worker)
                self.assertIsNone(manager._monitor)
                stored = run_store.load_run(task.task_id, root=root / 'experiment_runs')
                self.assertTrue(stored['metadata']['worker_reaped'])
                if outcome != 'cancelled':
                    self.assertEqual(stored['metadata']['worker_exitcode'], 0)
                content = json.dumps(asdict(finished)) + json.dumps(stored)
                for file in root.rglob('*'):
                    if file.is_file():
                        content += file.read_bytes().decode('utf-8', errors='replace')
                for key in MOCK_KEYS.values():
                    self.assertNotIn(key, content)
        self.assertEqual(dict(os.environ), original_env)

    def test_api_secret_structure_separated_from_config_and_validation_does_not_echo(self):
        with TemporaryDirectory() as temporary:
            manager = TaskManager(partial(credential_executor, root=temporary), store_root=temporary)
            app = create_app(manager_factory=lambda: manager, store_root=temporary)
            payload = {'test_type': 'Correctness', 'tool_id': 'pubmed',
                       'model_roles': {role: {'provider': 'deepseek', 'model_id': role} for role in MOCK_KEYS},
                       'temporary_credentials': MOCK_KEYS}
            parsed = TaskInput.model_validate(payload)
            for key in MOCK_KEYS.values():
                self.assertNotIn(key, repr(parsed))
                self.assertNotIn(key, repr(parsed.to_core()))
            with TestClient(app) as client:
                response = client.post('/api/v1/tasks', json=payload, headers={'origin': 'http://127.0.0.1:5173'})
                self.assertEqual(response.status_code, 202, response.text)
                task = self.wait(manager, 'completed')
                responses = [response.text, client.get('/api/v1/tasks/' + task.task_id).text,
                             client.get('/api/v1/runs').text]
                detail = client.get('/api/v1/runs/' + task.core_run_id)
                responses.append(detail.text)
                for url in detail.json()['reports'].values():
                    responses.append(client.get(url).content.decode('utf-8', errors='replace'))
                invalid = client.post('/api/v1/tasks', json={**payload, 'iterations': 0})
                self.assertEqual(invalid.status_code, 422)
                responses.append(invalid.text)
                for bad_credentials in ({'prompt': {'not_a_string': MOCK_KEYS['prompt']}},
                                        {'prompt': MOCK_KEYS['prompt'] + '\n'},
                                        {'unknown': MOCK_KEYS['prompt']}):
                    invalid = client.post('/api/v1/tasks', json={**payload, 'temporary_credentials': bad_credentials})
                    self.assertEqual(invalid.status_code, 422)
                    responses.append(invalid.text)
                for key in MOCK_KEYS.values():
                    self.assertNotIn(key, ''.join(responses))

    def test_untrusted_origin_is_rejected_before_manager_start(self):
        from GUI.tests.test_api import MemoryManager
        manager = MemoryManager()
        with TemporaryDirectory() as temporary, TestClient(create_app(manager_factory=lambda: manager, store_root=temporary)) as client:
            payload = {'test_type': 'Runtime', 'tool_id': 'duckduckgo', 'temporary_credentials': {'agent': MOCK_KEYS['agent']}}
            for origin in ('https://evil.example', 'null', 'http://127.0.0.1:9999'):
                self.assertEqual(client.post('/api/v1/tasks', json=payload, headers={'origin': origin}).status_code, 403)
            self.assertEqual(manager.started, [])


class DevLauncherTests(unittest.TestCase):
    def environment(self):
        return {key: value for key, value in os.environ.items()
                if key not in ('API_PORT', 'WEB_CONCURRENCY') and not key.startswith('UVICORN_')}

    def test_occupied_ports_are_reported_and_owner_is_untouched(self):
        for port in (8000, 5173):
            with self.subTest(port=port), socket.socket() as owner:
                owner.bind(('127.0.0.1', port)); owner.listen()
                completed = subprocess.run([str(run_store.PROJECT_ROOT / 'run_dev.sh')], cwd='/tmp',
                                           env=self.environment(), capture_output=True, text=True, timeout=10)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(str(port) + ' 已被占用', completed.stderr)
                self.assertGreater(owner.fileno(), 0)
                with socket.create_connection(('127.0.0.1', port), timeout=1):
                    pass

    def test_one_command_starts_both_and_sigint_stops_only_owned_services(self):
        self.verify_start_and_stop(run_store.PROJECT_ROOT)
        with TemporaryDirectory() as elsewhere:
            # Restart immediately: closed connections must not look occupied.
            self.verify_start_and_stop(elsewhere)

    def verify_start_and_stop(self, cwd):
        with socket.socket() as unrelated, TemporaryDirectory() as temporary:
            unrelated.bind(('127.0.0.1', 0)); unrelated.listen()
            with open(Path(temporary) / 'launcher.log', 'w+') as output:
                process = subprocess.Popen([str(run_store.PROJECT_ROOT / 'run_dev.sh')], cwd=cwd,
                                           env=self.environment(), stdout=output, stderr=output, start_new_session=True)
                try:
                    deadline = monotonic() + 30
                    while True:
                        try:
                            with urlopen('http://127.0.0.1:5173/api/v1/health', timeout=.3) as response:
                                self.assertEqual(response.status, 200)
                                break
                        except OSError:
                            if process.poll() is not None or monotonic() > deadline:
                                output.seek(0); self.fail(output.read())
                            sleep(.1)
                    with urlopen('http://127.0.0.1:8000/docs', timeout=1) as response:
                        self.assertEqual(response.status, 200)
                    process.send_signal(signal.SIGINT)
                    self.assertEqual(process.wait(timeout=15), 0)
                    for port in (8000, 5173):
                        with socket.socket() as probe:
                            self.assertNotEqual(probe.connect_ex(('127.0.0.1', port)), 0)
                    with socket.create_connection(unrelated.getsockname(), timeout=1):
                        pass
                finally:
                    if process.poll() is None:
                        process.send_signal(signal.SIGTERM); process.wait(timeout=45)
