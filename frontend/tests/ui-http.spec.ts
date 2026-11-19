// Real HTTP responses rendered by Vue Test Utils; this is not a browser test.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import App from '../src/App.vue'
import WorkspaceView from '../src/views/WorkspaceView.vue'
import HistoryView from '../src/views/HistoryView.vue'
import RunView from '../src/views/RunView.vue'
import type { RunPage } from '../src/types/api'

const nativeFetch = globalThis.fetch
const base = 'http://127.0.0.1:5173'
const wrappers: VueWrapper[] = []
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); vi.unstubAllGlobals(); sessionStorage.clear() })
async function eventually(check: () => void) {
  for (let attempt = 0; attempt < 100; attempt++) {
    await flushPromises()
    try { check(); return } catch (err) { if (attempt === 99) throw err }
    await new Promise(resolve => setTimeout(resolve, 20))
  }
}
async function application(path: string) {
  // Resolve browser-relative fetch URLs against the actual Vite HTTP server.
  // No responses are intercepted or fabricated.
  vi.stubGlobal('fetch', (input: string | URL | Request, init?: RequestInit) => nativeFetch(typeof input === 'string' ? new URL(input, base) : input, init))
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: WorkspaceView }, { path: '/history', component: HistoryView },
    { path: '/runs/:runId', component: RunView },
  ] })
  await router.push(path); await router.isReady()
  const wrapper = mount(App, { global: { plugins: [router] } }); wrappers.push(wrapper)
  return { wrapper, router }
}
describe('Vue components rendered with real FastAPI responses through Vite', () => {
  it('shows healthy service and live registered configuration choices', async () => {
    const { wrapper } = await application('/')
    await eventually(() => { expect(wrapper.text()).toContain('API 已连接'); expect(wrapper.find('[aria-label="测试工具"]').text()).toContain('PubMed') })
    expect(wrapper.findAll('select').some(select => select.text().includes('SiliconFlow'))).toBe(true)
    expect(wrapper.find('.start-button').attributes('disabled')).toBeUndefined()
  })
  it('opens actual local_echo History details and valid report links', async () => {
    const page = await nativeFetch(base + '/api/v1/runs?limit=100').then(r => r.json()) as RunPage
    const entry = page.items.find(item => item.tool === 'local_echo')!
    expect(entry).toBeTruthy()
    const { wrapper, router } = await application('/history')
    await eventually(() => expect(wrapper.text()).toContain('local_echo'))
    await router.push('/runs/' + encodeURIComponent(entry.run_id))
    await eventually(() => expect(wrapper.find('.results').text()).toContain('local_echo'))
    const links = wrapper.findAll('.report-links a')
    expect(links).toHaveLength(2)
    for (const link of links) {
      const href = link.attributes('href')
      if (!href) throw new Error('Missing report URL')
      const response = await nativeFetch(new URL(href, base))
      expect(response.status).toBe(200)
    }
    expect(wrapper.find('.results').text()).toContain(entry.run_id)
  })
  it('recovers an existing cancelled task after a fresh page session', async () => {
    const page = await nativeFetch(base + '/api/v1/runs?limit=100').then(r => r.json()) as RunPage
    const entry = page.items.find(item => item.status === 'cancelled')!
    expect(entry?.task_id).toBeTruthy()
    sessionStorage.setItem('toolfuzz.currentTaskId', entry.task_id!)
    const { wrapper } = await application('/')
    await eventually(() => expect(wrapper.text()).toContain('任务已取消。'))
    expect(wrapper.find('.danger').attributes('disabled')).toBeDefined()
    await eventually(() => expect(wrapper.find('.start-button').attributes('disabled')).toBeUndefined())
  })
})
