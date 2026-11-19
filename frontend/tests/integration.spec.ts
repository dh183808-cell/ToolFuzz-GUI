// @vitest-environment node
// Explicit opt-in: read existing records, never submit a valid model task.
import { describe, expect, it } from 'vitest'
import { createApi, ApiError, errorMessage } from '../src/api/client'
import type { RunSummary, TaskInput } from '../src/types/api'
const client = createApi('http://127.0.0.1:5173')
describe('Vue API Client → Vite proxy → real FastAPI', () => {
  it('loads health, registered providers and tools', async () => {
    expect((await client.health()).status).toBe('ok')
    expect((await client.providers()).items.map(item => item.id)).toEqual(expect.arrayContaining(['deepseek', 'openrouter', 'siliconflow', 'qwen', 'custom']))
    expect((await client.tools()).items.map(item => item.id)).toEqual(expect.arrayContaining(['duckduckgo', 'pubmed']))
  })
  it('reads real History and details with distinct identities', async () => {
    const page = await client.runs({ limit: 100, offset: 0 })
    expect(page.items.length).toBeGreaterThan(0)
    const different = page.items.find(item => item.task_id !== item.run_id)!
    expect(different).toBeTruthy()
    const detail = await client.run(different.run_id)
    expect(detail.metadata?.task_id).toBe(different.task_id)
    expect(detail.metadata?.run_id).toBe(different.run_id)
  })
  it('reads old local_echo, cancelled and both report kinds, without writing data', async () => {
    const page = await client.runs({ limit: 100 })
    expect(page.items.some(item => item.tool === 'local_echo')).toBe(true)
    expect(page.items.some(item => item.status === 'cancelled')).toBe(true)
    let json = 0, html = 0
    for (const entry of page.items) {
      const detail = await client.run(entry.run_id)
      for (const kind of ['json', 'html'] as const) {
        const url = client.reportUrl(detail.artifacts?.[`${kind}_path`])
        if (!url) continue
        const response = await fetch(url)
        expect(response.status).toBe(200)
        if (kind === 'json') { await response.json(); json++ }
        else { expect(await response.text()).toContain('html'); html++ }
      }
    }
    expect(json).toBeGreaterThan(0); expect(html).toBeGreaterThan(0)
  })
  it('passes filtering and paging to the real service', async () => {
    const page = await client.runs({ limit: 1, offset: 1, test_type: 'runtime' })
    expect(page.limit).toBe(1); expect(page.offset).toBe(1)
    expect(page.items.every((item: RunSummary) => item.run_type === 'runtime')).toBe(true)
  })
  it('handles real 404 errors', async () => {
    await expect(client.run('p2_3_definitely_missing')).rejects.toMatchObject({ status: 404 })
  })
  it('handles real 422 without creating a valid task', async () => {
    await expect(client.start({} as TaskInput)).rejects.toMatchObject({ status: 422, code: 'validation_error' })
  })
  it('handles real 409 by cancelling only an already-terminal stored task', async () => {
    const page = await client.runs({ limit: 100 })
    const ended = page.items.find(item => ['completed', 'cancelled', 'failed'].includes(item.status ?? ''))!
    expect(ended?.task_id).toBeTruthy()
    try { await client.cancel(ended.task_id!); throw new Error('Expected conflict') }
    catch (err) { expect(err).toBeInstanceOf(ApiError); expect((err as ApiError).status).toBe(409); expect(errorMessage(err)).toContain('状态已变化') }
  })
})
