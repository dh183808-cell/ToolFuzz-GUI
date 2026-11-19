import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createApi, ApiError, errorMessage } from '@/api/client'
import { buildInput, clearKeys, defaultForm } from '@/composables/config'
import { createTaskController } from '@/composables/task'
import { metrics, trace } from '@/composables/display'
import App from '@/App.vue'
import WorkspaceView from '@/views/WorkspaceView.vue'
import HistoryView from '@/views/HistoryView.vue'
import RunView from '@/views/RunView.vue'
import ResultPanel from '@/components/ResultPanel.vue'
import ModelEditor from '@/components/ModelEditor.vue'
import type { ModelConfig, Provider, RunResult } from '@/types/api'

const providers: Provider[] = [
  { id: 'deepseek', name: 'DeepSeek', default_base_url: 'https://api.deepseek.com', requires_base_url: false, credentials_configured: true },
  { id: 'custom', name: 'Custom', default_base_url: null, requires_base_url: true, credentials_configured: false },
]
const tools = [{ id: 'duckduckgo', name: 'DuckDuckGo Search' }, { id: 'pubmed', name: 'PubMed' }]
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const record: RunResult = { status: 'completed', test_type: 'runtime', tool: 'local_echo', model: 'old-deepseek', metadata: { task_id: 'gui-id', run_id: 'core-id' }, summary: { test_count: 1, successful_triggers: 1 }, results: [{ prompt: '<script>unsafe</script>', successful_trigger: true, trace: JSON.stringify([{ role: 'tool', content: 'tool output' }, { role: 'assistant', content: 'agent answer' }]) }], artifacts: { json_path: '/api/v1/runs/core-id/reports/runtime.json', html_path: '/api/v1/runs/core-id/reports/runtime.html' } }
const form = () => {
  const value = { ...defaultForm(), tool_id: 'duckduckgo' }
  for (const role of ['shared', 'prompt', 'agent', 'judge'] as const) value[role].credential_source = 'environment'
  return value
}
const wrappers: { unmount: () => void }[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); sessionStorage.clear() })

describe('API client', () => {
  it('loads providers, tools and health from the official API paths', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ items: providers })).mockResolvedValueOnce(response({ items: tools })).mockResolvedValueOnce(response({ status: 'ok' }))
    const client = createApi('', fetcher)
    expect((await client.providers()).items).toEqual(providers)
    expect((await client.tools()).items).toEqual(tools)
    expect(await client.health()).toEqual({ status: 'ok' })
    expect(fetcher.mock.calls.map(call => call[0])).toEqual(['/api/v1/providers', '/api/v1/tools', '/api/v1/health'])
  })
  it.each([422, 404, 409, 500])('handles HTTP %s without displaying response secrets', async status => {
    const client = createApi('', vi.fn().mockResolvedValue(response({ error: { code: 'http_error', message: 'sk-private-secret' } }, status)))
    let caught: unknown
    try { await client.health() } catch (err) { caught = err }
    expect(caught).toBeInstanceOf(ApiError)
    expect((caught as ApiError).status).toBe(status)
    expect(errorMessage(caught)).not.toContain('sk-private')
  })
  it('handles network failure and invalid JSON responses', async () => {
    await expect(createApi('', vi.fn().mockRejectedValue(new Error('private diagnostic'))).health()).rejects.toMatchObject({ code: 'network' })
    await expect(createApi('', vi.fn().mockResolvedValue(new Response('<html>bad</html>'))).health()).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('times out a blocked request and cleans up abort listeners', async () => {
    vi.useFakeTimers()
    const client = createApi('', vi.fn((_url, init) => new Promise<Response>((_resolve, reject) => init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))))), 20)
    const check = expect(client.health()).rejects.toMatchObject({ code: 'timeout' })
    await vi.advanceTimersByTimeAsync(21); await check
    expect(vi.getTimerCount()).toBe(0)
  })
  it('supports explicit request cancellation without calling task cancel', async () => {
    const fetcher = vi.fn((_url, init) => new Promise<Response>((_resolve, reject) => init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))))
    const controller = new AbortController(), client = createApi('', fetcher)
    const check = expect(client.task('gui', controller.signal)).rejects.toMatchObject({ code: 'aborted' })
    controller.abort(); await check; expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('only permits backend report URLs, with encoded safe identifiers', () => {
    const client = createApi('http://127.0.0.1:8000')
    expect(client.reportUrl(record.artifacts?.html_path)).toBe('http://127.0.0.1:8000/api/v1/runs/core-id/reports/runtime.html')
    for (const raw of ['https://evil.test/file', 'javascript:alert(1)', '/etc/private', '/api/v1/runs/x/reports/%2e%2e', '/api/v1/runs/x/reports/a%2fb', '//evil.test']) expect(client.reportUrl(raw)).toBeNull()
    expect(() => createApi('https://user:private@example.com')).toThrow()
  })
})

