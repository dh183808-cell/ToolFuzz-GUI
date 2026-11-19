// @vitest-environment node
import { spawn, type ChildProcess } from 'node:child_process'
import { createServer } from 'node:net'
import { fileURLToPath } from 'node:url'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { createApi, type Api } from '../src/api/client'
import { buildInput, defaultForm } from '../src/composables/config'
import type { Provider, Task, TaskInput, Tool } from '../src/types/api'

let server: ChildProcess, client: Api, payload: TaskInput, diagnostic = ''
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))
async function until(id: string, status: string): Promise<Task> {
  for (let attempt = 0; attempt < 150; attempt++) {
    const task = await client.task(id)
    if (task.status === status) return task
    if (task.status === 'failed') throw new Error('Offline task failed: ' + task.error)
    await delay(50)
  }
  throw new Error('Offline task did not reach ' + status)
}
beforeAll(async () => {
  const reservation = createServer()
  await new Promise<void>(resolve => reservation.listen(0, '127.0.0.1', resolve))
  const port = (reservation.address() as { port: number }).port
  await new Promise<void>(resolve => reservation.close(() => resolve()))
  const root = fileURLToPath(new URL('../../', import.meta.url))
  const env = { ...process.env, PYTHONDONTWRITEBYTECODE: '1', PYTHONPATH: `${root}.venv/api-deps:${root}:${root}ToolFuzz`, WEB_CONCURRENCY: '1' }
  server = spawn(root + 'ToolFuzz/.venv/bin/python', ['-B', root + 'frontend/tests/offline_server.py', String(port)], { env, stdio: ['ignore', 'pipe', 'pipe'] })
  server.stderr?.on('data', data => { diagnostic += data.toString() })
  server.stdout?.on('data', data => { diagnostic += data.toString() })
  client = createApi(`http://127.0.0.1:${port}`)
  let ready = false
  for (let attempt = 0; attempt < 100; attempt++) {
    if (server.exitCode !== null) throw new Error('Offline HTTP server exited: ' + diagnostic)
    try { await client.health(); ready = true; break } catch { await delay(50) }
  }
  expect(ready, diagnostic).toBe(true)
  const providers: Provider[] = (await client.providers()).items, tools: Tool[] = (await client.tools()).items
  const form = { ...defaultForm(), tool_id: tools[0]!.id }
  form.shared.credential_source = 'environment'
  payload = buildInput(form, providers, tools)
}, 15000)
afterAll(async () => {
  if (!server || server.exitCode !== null) return
  server.kill('SIGINT')
  await new Promise<void>(resolve => { server.once('exit', () => resolve()); setTimeout(resolve, 10000).unref() })
  if (server.exitCode === null) { server.kill('SIGKILL'); throw new Error('Offline server did not stop gracefully') }
  expect(server.exitCode, diagnostic).toBe(0)
}, 12000)

describe('API Client with real HTTP / TaskManager / spawn Worker, offline executor', () => {
  it('starts, completes and reads real saved reports in a temporary store', async () => {
    const created = await client.start(payload)
    const completed = await until(created.task_id, 'completed')
    expect(completed.run_id).toBeTruthy()
    const run = await client.run(completed.run_id!)
    expect(run.metadata?.task_id).toBe(created.task_id)
    expect(run.metadata?.run_id).not.toBe(created.task_id)
    for (const type of ['json', 'html'] as const) expect((await fetch(client.reportUrl(run.artifacts?.[`${type}_path`])!)).status).toBe(200)
  })
  it('rejects a second task, cancels a blocked worker and starts again after reaping', async () => {
    const created = await client.start({ ...payload, iterations: 2 })
    await until(created.task_id, 'running')
    for (let attempt = 0; attempt < 100 && !diagnostic.includes('offline-blocking-entered:' + created.task_id); attempt++) await delay(20)
    expect(diagnostic).toContain('offline-blocking-entered:' + created.task_id)
    await expect(client.start(payload)).rejects.toMatchObject({ status: 409 })
    const cancelled = await client.cancel(created.task_id)
    expect(['cancelling', 'cancelled']).toContain(cancelled.status)
    const ended = await until(created.task_id, 'cancelled')
    const record = await client.run(ended.run_id!)
    expect(record.status).toBe('cancelled'); expect(record.results).toEqual([])
    expect(record.artifacts?.html_path).toBeNull()
    const next = await client.start(payload)
    expect(next.task_id).not.toBe(created.task_id)
    await until(next.task_id, 'completed')
    expect((await client.task(created.task_id)).status).toBe('cancelled')
  })
})
