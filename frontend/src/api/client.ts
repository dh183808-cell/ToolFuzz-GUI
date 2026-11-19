import type { Provider, RunPage, RunResult, Task, TaskInput, TaskStatus, Tool } from '@/types/api'

export class ApiError extends Error {
  constructor(public status: number, public code: string) { super(code); this.name = 'ApiError' }
}
const messages: Record<string, string> = {
  network: '无法连接服务，请确认后端已启动。', timeout: '请求超时，请查询任务状态后再操作。',
  aborted: '请求已中止。', invalid_response: '服务响应格式异常。',
  validation_error: '参数无效或不受支持，请检查配置。', task_conflict: '已有任务正在执行或取消，请稍后重试。',
  task_not_cancellable: '任务状态已变化，请刷新状态。', task_not_found: '任务不存在，或记录尚未保存。',
  run_not_found: '运行记录不存在或不可读取。', report_not_found: '报告缺失或不可读取。',
  credential_origin_rejected: '临时密钥仅允许从可信本机页面提交。',
  ambiguous_run_id: '运行 ID 对应多条记录，暂时无法查看。', service_stopping: '服务正在关闭。',
}
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return messages[error.code] ?? ({ 404: '内容不存在。', 409: '操作冲突，请刷新后重试。', 422: messages.validation_error }[error.status] ?? '服务请求失败，请稍后重试。')
  return '操作失败，请稍后重试。'
}

export function createApi(base = '', fetcher: typeof fetch = (...args) => fetch(...args), timeout = 15000) {
  const origin = base ? new URL(base) : null
  if (origin && (!['http:', 'https:'].includes(origin.protocol) || origin.username || origin.password || origin.search || origin.hash || origin.pathname !== '/')) throw new Error('API base must be an HTTP(S) origin')
  const prefix = origin?.origin ?? ''
  async function request<T>(path: string, options: { method?: string; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
    const controller = new AbortController()
    let expired = false
    const abort = () => controller.abort()
    if (options.signal?.aborted) controller.abort()
    options.signal?.addEventListener('abort', abort, { once: true })
    const timer = setTimeout(() => { expired = true; controller.abort() }, timeout)
    try {
      const response = await fetcher(prefix + '/api/v1' + path, {
        method: options.method ?? 'GET', signal: controller.signal,
        headers: options.body === undefined ? { Accept: 'application/json' } : { Accept: 'application/json', 'Content-Type': 'application/json' },
        ...(options.body === undefined ? {} : { body: JSON.stringify(options.body) }),
      })
      const value: unknown = await response.json().catch(() => null)
      if (!response.ok) {
        const code = (value as { error?: { code?: unknown } } | null)?.error?.code
        // Never display a server error payload that may contain submitted data.
        throw new ApiError(response.status, typeof code === 'string' ? code : 'http_error')
      }
      if (value === null || typeof value !== 'object') throw new ApiError(response.status, 'invalid_response')
      return value as T
    } catch (error) {
      if (error instanceof ApiError) throw error
      throw new ApiError(0, expired ? 'timeout' : controller.signal.aborted ? 'aborted' : 'network')
    } finally { clearTimeout(timer); options.signal?.removeEventListener('abort', abort) }
  }
  function reportUrl(raw: unknown): string | null {
    if (typeof raw !== 'string' || !/^\/api\/v1\/runs\/[^/?#]+\/reports\/[^/?#]+$/.test(raw)) return null
    try {
      if (raw.split('/').some(part => /[\\/]/.test(decodeURIComponent(part)) || ['.', '..'].includes(decodeURIComponent(part)))) return null
      return prefix + raw
    } catch { return null }
  }
  return {
    health: (signal?: AbortSignal) => request<{ status: string }>('/health', { signal }),
    providers: (signal?: AbortSignal) => request<{ items: Provider[] }>('/providers', { signal }),
    tools: (signal?: AbortSignal) => request<{ items: Tool[] }>('/tools', { signal }),
    start: (body: TaskInput, signal?: AbortSignal) => request<{ task_id: string; status: TaskStatus }>('/tasks', { method: 'POST', body, signal }),
    task: (id: string, signal?: AbortSignal) => request<Task>('/tasks/' + encodeURIComponent(id), { signal }),
    cancel: (id: string, signal?: AbortSignal) => request<{ task_id: string; status: TaskStatus }>('/tasks/' + encodeURIComponent(id) + '/cancel', { method: 'POST', signal }),
    runs: (params: { limit?: number; offset?: number; test_type?: string } = {}, signal?: AbortSignal) => {
      const query = new URLSearchParams()
      Object.entries(params).forEach(([key, val]) => { if (val !== undefined && val !== '') query.set(key, String(val)) })
      return request<RunPage>('/runs?' + query, { signal })
    },
    run: (id: string, signal?: AbortSignal) => request<RunResult>('/runs/' + encodeURIComponent(id), { signal }), reportUrl,
  }
}
export type Api = ReturnType<typeof createApi>
export const api = createApi(import.meta.env.VITE_API_BASE_URL || '')