describe('configuration', () => {
  it('defaults to direct credentials and requires a key before submission', () => {
    const value = { ...defaultForm(), tool_id: 'duckduckgo' }
    expect(value.shared.credential_source).toBe('direct')
    expect(() => buildInput(value, providers, tools)).toThrow(/API Key/)
  })
  it.each(['deepseek', 'openrouter', 'siliconflow', 'qwen', 'custom'])('reuses one temporary key for %s roles even when server credentials are unset', id => {
    const value = { ...defaultForm(), tool_id: 'duckduckgo', test_type: 'Correctness' as const }
    const provider = { id, name: id, default_base_url: null, requires_base_url: id === 'custom', credentials_configured: false }
    Object.assign(value.shared, { provider: id, temporary_key: 'offline-shared-value', base_url: id === 'custom' ? 'https://custom.example/v1' : '' })
    const payload = buildInput(value, [provider], tools)
    expect(payload.temporary_credentials).toEqual({ prompt: 'offline-shared-value', agent: 'offline-shared-value', judge: 'offline-shared-value' })
    expect(JSON.stringify(payload.model_roles)).not.toContain('offline-shared-value')
    if (id === 'custom') expect(payload.model_roles.agent.api_key_env).toBe('TOOLFUZZ_TEMPORARY_API_KEY')
  })
  it('keeps advanced credentials independent and supports mixed environment mode', () => {
    const value = { ...defaultForm(), tool_id: 'pubmed', mode: 'advanced' as const, test_type: 'Correctness' as const }
    for (const role of ['prompt', 'agent', 'judge'] as const) value[role].temporary_key = 'offline-' + role
    expect(buildInput(value, providers, tools).temporary_credentials).toEqual({ prompt: 'offline-prompt', agent: 'offline-agent', judge: 'offline-judge' })
    value.judge.credential_source = 'environment'
    expect(buildInput(value, providers, tools).temporary_credentials).not.toHaveProperty('judge')
    clearKeys(value)
    expect([value.prompt, value.agent, value.judge, value.shared].every(model => !model.temporary_key)).toBe(true)
  })
  it('omits temporary credentials entirely in server environment mode', () => {
    expect(buildInput(form(), providers, tools)).not.toHaveProperty('temporary_credentials')
  })
  it('creates Runtime simple-mode roles without legacy conflicting fields', () => {
    const value = buildInput(form(), providers, tools)
    expect(Object.keys(value.model_roles)).toEqual(['prompt', 'agent'])
    expect(value.model_roles.prompt).toEqual(value.model_roles.agent)
    expect(value).not.toHaveProperty('model_name')
    expect(value.iterations).toBe(1)
  })
  it('creates Correctness three-role payloads and respects advanced choices', () => {
    const value = form(); value.mode = 'advanced'; value.test_type = 'Correctness'
    value.judge = { ...value.judge, model_id: 'judge-model', temperature: .4 }
    const payload = buildInput(value, providers, tools)
    expect(payload.model_roles.judge?.model_id).toBe('judge-model')
    expect(payload.model_roles.agent.model_id).not.toBe('judge-model')
  })
  it('switches simple and advanced modes without leaking hidden role values', () => {
    const value = form(); value.prompt.model_id = 'advanced-only'
    expect(buildInput(value, providers, tools).model_roles.prompt.model_id).not.toBe('advanced-only')
    value.mode = 'advanced'
    expect(buildInput(value, providers, tools).model_roles.prompt.model_id).toBe('advanced-only')
  })
  it.each(['tool', 'iterations', 'model', 'temperature', 'timeout', 'retries'])('rejects invalid %s before submitting', field => {
    const value = form()
    if (field === 'tool') value.tool_id = 'unregistered'
    if (field === 'iterations') value.iterations = 1.5
    if (field === 'model') value.shared.model_id = ''
    if (field === 'temperature') value.shared.temperature = NaN
    if (field === 'timeout') value.shared.timeout = 0
    if (field === 'retries') value.shared.max_retries = -1
    expect(() => buildInput(value, providers, tools)).toThrow()
  })
  it('requires custom URL and environment variable NAME and never serializes raw keys', () => {
    const value = form(); value.shared.provider = 'custom'
    expect(() => buildInput(value, providers, tools)).toThrow(/Base URL/)
    value.shared.base_url = 'https://local.example/v1'; value.shared.api_key_env = 'sk-raw-secret'
    expect(() => buildInput(value, providers, tools)).toThrow(/环境变量/)
    value.shared.api_key_env = 'CUSTOM_API_KEY'
    Object.assign(value.shared, { api_key: 'raw-secret', token: 'raw-token' })
    const serialized = JSON.stringify(buildInput(value, providers, tools))
    expect(serialized).toContain('CUSTOM_API_KEY'); expect(serialized).not.toContain('raw-secret'); expect(serialized).not.toContain('raw-token')
    value.shared.base_url = 'https://user:secret@example.com'
    expect(() => buildInput(value, providers, tools)).toThrow()
  })
})

describe('task lifecycle over mocked HTTP', () => {
  it('sends temporary keys only in the request and retains only a task ID and safe summary', async () => {
    const fetcher = vi.fn(async (url: string | URL | Request) => String(url) === '/api/v1/tasks'
      ? response({ task_id: 'secret-task', status: 'running' }, 202)
      : response({ task_id: 'secret-task', status: 'completed' }))
    const writes: string[] = [], storage = { setItem: (key: string, value: string) => { writes.push(key + ':' + value) }, getItem: () => null }
    const draft = { ...defaultForm(), tool_id: 'duckduckgo' }; draft.shared.temporary_key = 'offline-memory-only-key'
    const payload = buildInput(draft, providers, tools)
    const state = createTaskController(createApi('', fetcher), storage)
    await state.start(payload); await flushPromises()
    expect(payload).not.toHaveProperty('temporary_credentials')
    expect(JSON.stringify(state.input.value)).not.toContain('offline-memory-only-key')
    expect(writes).toEqual(['toolfuzz.currentTaskId:secret-task'])
    expect(state.error.value).not.toContain('offline-memory-only-key')
    state.stop()
  })
  it('creates and polls by task_id, fetches completed result by different run_id and stops polling', async () => {
    vi.useFakeTimers()
    let queries = 0
    const fetcher = vi.fn(async (url: string | URL | Request) => {
      const path = String(url)
      if (path === '/api/v1/tasks') return response({ task_id: 'gui-id', status: 'running' }, 202)
      if (path === '/api/v1/tasks/gui-id') return response({ task_id: 'gui-id', run_id: 'core-id', status: ++queries === 1 ? 'running' : 'completed' })
      if (path === '/api/v1/runs/core-id') return response(record)
      throw new Error('Unexpected URL')
    })
    const storage = { setItem: vi.fn(), getItem: vi.fn() }, state = createTaskController(createApi('', fetcher), storage, 50)
    await state.start(buildInput(form(), providers, tools)); await flushPromises()
    expect(state.blocked.value).toBe(true); expect(storage.setItem).toHaveBeenCalledWith('toolfuzz.currentTaskId', 'gui-id')
    await vi.advanceTimersByTimeAsync(50); await flushPromises()
    expect(state.task.value?.status).toBe('completed'); expect(state.result.value?.metadata?.run_id).toBe('core-id')
    const count = fetcher.mock.calls.length; await vi.advanceTimersByTimeAsync(1000); expect(fetcher).toHaveBeenCalledTimes(count)
    state.stop()
  })
  it('keeps cancelling locked until backend confirms cancelled and ignores stale polls', async () => {
    vi.useFakeTimers()
    let finishOld!: (value: Response) => void, cancelled = false
    const fetcher = vi.fn(async (url: string | URL | Request) => {
      const path = String(url)
      if (path.endsWith('/cancel')) { cancelled = true; return response({ task_id: 'gui-id', status: 'cancelling' }, 202) }
      if (path.endsWith('/tasks/gui-id')) {
        if (!cancelled) return new Promise<Response>(resolve => { finishOld = resolve })
        return response({ task_id: 'gui-id', run_id: 'core-id', status: 'cancelling' })
      }
      throw new Error('Unexpected request')
    })
    const state = createTaskController(createApi('', fetcher), undefined, 20)
    state.task.value = { task_id: 'gui-id', status: 'running' }
    const old = state.resume('gui-id'); await flushPromises()
    await state.cancel(); expect(state.task.value?.status).toBe('cancelling'); expect(state.blocked.value).toBe(true)
    finishOld(response({ task_id: 'gui-id', status: 'completed' })); await old; expect(state.task.value?.status).toBe('cancelling')
    expect(await state.start(buildInput(form(), providers, tools))).toBeNull()
    state.stop()
  })
  it('only displays cancelled after confirmation, then allows another task', async () => {
    const fetcher = vi.fn(async (url: string | URL | Request) => String(url).endsWith('/cancel') ? response({ task_id: 'gui-id', status: 'cancelled' }) : response({ task_id: 'gui-id', status: 'cancelled' }))
    const state = createTaskController(createApi('', fetcher)); state.task.value = { task_id: 'gui-id', status: 'running' }
    await state.cancel(); expect(state.task.value?.status).toBe('cancelled'); expect(state.blocked.value).toBe(false); expect(state.result.value).toBeNull(); state.stop()
  })
  it('rechecks completion after a cancel conflict and never overwrites completed', async () => {
    const fetcher = vi.fn(async (url: string | URL | Request) => String(url).endsWith('/cancel') ? response({ error: { code: 'task_not_cancellable' } }, 409) : String(url).includes('/tasks/') ? response({ task_id: 'gui-id', run_id: 'core-id', status: 'completed' }) : response(record))
    const state = createTaskController(createApi('', fetcher)); state.task.value = { task_id: 'gui-id', status: 'running' }
    await state.cancel(); expect(state.task.value?.status).toBe('completed'); expect(state.result.value).toEqual(record); state.stop()
  })
  it('restores only a task ID after refresh and uses no persisted model/secret', async () => {
    const storage = new Map<string, string>(), saved = { setItem: (k: string, v: string) => storage.set(k, v), getItem: (k: string) => storage.get(k) ?? null }
    const fetcher = vi.fn().mockImplementation(async () => response({ task_id: 'gui-id', status: 'failed', error: 'offline model failure' }))
    const first = createTaskController(createApi('', fetcher), saved); await first.resume('gui-id'); first.stop()
    const fresh = createTaskController(createApi('', fetcher), saved); expect(fresh.savedId()).toBe('gui-id'); await fresh.resume(fresh.savedId())
    expect(fresh.task.value?.status).toBe('failed'); expect([...storage.entries()]).toEqual([['toolfuzz.currentTaskId', 'gui-id']]); fresh.stop()
  })
  it('cleans timers and suppresses late old-task responses after leaving the page', async () => {
    vi.useFakeTimers()
    let resolve!: (r: Response) => void
    const state = createTaskController(createApi('', vi.fn(() => new Promise<Response>(done => { resolve = done }))))
    const pending = state.resume('old-task'); state.stop(); state.task.value = { task_id: 'new-task', status: 'completed' }
    resolve(response({ task_id: 'old-task', status: 'failed' })); await pending
    expect(state.task.value.task_id).toBe('new-task'); expect(vi.getTimerCount()).toBe(0)
  })
  it('does not automatically retry a failed creation request', async () => {
    const fetcher = vi.fn().mockResolvedValue(response({ error: { code: 'task_conflict' } }, 409))
    const state = createTaskController(createApi('', fetcher)); await state.start(buildInput(form(), providers, tools))
    expect(fetcher).toHaveBeenCalledTimes(1); expect(state.error.value).toContain('已有任务'); state.stop()
  })
  it('does not cancel a finished task or send duplicate cancel requests', async () => {
    let accept!: (value: Response) => void
    const fetcher = vi.fn(async (url: string | URL | Request) => String(url).endsWith('/cancel')
      ? new Promise<Response>(resolve => { accept = resolve })
      : response({ task_id: 'gui-id', status: 'cancelled' }))
    const state = createTaskController(createApi('', fetcher))
    state.task.value = { task_id: 'gui-id', status: 'completed' }; await state.cancel(); expect(fetcher).not.toHaveBeenCalled()
    state.task.value = { task_id: 'gui-id', status: 'running' }
    const first = state.cancel(); await state.cancel(); expect(fetcher).toHaveBeenCalledTimes(1)
    accept(response({ task_id: 'gui-id', status: 'cancelling' })); await first
    expect(state.task.value?.status).toBe('cancelled'); state.stop()
  })
  it('starts a fresh task after cancellation and preserves the new identity', async () => {
    const fetcher = vi.fn(async (url: string | URL | Request) => String(url) === '/api/v1/tasks'
      ? response({ task_id: 'new-task', status: 'running' })
      : response({ task_id: 'new-task', status: 'completed' }))
    const state = createTaskController(createApi('', fetcher)); state.task.value = { task_id: 'old-task', status: 'cancelled' }
    expect(await state.start(buildInput(form(), providers, tools))).toBe('new-task'); await flushPromises()
    expect(state.task.value?.task_id).toBe('new-task'); expect(state.task.value?.status).toBe('completed'); state.stop()
  })
})

async function routed(component: typeof WorkspaceView | typeof HistoryView | typeof RunView, path = '/') {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component }] })
  await router.push(path); await router.isReady()
  const wrapper = mount(component, { global: { plugins: [router] } }); wrappers.push(wrapper); await flushPromises()
  return { wrapper, router }
}

describe('Vue pages', () => {
  it('uses a password input and clears it on button, provider and credential source changes', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => response({ items: url.endsWith('/providers') ? providers : tools })))
    const { wrapper } = await routed(WorkspaceView)
    const password = () => wrapper.find('input[type="password"]')
    await password().setValue('offline-value')
    await wrapper.findAll('button').find(button => button.text() === '清空密钥')!.trigger('click')
    expect((password().element as HTMLInputElement).value).toBe('')
    await password().setValue('offline-value')
    const provider = wrapper.findComponent(ModelEditor).findAll('select')[0]!
    await provider.setValue('custom')
    expect((password().element as HTMLInputElement).value).toBe('')
    await password().setValue('offline-value')
    const source = wrapper.findComponent(ModelEditor).findAll('select')[1]!
    await source.setValue('environment'); expect(password().exists()).toBe(false)
    await source.setValue('direct'); expect((password().element as HTMLInputElement).value).toBe('')
    expect(JSON.stringify({ ...sessionStorage })).not.toContain('offline-value')
  })
  it('renders API health and navigation in the application shell', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ status: 'ok' })))
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<p>workspace</p>' } }] })
    await router.push('/'); const wrapper = mount(App, { global: { plugins: [router] } }); wrappers.push(wrapper); await flushPromises()
    expect(wrapper.text()).toContain('API 已连接'); expect(wrapper.text()).toContain('历史记录')
  })
  it('loads dynamic tools/providers and switches simple/advanced/Correctness UI', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => response({ items: url.endsWith('/providers') ? providers : tools })))
    const { wrapper } = await routed(WorkspaceView)
    expect(wrapper.find('[aria-label="测试工具"]').text()).toContain('PubMed')
    expect(wrapper.findAllComponents(ModelEditor)).toHaveLength(1)
    await wrapper.find('[aria-label="模型配置模式"]').setValue('advanced')
    expect(wrapper.findAllComponents(ModelEditor)).toHaveLength(2)
    const correctness = wrapper.findAll('button').find(button => button.text() === 'Correctness')!
    await correctness.trigger('click'); expect(wrapper.findAllComponents(ModelEditor)).toHaveLength(3)
    expect(wrapper.text()).toContain('Prompt Set 迭代')
    expect(wrapper.findAll('input[type="password"]')).toHaveLength(3)
  })
  it('shows validation error without creating a task', async () => {
    const fetcher = vi.fn(async (url: string) => response({ items: url.endsWith('/providers') ? providers : tools }))
    vi.stubGlobal('fetch', fetcher); const { wrapper } = await routed(WorkspaceView)
    await wrapper.find('[aria-label="Iterations"]').setValue(0); await wrapper.find('form').trigger('submit')
    expect(wrapper.text()).toContain('正整数'); expect(fetcher.mock.calls.every(call => !call[0].endsWith('/tasks'))).toBe(true)
  })
  it('submits the form, locks configuration and renders confirmed cancellation', async () => {
    let end = false
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/providers')) return response({ items: providers })
      if (url.endsWith('/tools')) return response({ items: tools })
      if (url.endsWith('/cancel')) { end = true; return response({ task_id: 'ui-task', status: 'cancelled' }, 202) }
      if (url.endsWith('/tasks') && init?.method === 'POST') return response({ task_id: 'ui-task', status: 'running' }, 202)
      if (url.endsWith('/tasks/ui-task')) return response({ task_id: 'ui-task', status: end ? 'cancelled' : 'running' })
      throw new Error('Unexpected request')
    })
    vi.stubGlobal('fetch', fetcher); const { wrapper } = await routed(WorkspaceView)
    await wrapper.find('input[type="password"]').setValue('offline-ui-key')
    await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(wrapper.find('fieldset').attributes('disabled')).toBeDefined()
    const cancel = wrapper.findAll('button').find(button => button.text().includes('Cancel Task'))!
    expect(cancel.attributes('disabled')).toBeUndefined(); await cancel.trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('任务已取消。'); expect(cancel.attributes('disabled')).toBeDefined()
    expect(wrapper.find('fieldset').attributes('disabled')).toBeUndefined()
    const body = fetcher.mock.calls.find(call => call[0] === '/api/v1/tasks')?.[1]?.body
    expect(body).toBeTruthy(); expect(JSON.parse(String(body))).toHaveProperty('model_roles.agent')
    expect(JSON.parse(String(body)).temporary_credentials).toEqual({ prompt: 'offline-ui-key', agent: 'offline-ui-key' })
    expect((wrapper.find('input[type="password"]').element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).not.toContain('offline-ui-key')
    expect(JSON.stringify({ ...sessionStorage, ...localStorage })).not.toContain('offline-ui-key')
  })
  it('paginates and filters History with backend run IDs instead of task IDs', async () => {
    const fetcher = vi.fn().mockImplementation(async () => response({ items: [{ run_id: 'core-id', task_id: 'gui-id', tool: 'local_echo', status: 'cancelled' }], total: 21, offset: 0, limit: 10 }))
    vi.stubGlobal('fetch', fetcher); const { wrapper } = await routed(HistoryView)
    expect(wrapper.text()).toContain('local_echo'); expect(wrapper.text()).toContain('任务已取消')
    expect(wrapper.find('a').attributes('href')).toContain('/runs/core-id')
    await wrapper.findAll('button').find(b => b.text() === '下一页')!.trigger('click'); await flushPromises()
    expect(fetcher.mock.calls[fetcher.mock.calls.length - 1]?.[0]).toContain('offset=10')
    await wrapper.find('select').setValue('correctness'); await flushPromises()
    expect(fetcher.mock.calls[fetcher.mock.calls.length - 1]?.[0]).toContain('test_type=correctness'); expect(fetcher.mock.calls[fetcher.mock.calls.length - 1]?.[0]).toContain('offset=0')
  })
  it('opens legacy details via run_id and safely displays traces and local reports', async () => {
    const fetcher = vi.fn().mockResolvedValue(response(record)); vi.stubGlobal('fetch', fetcher)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/runs/:runId', component: RunView }] })
    await router.push('/runs/core-id'); const wrapper = mount(RunView, { global: { plugins: [router] } }); wrappers.push(wrapper); await flushPromises()
    expect(fetcher.mock.calls[0]?.[0]).toBe('/api/v1/runs/core-id')
    expect(wrapper.text()).toContain('local_echo'); expect(wrapper.text()).toContain('gui-id'); expect(wrapper.text()).toContain('agent answer')
    expect(wrapper.findAll('script')).toHaveLength(0)
    expect(wrapper.find('.report-links a').attributes('href')).toBe('/api/v1/runs/core-id/reports/runtime.json')
  })
  it.each(['cancelled', 'failed', 'completed'])('renders incomplete %s results and missing reports without crashing', status => {
    const wrapper = mount(ResultPanel, { props: { result: { status, metadata: { run_id: 'old-id' } } } }); wrappers.push(wrapper)
    expect(wrapper.text()).toContain('报告不可用'); expect(wrapper.text()).toContain('没有可用用例')
  })
  it('uses different metrics for Correctness and handles invalid/empty traces', () => {
    const correctness = { test_type: 'correctness', summary: { prompt_set_count: 1, prompt_count: 3, tool_failure_count: 2 }, results: [{ individual_run_test_results: [{ prompt: 'test', tool_failure: true, trace: 'bad json' }] }] }
    const wrapper = mount(ResultPanel, { props: { result: correctness } }); wrappers.push(wrapper)
    expect(wrapper.text()).toContain('工具失败数'); expect(wrapper.text()).not.toContain('成功触发数')
    expect(metrics(correctness)[1]?.value).toBe(3); expect(trace('bad')).toEqual([]); expect(trace(null)).toEqual([])
  })
})
